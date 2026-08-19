"""2. Lint & Formatting Engine (Ruff, AST, G++, Clang-Format)."""

import ast
import json
import shutil
import time
from pathlib import Path

from ici.core.env import find_uv
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
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
        tool_errors: list[str] = []
        tool_evidence: list[ToolEvidence] = []

        # 1. Python Linting & Formatting Check
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            tool_errors.extend(self._lint_python(targets, tool_evidence))

        # 2. C++ Linting & Syntax Check
        if proj_type in ("cpp", "hybrid") or any(self.project_root.rglob("*.cpp")):
            tool_errors.extend(self._lint_cpp(targets, tool_evidence))

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)

        cfg = self.get_config("lint")
        mode = cfg.get("mode", "pass_warn_fail")
        overall_status = (
            EngineStatus.ERROR
            if tool_errors
            else self.evaluate_status(fail_count > 0, warn_count > 0, mode)
        )

        summary = (
            "; ".join(tool_errors[:3])
            if tool_errors
            else "0 Violations Found"
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
            required=bool(cfg.get("required", True)),
            evidence=EvidenceState.NOT_RUN if tool_errors else EvidenceState.MEASURED,
            tool_evidence=tool_evidence,
        )

    def _lint_python(
        self, targets: list[InspectionTarget], tool_evidence: list[ToolEvidence] | None = None
    ) -> list[str]:
        errors: list[str] = []
        evidence = tool_evidence if tool_evidence is not None else []
        ruff_cmd: list[str] | None = None
        which_ruff = shutil.which("ruff")
        venv_ruff = self.project_root / ".venv/bin/ruff"
        if which_ruff:
            ruff_cmd = [which_ruff]
        elif venv_ruff.exists():
            ruff_cmd = [str(venv_ruff)]
        elif shutil.which("uvx"):
            ruff_cmd = ["uvx", "ruff"]
        elif find_uv():
            ruff_cmd = ["uv", "run", "ruff"]

        # 1. Ruff check
        if ruff_cmd:
            check_cmd = [*ruff_cmd, "check", ".", "--output-format=json"]
            result = run_process(check_cmd, cwd=self.project_root)
            evidence.append(
                ToolEvidence(
                    name="ruff check",
                    path=check_cmd[0],
                    argv=check_cmd,
                    returncode=result.returncode,
                )
            )
            out = result.stdout
            if result.timed_out:
                errors.append("Ruff check timed out")
            elif result.truncated:
                errors.append("Ruff check output was truncated")
            elif result.returncode not in (0, 1):
                errors.append(f"Ruff check failed with exit code {result.returncode}")
            elif result.stderr.strip():
                errors.append("Ruff check emitted unexpected stderr")
            elif result.returncode == 1 and not out.strip():
                errors.append("Ruff check returned violations without parseable JSON")
            elif not out.strip():
                errors.append("Ruff check succeeded without parseable JSON")
            else:
                try:
                    issues = json.loads(out)
                    if not isinstance(issues, list):
                        raise ValueError("Ruff JSON output is not a list")
                    if result.returncode == 1 and not issues:
                        errors.append("Ruff reported violations without any JSON findings")
                    for item in issues:
                        if not isinstance(item, dict):
                            raise ValueError("Ruff JSON item is not an object")
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
                    errors.append(f"Ruff check output was not valid JSON: {err}")

            # 2. Ruff format check
            format_cmd = [*ruff_cmd, "format", "--check", "."]
            format_result = run_process(format_cmd, cwd=self.project_root)
            evidence.append(
                ToolEvidence(
                    name="ruff format",
                    path=format_cmd[0],
                    argv=format_cmd,
                    returncode=format_result.returncode,
                )
            )
            f_code = format_result.returncode
            f_out = format_result.stdout
            f_err = format_result.stderr
            if format_result.timed_out:
                errors.append("Ruff format check timed out")
            elif format_result.truncated:
                errors.append("Ruff format output was truncated")
            elif f_code not in (0, 1):
                errors.append(f"Ruff format check failed with exit code {f_code}")
            elif f_code == 1 and not (f_out or f_err):
                errors.append("Ruff format check failed without diagnostic output")
            elif f_code == 0 and (f_out.strip() or f_err.strip()):
                errors.append("Ruff format output was not parseable")
            elif f_code != 0:
                found_reformat = False
                for line in (f_out + "\n" + f_err).splitlines():
                    if "Would reformat:" in line:
                        found_reformat = True
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
                if not found_reformat:
                    errors.append("Ruff format output was not parseable")

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

        return errors

    def _lint_cpp(
        self, targets: list[InspectionTarget], tool_evidence: list[ToolEvidence] | None = None
    ) -> list[str]:
        errors: list[str] = []
        evidence = tool_evidence if tool_evidence is not None else []
        gxx = shutil.which("g++")
        cpp_files = get_all_cpp_sources(self.project_root, self.config)
        inc_flags = get_all_cpp_includes(self.project_root)

        if gxx and cpp_files:
            for cpp in cpp_files:
                cmd = [gxx, "-fsyntax-only", "-std=c++17", "-Wall", "-Wextra", *inc_flags, str(cpp)]
                result = run_process(cmd, cwd=self.project_root)
                evidence.append(
                    ToolEvidence(
                        name="g++ syntax check",
                        path=cmd[0],
                        argv=cmd,
                        returncode=result.returncode,
                    )
                )
                code = result.returncode
                err = result.stderr
                if result.timed_out:
                    errors.append(f"C++ syntax check timed out: {cpp.name}")
                elif result.truncated:
                    errors.append(f"C++ syntax output was truncated: {cpp.name}")
                elif code != 0:
                    rel_p = str(cpp.relative_to(self.project_root))
                    found_diagnostic = False
                    for line in err.splitlines():
                        if "error:" in line or "warning:" in line:
                            found_diagnostic = True
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
                    if not found_diagnostic:
                        errors.append(f"C++ syntax output was not parseable: {cpp.name}")

        return errors
