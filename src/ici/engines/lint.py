"""2. Lint & Formatting Engine (Ruff, AST, and g++ diagnostics)."""

import ast
import json
import re
import shutil
import time
from pathlib import Path

from ici.core.env import find_project_executable
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines._cpp_lint import run_cpp_lint
from ici.engines.base import BaseEngine

_RUFF_FORMAT_SUCCESS_RE = re.compile(r"\d+ files? already formatted(?:\r?\n)?\Z")
_RUFF_REFORMAT_RE = re.compile(r"Would reformat: (?P<path>\S.*)")
_RUFF_REFORMAT_SUMMARY_RE = re.compile(
    r"(?P<would_count>[1-9]\d*) (?P<would_unit>file|files) would be reformatted"
    r"(?:, (?P<already_count>[1-9]\d*) (?P<already_unit>file|files) already formatted)?"
)
_RUFF_WARNING_RE = re.compile(r"^warning:\s+\S.*$")
_RUFF_FORMAT_PREVIEW_ONLY_RE = re.compile(r"only respected in preview mode", re.IGNORECASE)


def _parse_ruff_warning_blocks(stderr: str) -> tuple[list[str], str | None]:
    """Parse Ruff's line-oriented warning blocks without accepting arbitrary stderr."""

    if not stderr.strip():
        return [], None

    lines = stderr.splitlines()
    warnings: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _RUFF_WARNING_RE.fullmatch(line):
            return [], f"unrecognized stderr line: {line!r}"

        block = [line]
        index += 1
        while index < len(lines):
            continuation = lines[index]
            if _RUFF_WARNING_RE.fullmatch(continuation):
                break
            if continuation and continuation[0].isspace():
                block.append(continuation)
                index += 1
                continue
            return [], f"unrecognized stderr line: {continuation!r}"

        warnings.append("\n".join(block).rstrip("\r\n"))

    return warnings, None


