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


def _imports(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = item.name if item.asname else local
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _shadowed_builtins(tree: ast.AST) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return frozenset(names)


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


class _SecurityVisitor(ast.NodeVisitor):
    def __init__(
        self,
        file_path: str,
        tree: ast.AST,
        text: str,
        allowlist: frozenset[str],
    ) -> None:
        self.file_path = file_path
        self.aliases = _imports(tree)
        self.shadowed = _shadowed_builtins(tree)
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
        qualified = _qualified_name(node.func, self.aliases, self.shadowed)
        rule: tuple[str, str, str] | None = None
        if qualified in {"hashlib.md5", "Crypto.Hash.MD5.new", "Cryptodome.Hash.MD5.new"}:
            rule = ("WeakCryptoMD5", "MD5 is not collision resistant", "md5(...)")
        elif qualified in {
            "hashlib.sha1",
            "Crypto.Hash.SHA1.new",
            "Cryptodome.Hash.SHA1.new",
        }:
            rule = ("WeakCryptoSHA1", "SHA-1 is not collision resistant", "sha1(...)")
        elif qualified in {
            "random.choice",
            "random.randint",
            "random.random",
            "random.randbytes",
            "random.randrange",
        }:
            rule = (
                "WeakRandom",
                "The random module is not suitable for security-sensitive randomness",
                f"{qualified}(...)",
            )
        elif qualified in {"eval", "exec", "builtins.eval", "builtins.exec"} and not (
            isinstance(node.func, ast.Name) and node.func.id in self.shadowed
        ):
            rule = (
                "EvalExec",
                "Dynamic code execution accepts untrusted input",
                f"{qualified}(...)",
            )
        elif qualified in {"pickle.load", "pickle.loads", "_pickle.load", "_pickle.loads"}:
            rule = (
                "PickleLoad",
                "Pickle deserialization can execute attacker-controlled code",
                f"{qualified}(...)",
            )
        elif qualified in {"os.system", "os.popen"}:
            rule = (
                "CommandProcessor",
                "Command text is executed through a system shell",
                f"{qualified}(...)",
            )
        elif qualified.startswith("subprocess.") and _shell_true(node):
            rule = (
                "ShellTrue",
                "subprocess shell=True expands input through a command processor",
                f"{qualified}(..., shell=True)",
            )
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
