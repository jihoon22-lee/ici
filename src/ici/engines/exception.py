"""9. Exception handling safety and anti-pattern detection engine."""

import ast
import re
import time

from ici.core.models import EngineResult, EngineStatus, EvidenceState, InspectionTarget
from ici.core.project import detect_project_type, get_all_cpp_sources, get_all_python_sources
from ici.engines.base import BaseEngine


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
        if isinstance(node.exc, ast.Name) and node.exc.id == self.alias:
            self.raises.append(node)
        self.generic_visit(node)


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
                has_error, has_warning, cfg.get("mode", "pass_warn_fail")
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
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
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
            for line_no in self._empty_catch_all_lines(masked):
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
        return has_error

    @staticmethod
    def _mask_cpp_literals(content: str) -> str:
        chars = list(content)
        index = 0
        state = "code"
        while index < len(chars):
            current = chars[index]
            following = chars[index + 1] if index + 1 < len(chars) else ""
            if state == "code" and current == "/" and following == "/":
                chars[index] = chars[index + 1] = " "
                state = "line"
                index += 2
                continue
            if state == "code" and current == "/" and following == "*":
                chars[index] = chars[index + 1] = " "
                state = "block"
                index += 2
                continue
            if state == "code" and current in {'"', "'"}:
                state = current
                chars[index] = " "
                index += 1
                continue
            if state == "line":
                if current == "\n":
                    state = "code"
                else:
                    chars[index] = " "
                index += 1
                continue
            if state == "block":
                if current == "*" and following == "/":
                    chars[index] = chars[index + 1] = " "
                    state = "code"
                    index += 2
                else:
                    if current != "\n":
                        chars[index] = " "
                    index += 1
                continue
            if current == "\\":
                chars[index] = " "
                if index + 1 < len(chars) and chars[index + 1] != "\n":
                    chars[index + 1] = " "
                    index += 2
                else:
                    index += 1
                continue
            if current == state:
                state = "code"
            if current != "\n":
                chars[index] = " "
            index += 1
        return "".join(chars)

    @staticmethod
    def _destructor_throw_lines(masked: str) -> list[int]:
        lines = masked.splitlines()
        depth = 0
        active: list[int] = []
        found: list[int] = []
        destructor_re = re.compile(r"~\s*[A-Za-z_]\w*\s*\([^)]*\)[^{;]*\{")
        for line_no, line in enumerate(lines, 1):
            if destructor_re.search(line):
                active.append(depth)
            if active and re.search(r"\bthrow\b", line):
                found.append(line_no)
            depth += line.count("{") - line.count("}")
            active = [start_depth for start_depth in active if depth > start_depth]
        return found

    @staticmethod
    def _empty_catch_all_lines(masked: str) -> list[int]:
        found: list[int] = []
        lines = masked.splitlines()
        catch_re = re.compile(r"catch\s*\(\s*\.\.\.\s*\)\s*\{")
        for index, line in enumerate(lines):
            match = catch_re.search(line)
            if not match:
                continue
            tail = line[match.end() :]
            if "}" in tail and not tail[: tail.index("}")].strip():
                found.append(index + 1)
                continue
            depth = 1
            body = [tail]
            for following in lines[index + 1 :]:
                depth += following.count("{") - following.count("}")
                body.append(following)
                if depth <= 0:
                    break
            body_text = "\n".join(body)
            if body_text.split("}", 1)[0].strip():
                continue
            found.append(index + 1)
        return found

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