class LintEngine(BaseEngine):
    """Verifies linting, syntax, and formatting rules across C++ and Python."""

    CACHE_IMPLEMENTATION_MODULES = (
        "ici.core._cpp_replay_policy",
        "ici.core.cpp_replay",
        "ici.engines._cpp_lint",
        "ici.engines.lint",
    )

    def run(self) -> EngineResult:
        t0 = time.time()
        targets: list[InspectionTarget] = []
        tool_errors: list[str] = []
        tool_warnings: list[str] = []
        tool_evidence: list[ToolEvidence] = []
        self._python_files_parsed = 0
        self._cpp_analysis_mode = "not_applicable"
        self._cpp_configurations_checked = 0
        self._cpp_sources_checked = 0
        self._cpp_context_missing = 0

        # 1. Python Linting & Formatting Check
        python_files = self.project_python_sources()
        if python_files:
            tool_errors.extend(
                self._lint_python(python_files, targets, tool_evidence, tool_warnings)
            )

        # 2. C++ Linting & Syntax Check
        cpp_files = self.project_cpp_sources()
        if cpp_files:
            tool_errors.extend(self._lint_cpp(cpp_files, targets, tool_evidence, tool_warnings))

        nothing_applies = not python_files and not cpp_files
        if nothing_applies:
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="LintScope",
                    status=EngineStatus.SKIP,
                    message="No applicable source files were selected; lint analysis was not run",
                )
            )

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)
        warn_count += len(tool_warnings)

        cfg = self.get_config("lint")
        mode = cfg.get("mode", "pass_warn_fail")
        overall_status = (
            EngineStatus.ERROR
            if tool_errors
            else EngineStatus.SKIP
            if nothing_applies
            else self.evaluate_status(fail_count > 0, warn_count > 0, mode)
        )

        summary = (
            "; ".join(tool_errors[:3])
            if tool_errors
            else "Lint analysis skipped: no applicable source files"
            if nothing_applies
            else "0 Violations Found"
            if overall_status == EngineStatus.PASS
            else "; ".join(tool_warnings[:2])
            if tool_warnings and not fail_count
            else f"{fail_count} Errors, {warn_count} Warnings Found"
        )

        return self.create_result(
            name="lint",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={
                "violations_count": sum(
                    target.status not in {EngineStatus.PASS, EngineStatus.SKIP}
                    for target in targets
                ),
                "python_files_parsed": getattr(self, "_python_files_parsed", 0),
                "cpp_analysis_mode": self._cpp_analysis_mode,
                "cpp_configurations_checked": self._cpp_configurations_checked,
                "cpp_sources_checked": self._cpp_sources_checked,
                "cpp_context_missing": self._cpp_context_missing,
                "metrics_summary": f"{fail_count + warn_count} issues",
            },
            required=bool(cfg.get("required", True)),
            evidence=(
                EvidenceState.NOT_RUN
                if tool_errors
                else EvidenceState.NOT_APPLICABLE
                if nothing_applies
                else EvidenceState.ESTIMATED
                if tool_warnings
                else EvidenceState.MEASURED
            ),
            tool_evidence=tool_evidence,
        )

    def _lint_python(
        self,
        python_files: list[Path],
        targets: list[InspectionTarget],
        tool_evidence: list[ToolEvidence] | None = None,
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        evidence = tool_evidence if tool_evidence is not None else []
        warnings = tool_warnings if tool_warnings is not None else []
        errors: list[str] = []
        ruff_cmd = self._find_ruff_command()
        if ruff_cmd is not None:
            errors.extend(self._run_ruff_check(ruff_cmd, python_files, targets, evidence, warnings))
            errors.extend(
                self._run_ruff_format(ruff_cmd, python_files, targets, evidence, warnings)
            )
        else:
            self._record_missing_tool(evidence, "ruff")
            message = "Ruff is unavailable; AST syntax fallback is ESTIMATED"
            if self.get_config("lint").get("ruff_required", False):
                errors.append("Ruff is required but was not found")
            else:
                warnings.append(message)
        inspected = self._check_python_syntax(python_files, targets)
        self._python_files_parsed = inspected
        if ruff_cmd is None:
            # Without this the fallback is indistinguishable from having done
            # nothing: it only speaks up on a SyntaxError, so a clean project and
            # a project it never looked at both report zero targets.
            targets.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name="ASTSyntaxFallback",
                    status=EngineStatus.PASS,
                    message=(
                        f"Ruff was unavailable; parsed {inspected} Python file(s) "
                        "for syntax errors only. Style and lint rules were not checked."
                    ),
                    metrics={"files_parsed": inspected},
                )
            )
        return errors

    @staticmethod
    def _record_missing_tool(evidence: list[ToolEvidence], name: str) -> None:
        evidence.append(
            ToolEvidence(
                name=name,
                path="",
                error="tool not found; no command was executed",
            )
        )

    @staticmethod
    def _record_process(
        evidence: list[ToolEvidence], name: str, command: list[str], result: ProcessResult
    ) -> ToolEvidence:
        error = ""
        if result.timed_out:
            error = "timed out"
        elif result.truncated:
            error = "output truncated"
        elif not isinstance(result.returncode, int) or result.returncode < 0:
            error = "process failed to start or terminated by signal"
        item = ToolEvidence(
            name=name,
            path=command[0],
            argv=command,
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
            error=error,
        )
        evidence.append(item)
        return item

    @staticmethod
    def _record_tool_exception(
        evidence: list[ToolEvidence], name: str, command: list[str], exc: Exception
    ) -> None:
        evidence.append(
            ToolEvidence(
                name=name,
                path=command[0],
                argv=command,
                error=f"{type(exc).__name__}: {exc}",
            )
        )

    def _find_ruff_command(self) -> list[str] | None:
        which_ruff = shutil.which("ruff")
        if which_ruff:
            return [which_ruff]
        venv_ruff = find_project_executable(self.project_root, "ruff")
        if venv_ruff:
            return [venv_ruff]
        return None

    def _run_ruff_check(
        self,
        ruff_cmd: list[str],
        python_files: list[Path],
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        paths = [str(path.relative_to(self.project_root)) for path in python_files]
        command = [*ruff_cmd, "check", *paths, "--output-format=json"]
        try:
            result = run_process(command, cwd=self.project_root)
        except Exception as exc:
            self._record_tool_exception(evidence, "ruff check", command, exc)
            return [f"Ruff check could not execute: {type(exc).__name__}: {exc}"]
        tool_record = self._record_process(evidence, "ruff check", command, result)
        errors = self._evaluate_ruff_check(result, targets, tool_warnings)
        if errors:
            tool_record.error = errors[0]
        return errors

    def _evaluate_ruff_check(
        self,
        result: ProcessResult,
        targets: list[InspectionTarget],
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        if result.timed_out:
            return ["Ruff check timed out"]
        if result.truncated:
            return ["Ruff check output was truncated"]
        if not isinstance(result.returncode, int) or result.returncode < 0:
            return ["Ruff check terminated before producing a result"]
        if result.returncode not in (0, 1):
            return [f"Ruff check failed with exit code {result.returncode}"]
        warnings, warning_error = _parse_ruff_warning_blocks(result.stderr)
        if warning_error:
            return [f"Ruff check emitted unexpected stderr: {warning_error}"]
        if tool_warnings is not None:
            tool_warnings.extend(warnings)
        if not result.stdout.strip():
            message = "violations" if result.returncode == 1 else "succeeded"
            return [f"Ruff check {message} without parseable JSON"]
        try:
            issues = json.loads(result.stdout)
            if not isinstance(issues, list):
                raise ValueError("Ruff JSON output is not a list")
            if result.returncode == 0 and issues:
                return ["Ruff check returned success with diagnostic findings"]
            if result.returncode == 1 and not issues:
                return ["Ruff reported violations without any JSON findings"]
            targets.extend(self._parse_ruff_findings(issues))
        except (json.JSONDecodeError, ValueError) as error:
            return [f"Ruff check output was not valid JSON: {error}"]
        return []

    def _parse_ruff_findings(self, issues: list[object]) -> list[InspectionTarget]:
        parsed: list[InspectionTarget] = []
        for item in issues:
            if not isinstance(item, dict):
                raise ValueError("Ruff JSON item is not an object")
            fpath = item.get("filename", "")
            if not isinstance(fpath, str) or not fpath.strip():
                raise ValueError("Ruff JSON filename is missing")
            code = item.get("code", "RUFF")
            message = item.get("message", "")
            if not isinstance(code, str) or not code.strip():
                raise ValueError("Ruff JSON code is missing")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("Ruff JSON message is missing")
            try:
                rel_path = str(Path(fpath).relative_to(self.project_root))
            except (TypeError, ValueError):
                rel_path = str(fpath)
            location = item.get("location", {})
            if not isinstance(location, dict):
                raise ValueError("Ruff JSON location is not an object")
            row = location.get("row")
            if isinstance(row, bool) or not isinstance(row, int) or row < 1:
                raise ValueError("Ruff JSON row is invalid")
            parsed.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=row,
                    target_name=f"Ruff:{code}",
                    status=EngineStatus.FAIL,
                    message=message,
                )
            )
        return parsed

    def _run_ruff_format(
        self,
        ruff_cmd: list[str],
        python_files: list[Path],
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        warnings = tool_warnings if tool_warnings is not None else []
        supports_json, probe_error = self._probe_ruff_format_json_support(
            ruff_cmd, evidence, warnings
        )
        if probe_error:
            return [probe_error]

        paths = [str(path.relative_to(self.project_root)) for path in python_files]
        command = [*ruff_cmd, "format", "--check", *paths]
        if supports_json:
            command = [
                *ruff_cmd,
                "format",
                "--check",
                "--output-format=json",
                *paths,
            ]
        try:
            result = run_process(command, cwd=self.project_root)
        except Exception as exc:
            self._record_tool_exception(evidence, "ruff format", command, exc)
            return [f"Ruff format could not execute: {type(exc).__name__}: {exc}"]
        tool_record = self._record_process(evidence, "ruff format", command, result)
        errors = self._evaluate_ruff_format(result, targets, warnings, json_output=supports_json)
        if errors:
            tool_record.error = errors[0]
        return errors

    def _probe_ruff_format_json_support(
        self,
        ruff_cmd: list[str],
        evidence: list[ToolEvidence],
        tool_warnings: list[str],
    ) -> tuple[bool, str | None]:
        """Detect formatter JSON support using only the locally installed Ruff."""

        command = [*ruff_cmd, "format", "--help"]
        try:
            result = run_process(command, cwd=self.project_root)
        except Exception as exc:
            message = f"Ruff format capability probe could not execute: {type(exc).__name__}: {exc}"
            self._record_tool_exception(evidence, "ruff format capability", command, exc)
            evidence[-1].error = message
            return False, message

        tool_record = self._record_process(evidence, "ruff format capability", command, result)
        if result.timed_out:
            message = "Ruff format capability probe timed out"
            tool_record.error = message
            return False, message
        if result.truncated:
            message = "Ruff format capability probe output was truncated"
            tool_record.error = message
            return False, message
        if not isinstance(result.returncode, int) or result.returncode < 0:
            message = "Ruff format capability probe terminated before producing a result"
            tool_record.error = message
            return False, message
        if result.returncode != 0:
            message = f"Ruff format capability probe failed with exit code {result.returncode}"
            tool_record.error = message
            return False, message

        _, warning_error = _parse_ruff_warning_blocks(result.stderr)
        if warning_error:
            message = f"Ruff format capability probe emitted unexpected stderr: {warning_error}"
            tool_record.error = message
            return False, message
        supports_json = (
            "--output-format" in result.stdout
            and not _RUFF_FORMAT_PREVIEW_ONLY_RE.search(result.stdout)
        )
        return supports_json, None

    def _evaluate_ruff_format(
        self,
        result: ProcessResult,
        targets: list[InspectionTarget],
        tool_warnings: list[str] | None = None,
        *,
        json_output: bool = False,
    ) -> list[str]:
        if result.timed_out:
            return ["Ruff format check timed out"]
        if result.truncated:
            return ["Ruff format output was truncated"]
        if not isinstance(result.returncode, int) or result.returncode < 0:
            return ["Ruff format check terminated before producing a result"]
        if result.returncode not in (0, 1):
            return [f"Ruff format check failed with exit code {result.returncode}"]
        warnings, warning_error = _parse_ruff_warning_blocks(result.stderr)
        if warning_error:
            return [f"Ruff format emitted unexpected stderr: {warning_error}"]
        if tool_warnings is not None:
            tool_warnings.extend(warnings)
        if json_output:
            return self._evaluate_ruff_format_json(result, targets)
        if result.returncode == 1 and not result.stdout:
            return ["Ruff format check failed without diagnostic output"]
        if result.returncode == 0 and not self._is_valid_format_success(result):
            return ["Ruff format output was not parseable"]
        if result.returncode == 1 and not self._append_reformat_targets(result, targets):
            return ["Ruff format output was not parseable"]
        return []

    def _evaluate_ruff_format_json(
        self, result: ProcessResult, targets: list[InspectionTarget]
    ) -> list[str]:
        try:
            diagnostics = json.loads(result.stdout)
            if not isinstance(diagnostics, list):
                raise ValueError("Ruff format JSON output is not a list")
        except (json.JSONDecodeError, ValueError) as error:
            return [f"Ruff format output was not parseable as JSON: {error}"]

        if result.returncode == 0:
            if diagnostics:
                return ["Ruff format returned success with diagnostic findings"]
            return []
        if not diagnostics:
            return ["Ruff format reported violations without any JSON findings"]

        try:
            parsed = self._parse_ruff_format_diagnostics(diagnostics)
        except ValueError as error:
            return [f"Ruff format output was not parseable: {error}"]
        targets.extend(parsed)
        return []

    def _parse_ruff_format_diagnostics(self, diagnostics: list[object]) -> list[InspectionTarget]:
        parsed: list[InspectionTarget] = []
        for item in diagnostics:
            if not isinstance(item, dict):
                raise ValueError("Ruff format JSON item is not an object")
            filename = item.get("filename")
            if not isinstance(filename, str) or not filename.strip():
                raise ValueError("Ruff format JSON filename is missing")
            if item.get("code") != "unformatted":
                raise ValueError("Ruff format JSON code is not unformatted")
            location = item.get("location")
            if not isinstance(location, dict):
                raise ValueError("Ruff format JSON location is not an object")
            row = location.get("row")
            if isinstance(row, bool) or not isinstance(row, int) or row < 1:
                raise ValueError("Ruff format JSON row is invalid")
            column = location.get("column")
            if column is not None and (
                isinstance(column, bool) or not isinstance(column, int) or column < 1
            ):
                raise ValueError("Ruff format JSON column is invalid")
            message = item.get("message", "File requires reformatting (PEP 8 style mismatch)")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("Ruff format JSON message is invalid")
            try:
                rel_path = str(Path(filename).relative_to(self.project_root))
            except (TypeError, ValueError):
                rel_path = str(filename)
            parsed.append(
                InspectionTarget(
                    file_path=rel_path,
                    start_line=row,
                    target_name="Format:Style",
                    status=EngineStatus.WARN,
                    message=message,
                )
            )
        return parsed

    @staticmethod
    def _is_valid_format_success(result: ProcessResult) -> bool:
        return (
            not result.stdout.strip()
            or _RUFF_FORMAT_SUCCESS_RE.fullmatch(result.stdout) is not None
        )

    @staticmethod
    def _append_reformat_targets(result: ProcessResult, targets: list[InspectionTarget]) -> bool:
        lines = result.stdout.splitlines()
        if not lines:
            return False

        summary_matches = [
            (index, _RUFF_REFORMAT_SUMMARY_RE.fullmatch(line)) for index, line in enumerate(lines)
        ]
        summaries = [(index, match) for index, match in summary_matches if match is not None]
        if len(summaries) != 1 or summaries[0][0] != len(lines) - 1:
            return False

        summary = summaries[0][1]
        assert summary is not None
        would_count = int(summary.group("would_count"))
        would_unit = summary.group("would_unit")
        if would_unit != ("file" if would_count == 1 else "files"):
            return False

        already_count = summary.group("already_count")
        already_unit = summary.group("already_unit")
        if already_count is not None:
            already_count_value = int(already_count)
            if already_unit != ("file" if already_count_value == 1 else "files"):
                return False

        paths: list[str] = []
        for line in lines[:-1]:
            match = _RUFF_REFORMAT_RE.fullmatch(line)
            if match is None:
                return False
            path = match.group("path").strip()
            if not path:
                return False
            paths.append(path)

        if not paths or would_count != len(paths):
            return False

        targets.extend(
            InspectionTarget(
                file_path=path,
                start_line=1,
                target_name="Format:Style",
                status=EngineStatus.WARN,
                message="File requires reformatting (PEP 8 style mismatch)",
            )
            for path in paths
        )
        return True

    def _check_python_syntax(
        self, python_files: list[Path], targets: list[InspectionTarget]
    ) -> int:
        """Parse every Python file and report the ones that will not parse.

        Returns how many files were read, which the caller needs: a clean run
        and a run that inspected nothing both produce zero targets, and without
        the count the report cannot tell them apart.
        """
        inspected = 0
        for py_file in python_files:
            inspected += 1
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
        return inspected

    def _lint_cpp(
        self,
        cpp_files: list[Path],
        targets: list[InspectionTarget],
        tool_evidence: list[ToolEvidence] | None = None,
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        evidence = tool_evidence if tool_evidence is not None else []
        warnings = tool_warnings if tool_warnings is not None else []
        outcome = run_cpp_lint(
            self.project_root,
            cpp_files,
            self.analysis_context,
            self.project_cpp_include_flags(),
            runner=run_process,
            which=shutil.which,
        )
        targets.extend(outcome.targets)
        evidence.extend(outcome.evidence)
        warnings.extend(outcome.warnings)
        self._cpp_analysis_mode = outcome.mode
        self._cpp_configurations_checked = outcome.configurations_checked
        self._cpp_sources_checked = outcome.sources_checked
        self._cpp_context_missing = outcome.missing_sources
        return outcome.errors
