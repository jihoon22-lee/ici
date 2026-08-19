"""9. Exception handling safety and anti-pattern detection engine."""

import ast
import re
import time

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import detect_project_type, get_all_cpp_sources, get_all_python_sources
from ici.engines.base import BaseEngine

ScopeAliases = tuple[set[str], set[str], set[str], set[str]]


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
        self.events: list[tuple[tuple[int, int, int], str, str]] = []
        self._sequence = 0
        self._is_function_scope = isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef))
        self._global_names: set[str] = set()
        self._nonlocal_names: set[str] = set()

    def _record(self, name: str, kind: str, node: ast.AST) -> None:
        position = (
            getattr(node, "lineno", getattr(self.scope, "lineno", 0)),
            getattr(node, "col_offset", getattr(self.scope, "col_offset", 0)),
            self._sequence,
        )
        self.events.append((position, name, kind))
        self._sequence += 1

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
        if isinstance(node.ctx, (ast.Store, ast.Del)):
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

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._record(node.name, "shadow", node)
        self.generic_visit(node)

    def resolve(
        self,
        handler: ast.ExceptHandler | None,
        cutoff: tuple[int, int] | None = None,
    ) -> ScopeAliases:
        """Return effective bindings and all lexical names bound in this scope.

        A handler cutoff models the current scope's execution point; ``None``
        resolves a complete enclosing scope.  Branches remain intentionally
        path-insensitive: lexical binding events are applied in source order,
        with the last event before the cutoff winning.
        """
        if cutoff is None:
            cutoff = self._handler_position(handler)
        bindings: dict[str, str] = {}
        bound_names = self._bound_names(handler, cutoff)
        for position, name, kind in sorted(self.events):
            if cutoff is None or position[:2] < cutoff:
                bindings[name] = kind
        return (
            {name for name, kind in bindings.items() if kind == "exception"},
            {name for name, kind in bindings.items() if kind == "builtins"},
            {name for name, kind in bindings.items() if kind == "shadow"},
            bound_names,
        )

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
            for position, name, _ in self.events
            if name not in self._global_names
            and name not in self._nonlocal_names
            and not (
                handler_name is not None
                and position[:2] == cutoff
                and name == handler_name
            )
        }

    @staticmethod
    def _handler_position(handler: ast.ExceptHandler | None) -> tuple[int, int] | None:
        if handler is None:
            return None
        return handler.lineno, handler.col_offset


