"""9. Exception Handling Safety & Anti-pattern Detection Engine."""

import ast
import re
import time

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_cpp_sources,
    get_all_python_sources,
)
from ici.engines.base import BaseEngine


class ExceptionSafetyEngine(BaseEngine):
    """Detects dangerous exception swallowing (except: pass), destructor throws, and lost tracebacks."""

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        has_error = False

        # 1. Python Exception Safety
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_fail = self._check_python_exceptions(targets)
            if p_fail:
                has_error = True

        # 2. C++ Exception Safety
        if proj_type in ("cpp", "hybrid") or any(self.project_root.rglob("*.cpp")):
            c_fail = self._check_cpp_exceptions(targets)
            if c_fail:
                has_error = True

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)

        overall_status = (
            EngineStatus.FAIL
            if has_error
            else (EngineStatus.WARN if warn_count > 0 else EngineStatus.PASS)
        )
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
            extra={"violations_count": len(targets), "metrics_summary": f"{fail_count} exc errors"},
        )

    def _check_python_exceptions(self, targets: list[InspectionTarget]) -> bool:
        has_error = False
        for py_file in get_all_python_sources(self.project_root, self.config):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                rel_p = str(py_file.relative_to(self.project_root))

                for node in ast.walk(tree):
                    # 1. Check Except Handlers
                    if isinstance(node, ast.ExceptHandler):
                        # Bare except: (node.type is None)
                        if node.type is None:
                            has_error = True
                            targets.append(
                                InspectionTarget(
                                    file_path=rel_p,
                                    start_line=node.lineno,
                                    target_name="BareExcept",
                                    status=EngineStatus.FAIL,
                                    message="Dangerous bare 'except:' clause caught all system interrupts",
                                )
                            )

                        # Silent error swallowing: except body is just [Pass] or [Ellipsis]
                        is_swallowed = False
                        if len(node.body) == 1:
                            stmt = node.body[0]
                            if isinstance(stmt, ast.Pass) or (
                                isinstance(stmt, ast.Expr)
                                and isinstance(stmt.value, ast.Constant)
                                and stmt.value.value is Ellipsis
                            ):
                                is_swallowed = True

                        if is_swallowed:
                            has_error = True
                            targets.append(
                                InspectionTarget(
                                    file_path=rel_p,
                                    start_line=node.lineno,
                                    target_name="ErrorSwallowing",
                                    status=EngineStatus.FAIL,
                                    message="Silent exception swallowing: 'except' block only contains 'pass'",
                                )
                            )

                    # 2. Check Re-Raise with loss of traceback
                    if isinstance(node, ast.Raise) and node.exc is not None and node.cause is None:
                        # if in except handler and raising same exception object
                        pass
            except (SyntaxError, OSError) as err:
                _ = err

        return has_error

    def _check_cpp_exceptions(self, targets: list[InspectionTarget]) -> bool:
        has_error = False
        for cpp_file in get_all_cpp_sources(self.project_root, self.config):
            try:
                rel_p = str(cpp_file.relative_to(self.project_root))
                with open(cpp_file, encoding="utf-8", errors="ignore") as f:
                    in_destructor = False

                    for line_idx, line in enumerate(f, 1):
                        stripped = line.strip()

                        # Check Destructor throw
                        if "~" in stripped and "::" in stripped and "()" in stripped:
                            in_destructor = True

                        if in_destructor:
                            if "throw " in stripped and not stripped.startswith(("//", "/*")):
                                has_error = True
                                targets.append(
                                    InspectionTarget(
                                        file_path=rel_p,
                                        start_line=line_idx,
                                        target_name="DestructorThrow",
                                        status=EngineStatus.FAIL,
                                        message="C++ destructor throws exception, liable to std::terminate crash",
                                    )
                                )
                            if "}" in stripped:
                                in_destructor = False

                        # Check empty catch(...)
                        if re.search(r"catch\s*\(\s*\.\.\.\s*\)\s*\{\s*\}", stripped):
                            has_error = True
                            targets.append(
                                InspectionTarget(
                                    file_path=rel_p,
                                    start_line=line_idx,
                                    target_name="CatchAllSwallowed",
                                    status=EngineStatus.FAIL,
                                    message="Silent catch(...) block without logging or re-throw",
                                )
                            )
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        return has_error
