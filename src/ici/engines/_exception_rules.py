"""Exception-handling safety rules shared by the stable engine and ici next.

The stable ``exception`` engine and the ``python.exception``/``cpp.exception``
internal checks call the same per-file analyses — ``analyze_python_exceptions``
and ``analyze_cpp_exceptions`` — so the rule logic lives here once (#218).
Each analyzer takes an already-read source snapshot and returns
``InspectionTarget``s with their native rule names and locations intact.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass

from ici.core.models import EngineStatus, InspectionTarget

ScopeAliases = tuple[set[str], set[str], set[str], set[str]]
ScopeEvent = tuple[tuple[int, int, int], str, str, bool]
TransientEvents = list[tuple[str, tuple[int, int, int]]]


class _HandlerRaiseVisitor(ast.NodeVisitor):
    """Find raises in one handler while excluding nested function/class scopes."""

    def __init__(self, handler: ast.ExceptHandler, alias: str):
        self.handler = handler
        self.alias = alias
        self.raises: list[ast.Raise] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        del node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        del node

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        del node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node is not self.handler and node.name == self.alias:
            return
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if isinstance(node.exc, ast.Name) and node.exc.id == self.alias and node.cause is None:
            self.raises.append(node)
        self.generic_visit(node)


class _ScopeAliasCollector(ast.NodeVisitor):
    """Collect lexical bindings without descending into nested scopes."""

    def __init__(self, scope: ast.AST):
        self.scope = scope
        self.events: list[ScopeEvent] = []
        self._sequence = 0
        self._conditional_depth = 0
        self._is_function_scope = isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
        self._global_names: set[str] = set()
        self._nonlocal_names: set[str] = set()

    def _record(self, name: str, kind: str, node: ast.AST) -> None:
        position = (
            getattr(node, "lineno", getattr(self.scope, "lineno", 0)),
            getattr(node, "col_offset", getattr(self.scope, "col_offset", 0)),
            self._sequence,
        )
        self.events.append((position, name, kind, self._conditional_depth > 0))
        self._sequence += 1

    def _visit_conditional_nodes(self, nodes: Iterable[ast.AST]) -> None:
        self._conditional_depth += 1
        for node in nodes:
            self.visit(node)
        self._conditional_depth -= 1

    def _visit_conditional_suite(self, statements: Iterable[ast.AST]) -> None:
        self._visit_conditional_nodes(statements)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        self._visit_conditional_suite(node.body)
        self._visit_conditional_suite(node.orelse)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        self._visit_conditional_nodes([node.target, *node.body, *node.orelse])

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.visit(node.iter)
        self._visit_conditional_nodes([node.target, *node.body, *node.orelse])

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        self._visit_conditional_suite(node.body)
        self._visit_conditional_suite(node.orelse)

    def visit_With(self, node: ast.With) -> None:
        for index, item in enumerate(node.items):
            if index == 0:
                self.visit(item.context_expr)
            else:
                self._visit_conditional_nodes([item.context_expr])
            if item.optional_vars is not None:
                self._visit_conditional_nodes([item.optional_vars])
        self._visit_conditional_suite(node.body)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        for index, item in enumerate(node.items):
            if index == 0:
                self.visit(item.context_expr)
            else:
                self._visit_conditional_nodes([item.context_expr])
            if item.optional_vars is not None:
                self._visit_conditional_nodes([item.optional_vars])
        self._visit_conditional_suite(node.body)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if node.values:
            self.visit(node.values[0])
            self._visit_conditional_nodes(node.values[1:])

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        self._visit_conditional_nodes([node.body, node.orelse])

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        definite_capture = (
            len(node.cases) == 1
            and node.cases[0].guard is None
            and self._is_irrefutable_capture(node.cases[0].pattern)
        )
        for case in node.cases:
            self._conditional_depth += 1
            capture_kind = "match_definite" if definite_capture else "match"
            for name, capture in self._match_captures(case.pattern):
                self._record(name, capture_kind, capture)
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
            self._conditional_depth -= 1

    @staticmethod
    def _is_irrefutable_capture(pattern: ast.pattern) -> bool:
        return isinstance(pattern, ast.MatchAs) and pattern.pattern is None and bool(pattern.name)

    @staticmethod
    def _match_captures(pattern: ast.pattern) -> list[tuple[str, ast.AST]]:
        captures: list[tuple[str, ast.AST]] = []
        for node in ast.walk(pattern):
            if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                captures.append((node.name, node))
            elif isinstance(node, ast.MatchMapping) and node.rest:
                captures.append((node.rest, node))
        return captures

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_conditional_suite(node.body)
        self._visit_conditional_suite(node.handlers)
        self._visit_conditional_suite(node.orelse)
        self._visit_conditional_suite(node.finalbody)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        self._visit_conditional_suite(node.body)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        del node

    def visit_SetComp(self, node: ast.SetComp) -> None:
        del node

    def visit_DictComp(self, node: ast.DictComp) -> None:
        del node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        del node

    def _visit_arguments(self, arguments: ast.arguments) -> None:
        argument_nodes = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
        for argument in argument_nodes:
            self._record(argument.arg, "shadow", argument)
        if arguments.vararg is not None:
            self._record(arguments.vararg.arg, "shadow", arguments.vararg)
        if arguments.kwarg is not None:
            self._record(arguments.kwarg.arg, "shadow", arguments.kwarg)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node is self.scope:
            self._visit_arguments(node.args)
            self.generic_visit(node)
        else:
            self._record(node.name, "shadow", node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node is self.scope:
            self.generic_visit(node)
        else:
            self._record(node.name, "shadow", node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node

    def visit_arg(self, node: ast.arg) -> None:
        del node

    def visit_Global(self, node: ast.Global) -> None:
        self._global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._nonlocal_names.update(node.names)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Del):
            self._record(node.id, "delete", node)
        elif isinstance(node.ctx, ast.Store):
            self._record(node.id, "shadow", node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".")[0]
            kind = "builtins" if alias.name == "builtins" else "shadow"
            self._record(local_name, kind, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            kind = "shadow"
            if node.module == "builtins" and alias.name == "BaseException":
                kind = "exception"
            self._record(local_name, kind, node)

    def resolve(
        self,
        handler: ast.ExceptHandler | None,
        cutoff: tuple[int, int] | None = None,
        possible_after: bool = False,
    ) -> ScopeAliases:
        """Return effective bindings and all lexical names bound in this scope.

        A handler cutoff models the current scope's execution point; ``None``
        resolves a complete enclosing scope.  For a child function, the
        ``possible_after`` policy keeps an alias possible when it exists at the
        child definition or appears later in the enclosing scope.  Branches
        remain intentionally path-insensitive.
        """
        if cutoff is None:
            cutoff = self._handler_position(handler)
        bindings = self._bindings_before(cutoff)
        if possible_after and cutoff is not None:
            alias_sets = self._possible_aliases(bindings, cutoff, possible_after=True)
        elif cutoff is not None:
            alias_sets = self._possible_aliases(bindings, cutoff, possible_after=False)
        else:
            alias_sets = self._effective_aliases(bindings)
        bound_names = self._bound_names(handler, cutoff)
        return (
            *alias_sets,
            bound_names,
        )

    def _bindings_before(self, cutoff: tuple[int, int] | None) -> dict[str, set[str]]:
        bindings: dict[str, set[str]] = {}
        for position, name, kind, conditional in sorted(self.events):
            if cutoff is not None and position[:2] >= cutoff:
                continue
            if conditional:
                bindings.setdefault(name, {"unbound"}).add(kind)
            else:
                bindings[name] = {kind}
        return bindings

    @staticmethod
    def _effective_aliases(
        bindings: dict[str, set[str]],
    ) -> tuple[set[str], set[str], set[str]]:
        return (
            {name for name, kinds in bindings.items() if "exception" in kinds},
            {name for name, kinds in bindings.items() if "builtins" in kinds},
            {
                name
                for name, kinds in bindings.items()
                if (
                    (
                        "shadow" in kinds
                        and "unbound" not in kinds
                        and "exception" not in kinds
                        and "builtins" not in kinds
                        and not ("delete" in kinds and name == "BaseException")
                    )
                    or ("match_definite" in kinds)
                    or ("delete" in kinds and name != "BaseException")
                )
            },
        )

    def _possible_aliases(
        self,
        bindings: dict[str, set[str]],
        cutoff: tuple[int, int],
        possible_after: bool,
    ) -> tuple[set[str], set[str], set[str]]:
        exception_aliases, builtins_aliases, _ = self._effective_aliases(bindings)
        if possible_after:
            for position, name, kind, _ in self.events:
                if position[:2] < cutoff:
                    continue
                if kind == "exception":
                    exception_aliases.add(name)
                elif kind == "builtins":
                    builtins_aliases.add(name)
        shadowed_names = {
            name
            for name, kinds in bindings.items()
            if name not in exception_aliases
            and name not in builtins_aliases
            and (
                (
                    "shadow" in kinds
                    and "unbound" not in kinds
                    and not ("delete" in kinds and name == "BaseException")
                )
                or "match_definite" in kinds
                or ("delete" in kinds and name != "BaseException")
            )
        }
        return exception_aliases, builtins_aliases, shadowed_names

    def _bound_names(
        self,
        handler: ast.ExceptHandler | None,
        cutoff: tuple[int, int] | None,
    ) -> set[str]:
        if handler is None or not self._is_function_scope:
            return set()
        handler_name = handler.name
        return {
            name
            for position, name, _, _ in self.events
            if name not in self._global_names
            and name not in self._nonlocal_names
            and not (handler_name is not None and position[:2] == cutoff and name == handler_name)
        }

    @staticmethod
    def _handler_position(handler: ast.ExceptHandler | None) -> tuple[int, int] | None:
        if handler is None:
            return None
        return handler.lineno, handler.col_offset


def _is_swallowed(node: ast.ExceptHandler) -> bool:
    if len(node.body) != 1:
        return False
    statement = node.body[0]
    return isinstance(statement, ast.Pass) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    )


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _enclosing_scope_aliases(
    node: ast.ExceptHandler,
    parent_map: dict[ast.AST, ast.AST],
    tree: ast.Module,
) -> list[ScopeAliases]:
    scopes: list[ast.AST] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(
            current,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            scopes.append(current)
        current = parent_map.get(current)
    if tree not in scopes:
        scopes.append(tree)
    scopes.reverse()

    active_scopes: list[ast.AST] = []
    function_after_class = False
    for scope in reversed(scopes):
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_after_class = True
        if isinstance(scope, ast.ClassDef) and function_after_class:
            continue
        active_scopes.append(scope)
    active_scopes.reverse()
    transient = _transient_handler_bindings(node, parent_map, active_scopes)
    aliases: list[ScopeAliases] = []
    for scope_index, scope in enumerate(active_scopes):
        is_current_scope = scope_index == len(active_scopes) - 1
        transient_events = transient.get(scope, [])
        if is_current_scope:
            aliases.append(_scope_aliases(scope, node, transient_events=transient_events))
            continue
        child_scope = active_scopes[scope_index + 1]
        aliases.append(
            _scope_aliases(
                scope,
                None,
                _child_scope_cutoff(child_scope),
                possible_after=isinstance(child_scope, (ast.FunctionDef, ast.AsyncFunctionDef)),
                transient_events=transient_events,
            )
        )
    return aliases


def _transient_handler_bindings(
    node: ast.ExceptHandler,
    parent_map: dict[ast.AST, ast.AST],
    active_scopes: list[ast.AST],
) -> dict[ast.AST, TransientEvents]:
    scope_indexes = {scope: index for index, scope in enumerate(active_scopes)}
    bindings: dict[ast.AST, TransientEvents] = {}
    current = parent_map.get(node)
    while current is not None:
        if isinstance(current, ast.ExceptHandler) and current.name:
            owner = parent_map.get(current)
            while owner is not None and not isinstance(
                owner,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                owner = parent_map.get(owner)
            if owner in scope_indexes:
                position = (current.lineno, current.col_offset, -1)
                bindings.setdefault(owner, []).append((current.name, position))
        current = parent_map.get(current)
    return bindings


def _child_scope_cutoff(scope: ast.AST) -> tuple[int, int] | None:
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return scope.lineno, scope.col_offset
    return None


def _scope_aliases(
    scope: ast.AST,
    handler: ast.ExceptHandler | None,
    cutoff: tuple[int, int] | None = None,
    possible_after: bool = False,
    transient_events: TransientEvents | None = None,
) -> ScopeAliases:
    collector = _ScopeAliasCollector(scope)
    collector.visit(scope)
    for name, position in transient_events or ():
        collector.events.append((position, name, "shadow", False))
    return collector.resolve(handler, cutoff, possible_after)


def _is_base_exception_type(
    node: ast.expr,
    scope_aliases: list[ScopeAliases],
) -> bool:
    if isinstance(node, ast.Name):
        if node.id == "BaseException":
            return _resolves_direct_base_exception(scope_aliases)
        return _resolves_alias(node.id, scope_aliases, alias_kind="exception")
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "BaseException"
            and isinstance(node.value, ast.Name)
            and _resolves_alias(node.value.id, scope_aliases, alias_kind="builtins")
        )
    if isinstance(node, (ast.Tuple, ast.List)):
        return any(_is_base_exception_type(item, scope_aliases) for item in node.elts)
    return False


def _resolves_direct_base_exception(
    scope_aliases: list[ScopeAliases],
) -> bool:
    for exception_aliases, _, shadowed, bound_names in reversed(scope_aliases):
        if "BaseException" in exception_aliases:
            return True
        if "BaseException" in shadowed or "BaseException" in bound_names:
            return False
    return True


def _resolves_alias(
    name: str,
    scope_aliases: list[ScopeAliases],
    *,
    alias_kind: str,
) -> bool:
    for exception_aliases, builtins_aliases, shadowed, bound_names in reversed(scope_aliases):
        aliases = exception_aliases if alias_kind == "exception" else builtins_aliases
        if name in aliases:
            return True
        if name in shadowed or name in bound_names:
            return False
    return False


def _mask_cpp_literals(content: str) -> str:
    chars = list(content)
    index = 0
    state = "code"
    while index < len(chars):
        index, state = _mask_cpp_step(content, chars, index, state)
    return "".join(chars)


def _mask_cpp_step(content: str, chars: list[str], index: int, state: str) -> tuple[int, str]:
    if state == "code":
        return _mask_cpp_code_step(content, chars, index)
    if state == "line":
        return _mask_cpp_line_step(chars, index)
    if state == "line_splice":
        return _mask_cpp_line_splice_step(chars, index)
    if state == "block":
        return _mask_cpp_block_step(chars, index)
    return _mask_cpp_quote_step(chars, index, state)


def _mask_cpp_code_step(content: str, chars: list[str], index: int) -> tuple[int, str]:
    following = chars[index + 1] if index + 1 < len(chars) else ""
    raw_end = _cpp_raw_string_end(content, index)
    if raw_end is not None:
        _blank_cpp_span(chars, index, raw_end)
        return raw_end, "code"
    if following == "/" and chars[index] == "/":
        chars[index] = chars[index + 1] = " "
        return index + 2, "line"
    if following == "*" and chars[index] == "/":
        chars[index] = chars[index + 1] = " "
        return index + 2, "block"
    quote = chars[index]
    if quote in {'"', "'"}:
        chars[index] = " "
        return index + 1, quote
    if chars[index] == "\\":
        return _blank_cpp_escape(chars, index), "code"
    return index + 1, "code"


def _mask_cpp_line_step(chars: list[str], index: int) -> tuple[int, str]:
    if chars[index] == "\\" and index + 1 < len(chars):
        following = chars[index + 1]
        if following == "\n" or (
            following == "\r" and index + 2 < len(chars) and chars[index + 2] == "\n"
        ):
            chars[index] = " "
            return index + 1, "line_splice"
    if chars[index] == "\n":
        return index + 1, "code"
    chars[index] = " "
    return index + 1, "line"


def _mask_cpp_line_splice_step(chars: list[str], index: int) -> tuple[int, str]:
    if chars[index] == "\r" and index + 1 < len(chars) and chars[index + 1] == "\n":
        return index + 2, "line"
    if chars[index] == "\n":
        return index + 1, "line"
    return _mask_cpp_line_step(chars, index)


def _mask_cpp_block_step(chars: list[str], index: int) -> tuple[int, str]:
    following = chars[index + 1] if index + 1 < len(chars) else ""
    if chars[index] == "*" and following == "/":
        chars[index] = chars[index + 1] = " "
        return index + 2, "code"
    if chars[index] != "\n":
        chars[index] = " "
    return index + 1, "block"


def _mask_cpp_quote_step(chars: list[str], index: int, state: str) -> tuple[int, str]:
    current = chars[index]
    if current == "\\":
        return _blank_cpp_escape(chars, index), state
    if current == state:
        return index + 1, "code"
    if current != "\n":
        chars[index] = " "
    return index + 1, state


def _blank_cpp_escape(chars: list[str], index: int) -> int:
    chars[index] = " "
    if index + 1 < len(chars) and chars[index + 1] != "\n":
        chars[index + 1] = " "
        return index + 2
    return index + 1


def _cpp_raw_string_end(content: str, index: int) -> int | None:
    if content[index : index + 2] != 'R"':
        return None
    open_paren = content.find("(", index + 2)
    if open_paren == -1:
        return None
    delimiter = content[index + 2 : open_paren]
    if len(delimiter) > 16 or any(char.isspace() or char in {"\\", "(", ")"} for char in delimiter):
        return None
    close_marker = ")" + delimiter + '"'
    close_start = content.find(close_marker, open_paren + 1)
    return len(content) if close_start == -1 else close_start + len(close_marker)


def _blank_cpp_span(chars: list[str], start: int, end: int) -> None:
    for position in range(start, end):
        if chars[position] != "\n":
            chars[position] = " "


def _destructor_throw_lines(masked: str) -> list[int]:
    lines = masked.splitlines()
    depth = 0
    active: list[int] = []
    found: list[int] = []
    destructor_re = re.compile(r"~\s*[A-Za-z_]\w*\s*\([^)]*\)[^{;]*\{")
    declaration_re = re.compile(r"~\s*[A-Za-z_]\w*\s*\([^)]*\)[^;{]*$")
    pending = False
    for line_no, line in enumerate(lines, 1):
        if destructor_re.search(line):
            active.append(depth)
            pending = False
        elif pending and ";" in line:
            pending = False
        elif pending and "{" in line:
            active.append(depth)
            pending = False
        elif declaration_re.search(line.strip()):
            pending = True
        if active and re.search(r"\bthrow\b", line):
            found.append(line_no)
        depth += line.count("{") - line.count("}")
        active = [start_depth for start_depth in active if depth > start_depth]
    return found


def _empty_catch_all_lines(masked: str) -> list[int]:
    pattern = re.compile(r"catch\s*\(\s*\.\.\.\s*\)\s*\{(?P<body>.*?)\}", re.DOTALL)
    return [
        masked[: match.start()].count("\n") + 1
        for match in pattern.finditer(masked)
        if not match.group("body").strip()
    ]


@dataclass(frozen=True)
class PythonExceptionAnalysis:
    """One Python file's exception-safety targets."""

    targets: tuple[InspectionTarget, ...]
    has_error: bool
    has_warning: bool
    error_name: str = ""
    error_message: str = ""
    error_line: int = 1


@dataclass(frozen=True)
class CppExceptionAnalysis:
    """One C++ file's exception-safety targets."""

    targets: tuple[InspectionTarget, ...]
    has_error: bool
    error_message: str = ""


def analyze_python_exceptions(file_path: str, text: str) -> PythonExceptionAnalysis:
    """Inspect one already-read Python source for handler anti-patterns."""
    try:
        tree = ast.parse(text, filename=file_path)
    except SyntaxError as err:
        return PythonExceptionAnalysis(
            targets=(),
            has_error=False,
            has_warning=False,
            error_name="SyntaxError",
            error_message=f"SyntaxError: {err.msg}",
            error_line=err.lineno or 1,
        )
    targets: list[InspectionTarget] = []
    has_error = False
    has_warning = False
    parent_map = _parent_map(tree)
    target_start = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is not None and _is_base_exception_type(
            node.type,
            _enclosing_scope_aliases(node, parent_map, tree),
        ):
            has_error = True
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=getattr(node.type, "lineno", node.lineno),
                    end_line=getattr(node.type, "end_lineno", node.lineno),
                    start_column=getattr(node.type, "col_offset", 0) + 1,
                    end_column=getattr(node.type, "end_col_offset", None),
                    target_name="BaseException",
                    status=EngineStatus.FAIL,
                    message=(
                        "Catching BaseException also intercepts system-exit and keyboard "
                        "interrupt signals; catch a narrower exception type"
                    ),
                )
            )
        if node.type is None:
            has_error = True
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    start_column=getattr(node, "col_offset", 0) + 1,
                    end_column=getattr(node, "end_col_offset", None),
                    target_name="BareExcept",
                    status=EngineStatus.FAIL,
                    message="Dangerous bare 'except:' clause catches all exceptions",
                )
            )
        if _is_swallowed(node):
            has_error = True
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    start_column=getattr(node, "col_offset", 0) + 1,
                    end_column=getattr(node, "end_col_offset", None),
                    target_name="ErrorSwallowing",
                    status=EngineStatus.FAIL,
                    message="Silent exception swallowing: handler only contains pass",
                )
            )
        if isinstance(node.name, str):
            visitor = _HandlerRaiseVisitor(node, node.name)
            for statement in node.body:
                visitor.visit(statement)
            for raise_node in visitor.raises:
                has_warning = True
                targets.append(
                    InspectionTarget(
                        file_path=file_path,
                        start_line=raise_node.lineno,
                        end_line=getattr(raise_node, "end_lineno", raise_node.lineno),
                        start_column=getattr(raise_node, "col_offset", 0) + 1,
                        end_column=getattr(raise_node, "end_col_offset", None),
                        target_name="LostTraceback",
                        status=EngineStatus.WARN,
                        message=(
                            f"raise {node.name} resets the traceback; use bare 'raise' "
                            "to re-raise the caught exception"
                        ),
                    )
                )
    targets.append(
        InspectionTarget(
            file_path=file_path,
            start_line=1,
            target_name="PythonExceptionSafety",
            status=EngineStatus.PASS,
            message=(
                "Python exception handlers were inspected"
                if len(targets) == target_start
                else "Python exception handlers were inspected; see findings above"
            ),
        )
    )
    return PythonExceptionAnalysis(
        targets=tuple(targets), has_error=has_error, has_warning=has_warning
    )


def analyze_cpp_exceptions(file_path: str, text: str) -> CppExceptionAnalysis:
    """Inspect one already-read C++ source for throwing dtors and catch-all."""
    masked = _mask_cpp_literals(text)
    destructor_lines = _destructor_throw_lines(masked)
    empty_catch_lines = _empty_catch_all_lines(masked)
    targets: list[InspectionTarget] = []
    for line_no in destructor_lines:
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=line_no,
                target_name="DestructorThrow",
                status=EngineStatus.FAIL,
                message="C++ destructor throws an exception and may call std::terminate",
            )
        )
    for line_no in empty_catch_lines:
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=line_no,
                target_name="CatchAllSwallowed",
                status=EngineStatus.FAIL,
                message="Silent catch(...) block without logging or re-throw",
            )
        )
    targets.append(
        InspectionTarget(
            file_path=file_path,
            start_line=1,
            target_name="CppExceptionSafety",
            status=EngineStatus.PASS,
            message=(
                "C++ exception constructs were inspected"
                if not destructor_lines and not empty_catch_lines
                else "C++ exception constructs were inspected; see findings above"
            ),
        )
    )
    return CppExceptionAnalysis(
        targets=tuple(targets), has_error=bool(destructor_lines or empty_catch_lines)
    )