class ExceptionSafetyEngine(BaseEngine):
    """Detect swallowed errors, lost Python tracebacks, and unsafe C++ throws."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._analysis_errors: list[str] = []

    def run(self) -> EngineResult:
        t0 = time.time()
        self._analysis_errors = []
        targets: list[InspectionTarget] = []
        py_sources = get_all_python_sources(self.project_root, self.config)
        cpp_sources = get_all_cpp_sources(self.project_root, self.config)
        proj_type = detect_project_type(self.project_root)
        has_python_scope = bool(py_sources) or proj_type in ("python", "hybrid")
        has_cpp_scope = bool(cpp_sources) or proj_type in ("cpp", "hybrid")
        has_error = False
        has_warning = False
        if has_python_scope and py_sources:
            py_error, py_warning = self._check_python_exceptions(targets)
            has_error = has_error or py_error
            has_warning = has_warning or py_warning
        if has_cpp_scope and cpp_sources:
            cpp_error = self._check_cpp_exceptions(targets)
            has_error = has_error or cpp_error
        if not py_sources and not cpp_sources:
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="ExceptionSafety",
                    status=EngineStatus.SKIP,
                    message="No applicable Python or C++ source files were selected; analysis was not run",
                )
            )

        cfg = self.get_config("exception")
        duration = time.time() - t0
        fail_count = sum(1 for target in targets if target.status == EngineStatus.FAIL)
        warn_count = sum(1 for target in targets if target.status == EngineStatus.WARN)
        if self._analysis_errors:
            overall_status = EngineStatus.ERROR
            evidence = EvidenceState.NOT_RUN
            summary = "; ".join(self._analysis_errors[:3])
        elif not py_sources and not cpp_sources:
            overall_status = EngineStatus.SKIP
            evidence = EvidenceState.ESTIMATED
            summary = "Exception safety analysis skipped: no applicable source files"
        else:
            overall_status = self.evaluate_status(
                has_error, has_warning, cfg.get("mode", "pass_fail")
            )
            evidence = EvidenceState.MEASURED
            summary = (
                "Exception Handling Safety Clean"
                if overall_status == EngineStatus.PASS
                else f"{fail_count} Critical Exception Violations, {warn_count} Warnings"
            )
        return self.create_result(
            name="exception",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "violations_count": fail_count + warn_count,
                "metrics_summary": f"{fail_count} exc errors",
            },
            required=bool(cfg.get("required", True)),
            evidence=evidence,
        )

    def _check_python_exceptions(self, targets: list[InspectionTarget]) -> tuple[bool, bool]:
        has_error = False
        has_warning = False
        for py_file in get_all_python_sources(self.project_root, self.config):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError as err:
                self._append_analysis_error(
                    targets,
                    py_file,
                    "SyntaxError",
                    f"SyntaxError: {err.msg}",
                    err.lineno or 1,
                )
                continue
            except (OSError, UnicodeDecodeError) as err:
                self._append_analysis_error(
                    targets, py_file, "ReadError", f"Could not read Python source: {err}", 1
                )
                continue
            rel_path = str(py_file.relative_to(self.project_root))
            parent_map = self._parent_map(tree)
            target_start = len(targets)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                if node.type is not None and self._is_base_exception_type(
                    node.type,
                    self._enclosing_scope_aliases(node, parent_map, tree),
                ):
                    has_error = True
                    targets.append(
                        InspectionTarget(
                            file_path=rel_path,
                            start_line=node.lineno,
                            end_line=getattr(node, "end_lineno", node.lineno),
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
                            file_path=rel_path,
                            start_line=node.lineno,
                            target_name="BareExcept",
                            status=EngineStatus.FAIL,
                            message="Dangerous bare 'except:' clause catches all exceptions",
                        )
                    )
                if self._is_swallowed(node):
                    has_error = True
                    targets.append(
                        InspectionTarget(
                            file_path=rel_path,
                            start_line=node.lineno,
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
                                file_path=rel_path,
                                start_line=raise_node.lineno,
                                end_line=getattr(raise_node, "end_lineno", raise_node.lineno),
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
                    file_path=rel_path,
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
        return has_error, has_warning

    @staticmethod
    def _is_swallowed(node: ast.ExceptHandler) -> bool:
        if len(node.body) != 1:
            return False
        statement = node.body[0]
        return isinstance(statement, ast.Pass) or (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis
        )

    @staticmethod
    def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
        return {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }

    @classmethod
    def _enclosing_scope_aliases(
        cls,
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
        aliases: list[ScopeAliases] = []
        for scope_index, scope in enumerate(active_scopes):
            is_current_scope = scope_index == len(active_scopes) - 1
            if is_current_scope:
                aliases.append(cls._scope_aliases(scope, node))
                continue
            child_scope = active_scopes[scope_index + 1]
            aliases.append(cls._scope_aliases(scope, None, cls._child_scope_cutoff(child_scope)))
        return aliases

    @staticmethod
    def _child_scope_cutoff(scope: ast.AST) -> tuple[int, int] | None:
        if isinstance(scope, ast.ClassDef):
            return scope.lineno, scope.col_offset
        return None

    @staticmethod
    def _scope_aliases(
        scope: ast.AST,
        handler: ast.ExceptHandler | None,
        cutoff: tuple[int, int] | None = None,
    ) -> ScopeAliases:
        collector = _ScopeAliasCollector(scope)
        collector.visit(scope)
        return collector.resolve(handler, cutoff)

    @staticmethod
    def _is_base_exception_type(
        node: ast.expr,
        scope_aliases: list[ScopeAliases],
    ) -> bool:
        if isinstance(node, ast.Name):
            if node.id == "BaseException":
                return ExceptionSafetyEngine._resolves_direct_base_exception(scope_aliases)
            return ExceptionSafetyEngine._resolves_alias(
                node.id, scope_aliases, alias_kind="exception"
            )
        if isinstance(node, ast.Attribute):
            return (
                node.attr == "BaseException"
                and isinstance(node.value, ast.Name)
                and ExceptionSafetyEngine._resolves_alias(
                    node.value.id, scope_aliases, alias_kind="builtins"
                )
            )
        if isinstance(node, (ast.Tuple, ast.List)):
            return any(
                ExceptionSafetyEngine._is_base_exception_type(item, scope_aliases)
                for item in node.elts
            )
        return False

    @staticmethod
    def _resolves_direct_base_exception(
        scope_aliases: list[ScopeAliases],
    ) -> bool:
        for exception_aliases, _, shadowed, bound_names in reversed(scope_aliases):
            if "BaseException" in exception_aliases:
                return True
            if "BaseException" in shadowed or "BaseException" in bound_names:
                return False
        return True

    @staticmethod
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
        return alias_kind == "builtins" and name == "builtins"

    def _check_cpp_exceptions(self, targets: list[InspectionTarget]) -> bool:
        has_error = False
        for cpp_file in get_all_cpp_sources(self.project_root, self.config):
            try:
                content = cpp_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as err:
                self._append_analysis_error(
                    targets, cpp_file, "ReadError", f"Could not read C++ source: {err}", 1
                )
                continue
            rel_path = str(cpp_file.relative_to(self.project_root))
            masked = self._mask_cpp_literals(content)
            destructor_lines = self._destructor_throw_lines(masked)
            empty_catch_lines = self._empty_catch_all_lines(masked)
            for line_no in destructor_lines:
                has_error = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_path,
                        start_line=line_no,
                        target_name="DestructorThrow",
                        status=EngineStatus.FAIL,
                        message="C++ destructor throws an exception and may call std::terminate",
                    )
                )
            for line_no in empty_catch_lines:
                has_error = True
                targets.append(
                    InspectionTarget(
                        file_path=rel_path,
                        start_line=line_no,
                        target_name="CatchAllSwallowed",
                        status=EngineStatus.FAIL,
                        message="Silent catch(...) block without logging or re-throw",
                    )
                )
            targets.append(
                InspectionTarget(
                    file_path=rel_path,
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
        return has_error

    @staticmethod
    def _mask_cpp_literals(content: str) -> str:
        chars = list(content)
        index = 0
        state = "code"
        while index < len(chars):
            index, state = ExceptionSafetyEngine._mask_cpp_step(content, chars, index, state)
        return "".join(chars)

    @staticmethod
    def _mask_cpp_step(content: str, chars: list[str], index: int, state: str) -> tuple[int, str]:
        if state == "code":
            return ExceptionSafetyEngine._mask_cpp_code_step(content, chars, index)
        if state == "line":
            return ExceptionSafetyEngine._mask_cpp_line_step(chars, index)
        if state == "block":
            return ExceptionSafetyEngine._mask_cpp_block_step(chars, index)
        return ExceptionSafetyEngine._mask_cpp_quote_step(chars, index, state)

    @staticmethod
    def _mask_cpp_code_step(content: str, chars: list[str], index: int) -> tuple[int, str]:
        following = chars[index + 1] if index + 1 < len(chars) else ""
        raw_end = ExceptionSafetyEngine._cpp_raw_string_end(content, index)
        if raw_end is not None:
            ExceptionSafetyEngine._blank_cpp_span(chars, index, raw_end)
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
            return ExceptionSafetyEngine._blank_cpp_escape(chars, index), "code"
        return index + 1, "code"

    @staticmethod
    def _mask_cpp_line_step(chars: list[str], index: int) -> tuple[int, str]:
        if chars[index] == "\n":
            return index + 1, "code"
        chars[index] = " "
        return index + 1, "line"

    @staticmethod
    def _mask_cpp_block_step(chars: list[str], index: int) -> tuple[int, str]:
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if chars[index] == "*" and following == "/":
            chars[index] = chars[index + 1] = " "
            return index + 2, "code"
        if chars[index] != "\n":
            chars[index] = " "
        return index + 1, "block"

    @staticmethod
    def _mask_cpp_quote_step(chars: list[str], index: int, state: str) -> tuple[int, str]:
        current = chars[index]
        if current == "\\":
            return ExceptionSafetyEngine._blank_cpp_escape(chars, index), state
        if current == state:
            return index + 1, "code"
        if current != "\n":
            chars[index] = " "
        return index + 1, state

    @staticmethod
    def _blank_cpp_escape(chars: list[str], index: int) -> int:
        chars[index] = " "
        if index + 1 < len(chars) and chars[index + 1] != "\n":
            chars[index + 1] = " "
            return index + 2
        return index + 1

    @staticmethod
    def _cpp_raw_string_end(content: str, index: int) -> int | None:
        if content[index : index + 2] != 'R"':
            return None
        open_paren = content.find("(", index + 2)
        if open_paren == -1:
            return None
        delimiter = content[index + 2 : open_paren]
        if len(delimiter) > 16 or any(
            char.isspace() or char in {"\\", "(", ")"} for char in delimiter
        ):
            return None
        close_marker = ")" + delimiter + '"'
        close_start = content.find(close_marker, open_paren + 1)
        return len(content) if close_start == -1 else close_start + len(close_marker)

    @staticmethod
    def _blank_cpp_span(chars: list[str], start: int, end: int) -> None:
        for position in range(start, end):
            if chars[position] != "\n":
                chars[position] = " "

    @staticmethod
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

    @staticmethod
    def _empty_catch_all_lines(masked: str) -> list[int]:
        pattern = re.compile(r"catch\s*\(\s*\.\.\.\s*\)\s*\{(?P<body>.*?)\}", re.DOTALL)
        return [
            masked[: match.start()].count("\n") + 1
            for match in pattern.finditer(masked)
            if not match.group("body").strip()
        ]

    def _append_analysis_error(
        self,
        targets: list[InspectionTarget],
        path,
        name: str,
        message: str,
        line: int,
    ) -> None:
        self._analysis_errors.append(message)
        targets.append(
            InspectionTarget(
                file_path=str(path.relative_to(self.project_root)),
                start_line=line,
                target_name=name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )
