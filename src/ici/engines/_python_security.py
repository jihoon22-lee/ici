"""Bounded Python AST security rules with redaction-safe diagnostics."""

from __future__ import annotations

import ast
import io
import math
import re
import tokenize
from collections import Counter
from dataclasses import dataclass

from ici.core.models import EngineStatus, InspectionTarget
from ici.engines._python_resource_scopes import collect_import_aliases, collect_scope_bindings

_SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:api_?key|access_?key|auth_?token|client_?secret|passw(?:or)?d|passwd|"
    r"private_?key|secret|token)(?:$|_)",
    re.IGNORECASE,
)
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_KNOWN_SECRET_PREFIXES = ("akia", "ghp_", "github_pat_", "sk-", "xoxb-", "xoxp-")
_PLACEHOLDER_MARKERS = (
    "change-me",
    "change_me",
    "changeme",
    "dummy",
    "example",
    "not-a-real",
    "placeholder",
    "replace-me",
    "test-only",
    "your_",
    "xxxx",
)


@dataclass(frozen=True)
class PythonSecurityAnalysis:
    findings: tuple[InspectionTarget, ...]
    checked_calls: int
    secret_literals_checked: int


def _node_range(node: ast.AST) -> tuple[int, int | None, int | None, int | None]:
    start_column = getattr(node, "col_offset", None)
    end_column = getattr(node, "end_col_offset", None)
    return (
        max(1, getattr(node, "lineno", 1)),
        getattr(node, "end_lineno", None),
        start_column + 1 if isinstance(start_column, int) else None,
        end_column if isinstance(end_column, int) and end_column > 0 else None,
    )


def _finding(
    file_path: str,
    node: ast.AST,
    rule: str,
    message: str,
    snippet: str,
    **metrics: int | float | str,
) -> InspectionTarget:
    start_line, end_line, start_column, end_column = _node_range(node)
    return InspectionTarget(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        target_name=f"Security:{rule}",
        status=EngineStatus.WARN,
        message=message,
        snippet=snippet,
        metrics=metrics,
        start_column=start_column,
        end_column=end_column,
    )


def _suppressed_lines(text: str) -> set[int]:
    suppressed: set[int] = set()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and "nosec" in token.string.casefold():
                suppressed.add(token.start[0])
    except (IndentationError, tokenize.TokenError):
        return set()
    return suppressed


def _string_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = float(len(value))
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.casefold().strip()
    return any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


def _is_secret_value(name: str, value: str, allowlist: frozenset[str]) -> bool:
    if name.casefold() in allowlist or len(value) < 6 or _looks_like_placeholder(value):
        return False
    normalized = value.casefold()
    if normalized.startswith(_KNOWN_SECRET_PREFIXES):
        return True
    return bool(_SECRET_NAME_RE.search(name)) and (
        len(value) >= 8 or (len(value) >= 6 and _string_entropy(value) >= 2.5)
    )


def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, (ast.Tuple, ast.List)):
        return [name for item in node.elts for name in _assignment_names(item)]
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return [node.slice.value] if isinstance(node.slice.value, str) else []
    return []


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _ScopeMap(ast.NodeVisitor):
    """Map expression nodes to the lexical scope that resolves their names."""

    def __init__(self, tree: ast.AST) -> None:
        self.current = tree
        self.scope_by_node: dict[int, ast.AST] = {}
        self.parent_scope: dict[int, ast.AST | None] = {id(tree): None}
        self.visit(tree)

    def visit(self, node: ast.AST) -> None:
        self.scope_by_node[id(node)] = self.current
        super().visit(node)

    def _visit_function_signature(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)

    def _visit_child_scope(self, node: ast.AST, body: list[ast.stmt]) -> None:
        parent = self.current
        self.parent_scope[id(node)] = parent
        self.current = node
        for statement in body:
            self.visit(statement)
        self.current = parent

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_signature(node)
        self._visit_child_scope(node, node.body)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_signature(node)
        self._visit_child_scope(node, node.body)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases, *node.keywords]:
            self.visit(expression)
        self._visit_child_scope(node, node.body)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        parent = self.current
        self.parent_scope[id(node)] = parent
        self.current = node
        self.visit(node.body)
        self.current = parent


class _NameResolver:
    def __init__(self, tree: ast.AST) -> None:
        self.scope_map = _ScopeMap(tree)
        self.alias_cache: dict[int, dict[str, str]] = {}
        self.binding_cache: dict[int, frozenset[str]] = {}

    def _aliases_for_scope(self, scope: ast.AST) -> dict[str, str]:
        key = id(scope)
        cached = self.alias_cache.get(key)
        if cached is not None:
            return cached
        parent = self.scope_map.parent_scope.get(key)
        aliases = dict(self._aliases_for_scope(parent)) if parent is not None else {}
        aliases.update(collect_import_aliases(scope))
        self.alias_cache[key] = aliases
        return aliases

    def aliases(self, node: ast.AST) -> dict[str, str]:
        return self._aliases_for_scope(self.scope_map.scope_by_node[id(node)])

    def shadowed(self, node: ast.AST) -> frozenset[str]:
        scope = self.scope_map.scope_by_node[id(node)]
        key = id(scope)
        if key not in self.binding_cache:
            self.binding_cache[key] = collect_scope_bindings(scope)
        return self.binding_cache[key]


