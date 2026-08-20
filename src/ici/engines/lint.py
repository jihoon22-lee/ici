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
from ici.core.project import (
    _should_ignore_path,
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine

_RUFF_FORMAT_SUCCESS_RE = re.compile(r"\d+ files? already formatted(?:\r?\n)?\Z")
_RUFF_REFORMAT_RE = re.compile(r"Would reformat: (.+)")
_RUFF_REFORMAT_SUMMARY_RE = re.compile(
    r"(?:1 file|(?:[2-9]|[1-9]\d+) files) would be reformatted(?:\r?\n)?\Z"
)
_RUFF_WARNING_RE = re.compile(r"^warning:\s+\S.*$")
_CPP_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*"
    r"(?P<kind>fatal error|error|warning|note):\s*(?P<message>\S.*)$"
)
_CPP_CONTEXT_RE = re.compile(r"^\s*(?:\d+\s*\|.*|\|.*|[\^~].*)$")
_CPP_CONTEXT_HEADER_RE = re.compile(
    r"^.+:\s+In (?:function|member function|constructor|destructor|lambda function|"
    r"instantiation of)(?: .*)?:$"
)
_CPP_REQUIRED_FROM_RE = re.compile(r"^.+:[1-9]\d*(?::[1-9]\d*)?:\s+required from here$")


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

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        tool_errors: list[str] = []
        tool_warnings: list[str] = []
        tool_evidence: list[ToolEvidence] = []

        # 1. Python Linting & Formatting Check
        if proj_type in ("python", "hybrid") or any(self.project_root.rglob("*.py")):
            tool_errors.extend(self._lint_python(targets, tool_evidence, tool_warnings))

        # 2. C++ Linting & Syntax Check
        if proj_type in ("cpp", "hybrid") or get_all_cpp_sources(self.project_root, self.config):
            tool_errors.extend(self._lint_cpp(targets, tool_evidence))

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)
        warn_count += len(tool_warnings)

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
            extra={"violations_count": len(targets), "metrics_summary": f"{len(targets)} issues"},
            required=bool(cfg.get("required", True)),
            evidence=(
                EvidenceState.NOT_RUN
                if tool_errors
                else EvidenceState.ESTIMATED
                if tool_warnings
                else EvidenceState.MEASURED
            ),
            tool_evidence=tool_evidence,
        )

    def _lint_python(
        self,
        targets: list[InspectionTarget],
        tool_evidence: list[ToolEvidence] | None = None,
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        evidence = tool_evidence if tool_evidence is not None else []
        warnings = tool_warnings if tool_warnings is not None else []
        errors: list[str] = []
        ruff_cmd = self._find_ruff_command()
        if ruff_cmd is not None:
            errors.extend(self._run_ruff_check(ruff_cmd, targets, evidence, warnings))
            errors.extend(self._run_ruff_format(ruff_cmd, targets, evidence, warnings))
        else:
            self._record_missing_tool(evidence, "ruff")
            message = "Ruff is unavailable; AST syntax fallback is ESTIMATED"
            if self.get_config("lint").get("ruff_required", False):
                errors.append("Ruff is required but was not found")
            else:
                warnings.append(message)
        self._check_python_syntax(targets)
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
        targets: list[InspectionTarget],
        evidence: list[ToolEvidence],
        tool_warnings: list[str] | None = None,
    ) -> list[str]:
        command = [*ruff_cmd, "check", ".", "--output-format=json"]
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

        command = [*ruff_cmd, "format", "--check", "."]
        if supports_json:
            command = [*ruff_cmd, "format", "--check", "--output-format=json", "."]
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
        return "--output-format" in result.stdout, None

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
        found_reformat = False
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            match = _RUFF_REFORMAT_RE.fullmatch(line.strip())
            if match:
                found_reformat = True
                targets.append(
                    InspectionTarget(
                        file_path=match.group(1).strip(),
                        start_line=1,
                        target_name="Format:Style",
                        status=EngineStatus.WARN,
                        message="File requires reformatting (PEP 8 style mismatch)",
                    )
                )
                continue
            if index == len(lines) - 1 and _RUFF_REFORMAT_SUMMARY_RE.fullmatch(line + "\n"):
                continue
            return False
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
        if not cpp_files:
            return errors
        if not gxx:
            self._record_missing_tool(evidence, "g++")
            return ["g++ is required when C++ sources are present"]
        inc_flags = get_all_cpp_includes(self.project_root)

        for cpp in cpp_files:
            cmd = [gxx, "-fsyntax-only", "-std=c++17", "-Wall", "-Wextra", *inc_flags, str(cpp)]
            try:
                result = run_process(cmd, cwd=self.project_root)
            except Exception as exc:
                self._record_tool_exception(evidence, "g++", cmd, exc)
                errors.append(f"C++ syntax check could not execute: {cpp.name}")
                continue
            tool_record = self._record_process(evidence, "g++", cmd, result)
            if result.timed_out:
                message = f"C++ syntax check timed out: {cpp.name}"
                tool_record.error = message
                errors.append(message)
                continue
            if result.truncated:
                message = f"C++ syntax output was truncated: {cpp.name}"
                tool_record.error = message
                errors.append(message)
                continue
            if not isinstance(result.returncode, int) or result.returncode < 0:
                message = f"C++ syntax check terminated unexpectedly: {cpp.name}"
                tool_record.error = message
                errors.append(message)
                continue

            parsed_targets, malformed, found_diagnostic = self._parse_cpp_diagnostics(
                result.stdout, result.stderr
            )
            targets.extend(parsed_targets)
            if malformed:
                message = f"C++ syntax output was not parseable: {cpp.name}"
                tool_record.error = message
                errors.append(message)
            elif result.returncode >= 2:
                message = f"g++ failed with exit code {result.returncode}: {cpp.name}"
                tool_record.error = message
                errors.append(message)
            elif result.returncode != 0 and not found_diagnostic:
                message = f"C++ syntax output had no diagnostics: {cpp.name}"
                tool_record.error = message
                errors.append(message)

        return errors

    def _parse_cpp_diagnostics(
        self, stdout: str, stderr: str
    ) -> tuple[list[InspectionTarget], bool, bool]:
        parsed: list[InspectionTarget] = []
        malformed = False
        found_diagnostic = False
        for raw_line in (stdout + "\n" + stderr).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _CPP_DIAGNOSTIC_RE.match(line)
            if match:
                found_diagnostic = True
                kind = match.group("kind")
                file_path = self._diagnostic_path(match.group("file"))
                parsed.append(
                    InspectionTarget(
                        file_path=file_path,
                        start_line=int(match.group("line")),
                        target_name="C++Syntax",
                        status=EngineStatus.FAIL if "error" in kind else EngineStatus.WARN,
                        message=f"{kind}: {match.group('message')}",
                    )
                )
                continue
            if line.startswith("In file included from") or line.startswith("from "):
                continue
            if found_diagnostic and self._is_cpp_context(line):
                continue
            if _CPP_CONTEXT_HEADER_RE.fullmatch(line) or _CPP_REQUIRED_FROM_RE.fullmatch(line):
                continue
            malformed = True
        return parsed, malformed, found_diagnostic

    @staticmethod
    def _is_cpp_context(line: str) -> bool:
        return _CPP_CONTEXT_RE.fullmatch(line) is not None

    def _diagnostic_path(self, value: str) -> str:
        path = Path(value.strip())
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            if path.is_absolute():
                return str(path)
            return str(path)
