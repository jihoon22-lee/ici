"""2. Lint & Formatting Engine (Ruff, AST, G++, Clang-Format)."""

import ast
import json
import re
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
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine

_RUFF_FORMAT_SUCCESS_RE = re.compile(r"\d+ files? already formatted(?:\r?\n)?\Z")


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
        evidence = tool_evidence if tool_evidence is not None else []
        errors: list[str] = []
        ruff_cmd = self._find_ruff_command()
        if ruff_cmd is not None:
            errors.extend(self._run_ruff_check(ruff_cmd, targets, evidence))
            errors.extend(self._run_ruff_format(ruff_cmd, targets, evidence))
        self._check_python_syntax(targets)
        return errors

    def _find_ruff_command(self) -> list[str] | None:
        which_ruff = shutil.which("ruff")
        venv_ruff = self.project_root / ".venv/bin/ruff"
        if which_ruff:
            return [which_ruff]
        if venv_ruff.exists():
            return [str(venv_ruff)]
        if shutil.which("uvx"):
            return ["uvx", "ruff"]
        if find_uv():
            return ["uv", "run", "ruff"]
        return None

    def _run_ruff_check(
        self,
        ruff_cmd: list[str],
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
    ) -> list[str]:
        command = [*ruff_cmd, "check", ".", "--output-format=json"]
        result = run_process(command, cwd=self.project_root)
        evidence.append(
            ToolEvidence(
                name="ruff check",
                path=command[0],
                argv=command,
                returncode=result.returncode,
            )
        )
        return self._evaluate_ruff_check(result, targets)

    def _evaluate_ruff_check(
        self, result: ProcessResult, targets: list[InspectionTarget]
    ) -> list[str]:
        if result.timed_out:
            return ["Ruff check timed out"]
        if result.truncated:
            return ["Ruff check output was truncated"]
        if result.returncode not in (0, 1):
            return [f"Ruff check failed with exit code {result.returncode}"]
        if result.stderr.strip():
            return ["Ruff check emitted unexpected stderr"]
        if not result.stdout.strip():
            message = "violations" if result.returncode == 1 else "succeeded"
            return [f"Ruff check {message} without parseable JSON"]
        try:
            issues = json.loads(result.stdout)
            if not isinstance(issues, list):
                raise ValueError("Ruff JSON output is not a list")
            if result.returncode == 1 and not issues:
                return ["Ruff reported violations without any JSON findings"]
            self._append_ruff_findings(issues, targets)
        except (json.JSONDecodeError, ValueError) as error:
            return [f"Ruff check output was not valid JSON: {error}"]
        return []

    def _append_ruff_findings(self, issues: list[object], targets: list[InspectionTarget]) -> None:
        for item in issues:
            if not isinstance(item, dict):
                raise ValueError("Ruff JSON item is not an object")
            fpath = item.get("filename", "")
            try:
                rel_path = str(Path(fpath).relative_to(self.project_root))
            except (TypeError, ValueError):
                rel_path = str(fpath)
            location = item.get("location", {})
            if not isinstance(location, dict):
                raise ValueError("Ruff JSON location is not an object")
            targets.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=location.get("row", 1),
                    target_name=f"Ruff:{item.get('code', 'RUFF')}",
                    status=EngineStatus.FAIL,
                    message=str(item.get("message", "")),
                )
            )

    def _run_ruff_format(
        self,
        ruff_cmd: list[str],
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
    ) -> list[str]:
        command = [*ruff_cmd, "format", "--check", "."]
        result = run_process(command, cwd=self.project_root)
        evidence.append(
            ToolEvidence(
                name="ruff format",
                path=command[0],
                argv=command,
                returncode=result.returncode,
            )
        )
        return self._evaluate_ruff_format(result, targets)

    def _evaluate_ruff_format(
        self, result: ProcessResult, targets: list[InspectionTarget]
    ) -> list[str]:
        if result.timed_out:
            return ["Ruff format check timed out"]
        if result.truncated:
            return ["Ruff format output was truncated"]
        if result.returncode not in (0, 1):
            return [f"Ruff format check failed with exit code {result.returncode}"]
        if result.returncode == 1 and not (result.stdout or result.stderr):
            return ["Ruff format check failed without diagnostic output"]
        if result.returncode == 0 and not self._is_valid_format_success(result):
            return ["Ruff format output was not parseable"]
        if result.returncode == 1 and not self._append_reformat_targets(result, targets):
            return ["Ruff format output was not parseable"]
        return []

    @staticmethod
    def _is_valid_format_success(result: ProcessResult) -> bool:
        return not result.stderr.strip() and (
            not result.stdout.strip()
            or _RUFF_FORMAT_SUCCESS_RE.fullmatch(result.stdout) is not None
        )

    @staticmethod
    def _append_reformat_targets(result: ProcessResult, targets: list[InspectionTarget]) -> bool:
        found_reformat = False
        for line in (result.stdout + "\n" + result.stderr).splitlines():
            if "Would reformat:" not in line:
                continue
            found_reformat = True
            targets.append(
                InspectionTarget(
                    file_path=line.replace("Would reformat:", "").strip(),
                    start_line=1,
                    target_name="Format:Style",
                    status=EngineStatus.WARN,
                    message="File requires reformatting (PEP 8 style mismatch)",
                )
            )
        return found_reformat

    def _check_python_syntax(self, targets: list[InspectionTarget]) -> None:
        for py_file in self.project_root.rglob("*.py"):
            if _should_ignore_path(py_file):
                continue
            try:
                ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except SyntaxError as error:
                targets.append(
                    InspectionTarget(
                        file_path=str(py_file.relative_to(self.project_root)),
                        start_line=error.lineno or 1,
                        target_name="SyntaxError",
                        status=EngineStatus.FAIL,
                        message=f"SyntaxError: {error.msg}",
                    )
                )

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