def _qualified_name(
    node: ast.AST,
    aliases: dict[str, str],
    shadowed: frozenset[str],
) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id) if node.id not in shadowed else node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value, aliases, shadowed)
        return f"{prefix}.{node.attr}" if prefix else ""
    return ""


def _shell_true(call: ast.Call) -> bool:
    return any(
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in call.keywords
    )


def _risky_call_rule(
    qualified: str,
    call: ast.Call,
    shadowed: frozenset[str],
) -> tuple[str, str, str] | None:
    if qualified in {"hashlib.md5", "Crypto.Hash.MD5.new", "Cryptodome.Hash.MD5.new"}:
        return ("WeakCryptoMD5", "MD5 is not collision resistant", "md5(...)")
    if qualified in {
        "hashlib.sha1",
        "Crypto.Hash.SHA1.new",
        "Cryptodome.Hash.SHA1.new",
    }:
        return ("WeakCryptoSHA1", "SHA-1 is not collision resistant", "sha1(...)")
    if qualified in {
        "random.choice",
        "random.randint",
        "random.random",
        "random.randbytes",
        "random.randrange",
    }:
        return (
            "WeakRandom",
            "The random module is not suitable for security-sensitive randomness",
            f"{qualified}(...)",
        )
    if qualified in {"eval", "exec", "builtins.eval", "builtins.exec"} and not (
        isinstance(call.func, ast.Name) and call.func.id in shadowed
    ):
        return (
            "EvalExec",
            "Dynamic code execution accepts untrusted input",
            f"{qualified}(...)",
        )
    if qualified in {"pickle.load", "pickle.loads", "_pickle.load", "_pickle.loads"}:
        return (
            "PickleLoad",
            "Pickle deserialization can execute attacker-controlled code",
            f"{qualified}(...)",
        )
    if qualified in {"os.system", "os.popen"}:
        return (
            "CommandProcessor",
            "Command text is executed through a system shell",
            f"{qualified}(...)",
        )
    if qualified.startswith("subprocess.") and _shell_true(call):
        return (
            "ShellTrue",
            "subprocess shell=True expands input through a command processor",
            f"{qualified}(..., shell=True)",
        )
    return None


class _SecurityVisitor(ast.NodeVisitor):
    def __init__(
        self,
        file_path: str,
        tree: ast.AST,
        text: str,
        allowlist: frozenset[str],
    ) -> None:
        self.file_path = file_path
        self.resolver = _NameResolver(tree)
        self.suppressed = _suppressed_lines(text)
        self.allowlist = allowlist
        self.findings: list[InspectionTarget] = []
        self.checked_calls = 0
        self.secret_literals_checked = 0

    def _is_suppressed(self, node: ast.AST) -> bool:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        return any(line in self.suppressed for line in range(start, end + 1))

    def _check_secret(self, name: str, value: ast.AST | None, owner: ast.AST) -> None:
        literal = _literal_string(value)
        if literal is None:
            return
        self.secret_literals_checked += 1
        if self._is_suppressed(owner) or name.casefold() in self.allowlist:
            return
        if _PRIVATE_KEY_RE.search(literal):
            self.findings.append(
                _finding(
                    self.file_path,
                    owner,
                    "PrivateKey",
                    "Private-key material is embedded in a source literal",
                    f'{name} = "***REDACTED***"',
                    detector="private-key-marker",
                )
            )
            return
        if _is_secret_value(name, literal, self.allowlist):
            self.findings.append(
                _finding(
                    self.file_path,
                    owner,
                    "HardcodedSecret",
                    f"Secret-like literal assigned to {name}; value REDACTED",
                    f'{name} = "***REDACTED***"',
                    detector="name-context-and-entropy",
                    length=len(literal),
                    entropy=round(_string_entropy(literal), 2),
                )
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            for name in _assignment_names(target):
                self._check_secret(name, node.value, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        for name in _assignment_names(node.target):
            self._check_secret(name, node.value, node)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key, value in zip(node.keys, node.values, strict=True):
            name = _literal_string(key)
            if name is not None:
                self._check_secret(name, value, node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.checked_calls += 1
        if self._is_suppressed(node):
            self.generic_visit(node)
            return
        shadowed = self.resolver.shadowed(node)
        qualified = _qualified_name(node.func, self.resolver.aliases(node), shadowed)
        rule = _risky_call_rule(qualified, node, shadowed)
        if rule is not None:
            self.findings.append(_finding(self.file_path, node, *rule))
        self.generic_visit(node)


def analyze_python_security(
    file_path: str,
    text: str,
    *,
    secret_name_allowlist: frozenset[str] = frozenset(),
) -> PythonSecurityAnalysis:
    """Analyze one already bounded UTF-8 source snapshot."""

    tree = ast.parse(text, filename=file_path)
    visitor = _SecurityVisitor(file_path, tree, text, secret_name_allowlist)
    visitor.visit(tree)
    visitor.findings.sort(
        key=lambda item: (
            item.start_line,
            item.start_column or 0,
            item.target_name,
            item.message,
        )
    )
    return PythonSecurityAnalysis(
        findings=tuple(visitor.findings),
        checked_calls=visitor.checked_calls,
        secret_literals_checked=visitor.secret_literals_checked,
    )
