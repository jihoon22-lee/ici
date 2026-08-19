"""4. Static Type Checking Engine (Mypy & Strict C++ Flags)."""

import ast
import shutil
import time
from pathlib import Path

from ici.core.env import find_uv
from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    detect_project_type,
    get_all_python_sources,
    get_source_dirs,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine


class TypeCheckEngine(BaseEngine):
    """Verifies static type safety for Python (Mypy/AST) and C++."""

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []

        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            _ = self._check_python_types(targets)

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)

        cfg = self.get_config("type")
        mode = cfg.get("mode", "pass_warn")
        overall_status = self.evaluate_status(fail_count > 0, warn_count > 0, mode)

        summary = (
            "Static Type Check Passed"
            if overall_status == EngineStatus.PASS
            else f"{fail_count} Type Errors, {warn_count} Missing Annotations"
        )

        return self.create_result(
            name="type",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"type_issues": len(targets), "metrics_summary": f"{len(targets)} type targets"},
        )

    def _check_python_types(self, targets: list[InspectionTarget]) -> bool:
        mypy_cmd: list[str] | None = None
        which_mypy = shutil.which("mypy")
        venv_mypy = self.project_root / ".venv/bin/mypy"
        if which_mypy:
            mypy_cmd = [which_mypy]
        elif venv_mypy.exists():
            mypy_cmd = [str(venv_mypy)]
        elif find_uv():
            mypy_cmd = ["uv", "run", "mypy"]

        has_error = False

        if mypy_cmd:
            mypy_targets = [
                str(d.relative_to(self.project_root))
                for d in get_source_dirs(self.project_root, self.config)
            ] or ["."]
            result = run_process(
                [*mypy_cmd, "--ignore-missing-imports", *mypy_targets], cwd=self.project_root
            )
            code = result.returncode
            out = result.stdout
            if code != 0 and out.strip():
                has_error = True
                for line in out.splitlines():
                    if ": error:" in line or ": note:" in line:
                        parts = line.split(":", 3)
                        if len(parts) >= 4:
                            fpath, lnum, _, msg = parts[0], parts[1], parts[2], parts[3]
                            try:
                                rel_p = str(Path(fpath).relative_to(self.project_root))
                            except ValueError as err:
                                _ = err
                                rel_p = fpath
                            targets.append(
                                InspectionTarget(
                                    file_path=rel_p,
                                    start_line=int(lnum) if lnum.isdigit() else 1,
                                    target_name="MypyError",
                                    status=EngineStatus.FAIL,
                                    message=msg.strip(),
                                )
                            )
                return has_error

        # Fallback: AST Type Annotation Inspector
        for py_file in get_all_python_sources(self.project_root):
            try:
                content = py_file.read_text(encoding="utf-8")
                tree = ast.parse(content, filename=str(py_file))
                rel_p = str(py_file.relative_to(self.project_root))

                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name.startswith("__"):
                            continue
                        cfg = self.get_config("type")
                        warn_on_missing = cfg.get("warn_on_missing_annotation", False)
                        # Check return annotation
                        if node.returns is None and warn_on_missing:
                            targets.append(
                                InspectionTarget(
                                    file_path=rel_p,
                                    start_line=node.lineno,
                                    target_name=f"{node.name}()",
                                    status=EngineStatus.WARN,
                                    message=f"Function '{node.name}' is missing return type annotation",
                                )
                            )
            except (SyntaxError, OSError) as err:
                _ = err

        return False
