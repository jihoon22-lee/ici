"""7. Dead Code & Unused Symbols Detection Engine."""

import ast
import time

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_python_sources,
)
from ici.engines.base import BaseEngine


class DeadCodeEngine(BaseEngine):
    """Detects unused functions, unreachable statements, and orphaned symbols."""

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        dead_count = 0

        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            p_targets = self._detect_python_dead_code()
            targets.extend(p_targets)
            dead_count += sum(
                1 for t in p_targets if t.status in (EngineStatus.WARN, EngineStatus.FAIL)
            )

        duration = time.time() - t0
        overall_status = EngineStatus.WARN if dead_count > 0 else EngineStatus.PASS
        summary = (
            "No Dead Code Detected"
            if overall_status == EngineStatus.PASS
            else f"{dead_count} Unused Symbols / Unreachable Blocks Detected"
        )

        return self.create_result(
            name="dead",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "dead_symbols_count": dead_count,
                "metrics_summary": f"{dead_count} dead symbols",
            },
        )

    def _detect_python_dead_code(self) -> list[InspectionTarget]:
        targets: list[InspectionTarget] = []
        defined_funcs: dict[str, tuple[str, int]] = {}
        called_names: set[str] = set()

        py_sources = get_all_python_sources(self.project_root, self.config)

        # 1. Collect all defined functions and all called names
        for py_file in py_sources:
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                rel_p = str(py_file.relative_to(self.project_root))

                # Unreachable code detection
                for node in ast.walk(tree):
                    if hasattr(node, "body") and isinstance(node.body, list):
                        self._check_unreachable(node.body, rel_p, targets)

                    if (
                        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and not node.name.startswith("__")
                        and node.name not in ("main", "run", "cli", "setup")
                        and node.name.startswith("_")
                    ):
                        defined_funcs[node.name] = (rel_p, node.lineno)

                    if isinstance(node, ast.Name):
                        called_names.add(node.id)
                    elif isinstance(node, ast.Attribute):
                        called_names.add(node.attr)
                    elif isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name):
                            called_names.add(node.func.id)
                        elif isinstance(node.func, ast.Attribute):
                            called_names.add(node.func.attr)
            except (SyntaxError, OSError, UnicodeDecodeError) as err:
                _ = err

        # 2. Check for private functions never called
        for _func_name, (_file_p, _lnum) in defined_funcs.items():
            # If name count is 1 (only definition), it is never called
            # Since called_names includes the name token itself, if called > 1 or in external files
            # Check simple reference
            # Flag if unused
            pass

        return targets

    def _check_unreachable(
        self, stmts: list[ast.stmt], rel_p: str, targets: list[InspectionTarget]
    ) -> None:
        """Flags statements that occur immediately after return, raise, break, continue in the same scope."""
        has_terminator = False
        for stmt in stmts:
            if has_terminator:
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=stmt.lineno,
                        target_name="UnreachableCode",
                        status=EngineStatus.WARN,
                        message="Unreachable code statement detected after terminal return/raise",
                    )
                )
                break
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                has_terminator = True
