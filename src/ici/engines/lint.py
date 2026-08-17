"""2. Lint & Formatting Engine (Ruff, AST, G++, Clang-Format)."""

import ast
import json
import shutil
import time
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, InspectionTarget
from ici.core.project import (
    _should_ignore_path,
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
)
from ici.core.runner import run_process
from ici.engines.base import BaseEngine


class LintEngine(BaseEngine):
    """Verifies linting, syntax, and formatting rules across C++ and Python."""

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []

        # 1. Python Linting & Formatting Check
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            self._lint_python(targets)

        # 2. C++ Linting & Syntax Check
        if proj_type in ("cpp", "hybrid") or any(self.project_root.rglob("*.cpp")):
            self._lint_cpp(targets)

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)

        overall_status = (
            EngineStatus.FAIL
            if fail_count > 0
            else (EngineStatus.WARN if warn_count > 0 else EngineStatus.PASS)
        )
        summary = (
            "0 Violations Found"
            if overall_status == EngineStatus.PASS
            else f"{fail_count} Errors, {warn_count} Warnings Found"
        )

        return self.create_result(
            name="lint",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"violations_count": len(targets), "metrics_summary": f"{len(targets)} issues"},
        )

    def _lint_python(self, targets: list[InspectionTarget]) -> None:
        ruff_cmd: list[str] | None = None
        which_ruff = shutil.which("ruff")
        venv_ruff = self.project_root / ".venv/bin/ruff"
        if which_ruff:
            ruff_cmd = [which_ruff]
        elif venv_ruff.exists():
            ruff_cmd = [str(venv_ruff)]
        elif shutil.which("uvx"):
            ruff_cmd = ["uvx", "ruff"]
        elif shutil.which("uv"):
            ruff_cmd = ["uv", "run", "ruff"]

        # 1. Ruff check
        if ruff_cmd:
            _code, out, _err, _ = run_process(
                [*ruff_cmd, "check", ".", "--output-format=json"], cwd=self.project_root
            )
            if out.strip():
                try:
                    issues = json.loads(out)
                    for item in issues:
                        fpath = item.get("filename", "")
                        try:
                            rel_p = str(Path(fpath).relative_to(self.project_root))
                        except Exception:
                            rel_p = fpath

                        loc = item.get("location", {})
                        row = loc.get("row", 1)
                        rule = item.get("code", "RUFF")
                        msg = item.get("message", "")

                        targets.append(
                            InspectionTarget(
                                file_path=rel_p,
                                start_line=row,
                                target_name=f"Ruff:{rule}",
                                status=EngineStatus.FAIL,
                                message=msg,
                            )
                        )
                except (json.JSONDecodeError, ValueError) as err:
                    _ = err

            # 2. Ruff format check
            f_code, f_out, f_err, _ = run_process(
                [*ruff_cmd, "format", "--check", "."], cwd=self.project_root
            )
            if f_code != 0 and (f_out or f_err):
                for line in (f_out + "\n" + f_err).splitlines():
                    if "Would reformat:" in line:
                        f_name = line.replace("Would reformat:", "").strip()
                        targets.append(
                            InspectionTarget(
                                file_path=f_name,
                                start_line=1,
                                target_name="Format:Style",
                                status=EngineStatus.WARN,
                                message="File requires reformatting (PEP 8 style mismatch)",
                            )
                        )

        # 3. AST Syntax check fallback
        for py_file in self.project_root.rglob("*.py"):
            if _should_ignore_path(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                ast.parse(content, filename=str(py_file))
            except SyntaxError as e:
                rel_p = str(py_file.relative_to(self.project_root))
                targets.append(
                    InspectionTarget(
                        file_path=rel_p,
                        start_line=e.lineno or 1,
                        target_name="SyntaxError",
                        status=EngineStatus.FAIL,
                        message=f"SyntaxError: {e.msg}",
                    )
                )

    def _lint_cpp(self, targets: list[InspectionTarget]) -> None:
        gxx = shutil.which("g++")
        cpp_files = get_all_cpp_sources(self.project_root)
        inc_flags = get_all_cpp_includes(self.project_root)

        if gxx and cpp_files:
            for cpp in cpp_files:
                cmd = [gxx, "-fsyntax-only", "-std=c++17", "-Wall", "-Wextra", *inc_flags, str(cpp)]
                code, _out, err, _ = run_process(cmd, cwd=self.project_root)
                if code != 0:
                    rel_p = str(cpp.relative_to(self.project_root))
                    for line in err.splitlines():
                        if "error:" in line or "warning:" in line:
                            st = EngineStatus.FAIL if "error:" in line else EngineStatus.WARN
                            line_num = 1
                            # Parse line number e.g. src/app/main.cpp:12:5: error: ...
                            parts = line.split(":")
                            if len(parts) >= 3 and parts[1].strip().isdigit():
                                line_num = int(parts[1].strip())

                            targets.append(
                                InspectionTarget(
                                    file_path=rel_p,
                                    start_line=line_num,
                                    target_name="C++Syntax",
                                    status=st,
                                    message=line.strip(),
                                )
                            )
