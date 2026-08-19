"""4. Static Type Checking Engine (Mypy with explicit C++ scope)."""

import ast
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
    detect_project_type,
    get_all_cpp_sources,
    get_all_python_sources,
    get_source_dirs,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine

_MYPY_SUCCESS_RE = re.compile(r"Success: no issues found in (?P<count>\d+) source files?\r?\n?\Z")
_MYPY_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*"
    r"(?P<kind>error|note):\s*(?P<message>\S.*)$"
)
_MYPY_SUMMARY_RE = re.compile(r"Found \d+ errors? in \d+ files? \(checked \d+ source files?\)")


class TypeCheckEngine(BaseEngine):
    """Verifies Python static types; C++ sources receive an explicit SKIP target."""

    def run(self) -> EngineResult:
        t0 = time.time()
        proj_type = detect_project_type(self.project_root)
        targets: list[InspectionTarget] = []
        tool_errors: list[str] = []
        tool_warnings: list[str] = []
        tool_evidence: list[ToolEvidence] = []

        has_python_scope = proj_type in ("python", "hybrid") or any(
            self.project_root.rglob("*.py")
        )
        python_files = (
            get_all_python_sources(self.project_root, self.config) if has_python_scope else []
        )
        if has_python_scope:
            if python_files:
                self._check_python_types(
                    targets, tool_errors, tool_warnings, tool_evidence, python_files
                )
            else:
                self._mark_python_type_check_skipped(targets, tool_warnings)

        cpp_files = get_all_cpp_sources(self.project_root, self.config)
        if cpp_files:
            self._mark_cpp_type_check_skipped(targets, tool_warnings, len(cpp_files))

        duration = time.time() - t0
        fail_count = sum(1 for t in targets if t.status == EngineStatus.FAIL)
        warn_count = sum(1 for t in targets if t.status == EngineStatus.WARN)
        warn_count += len(tool_warnings)

        cfg = self.get_config("type")
        mode = cfg.get("mode", "pass_warn")
        overall_status = (
            EngineStatus.ERROR
            if tool_errors
            else self.evaluate_status(fail_count > 0, warn_count > 0, mode)
        )

        if tool_errors:
            summary = "; ".join(tool_errors[:3])
        elif overall_status == EngineStatus.PASS:
            summary = "Static Type Check Passed"
        else:
            summary_parts = [f"{fail_count} Type Findings, {warn_count} Warnings"]
            summary_parts.extend(tool_warnings[:2])
            summary = "; ".join(summary_parts)

        return self.create_result(
            name="type",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"type_issues": len(targets), "metrics_summary": f"{len(targets)} type targets"},
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

    def _check_python_types(
        self,
        targets: list[InspectionTarget],
        tool_errors: list[str] | None = None,
        tool_warnings: list[str] | None = None,
        tool_evidence: list[ToolEvidence] | None = None,
        python_sources: list[Path] | None = None,
    ) -> bool:
        errors = tool_errors if tool_errors is not None else []
        warnings = tool_warnings if tool_warnings is not None else []
        evidence = tool_evidence if tool_evidence is not None else []
        mypy_cmd = self._find_mypy_cmd()
        if mypy_cmd is not None and self._run_mypy(mypy_cmd, targets, errors, evidence):
            return True
        if mypy_cmd is None:
            evidence.append(
                ToolEvidence(
                    name="mypy",
                    path="",
                    error="tool not found; no command was executed",
                )
            )
            if self.get_config("type").get("mypy_required", False):
                errors.append("Mypy is required but was not found")
            else:
                warnings.append("Mypy is unavailable; AST fallback is ESTIMATED")
        return self._check_python_annotations(targets, python_sources)

    def _find_mypy_cmd(self) -> list[str] | None:
        which_mypy = shutil.which("mypy")
        if which_mypy:
            return [which_mypy]
        venv_mypy = find_project_executable(self.project_root, "mypy")
        if venv_mypy:
            return [venv_mypy]
        return None

    def _run_mypy(
        self,
        mypy_cmd: list[str],
        targets: list[InspectionTarget],
        errors: list[str],
        evidence: list[ToolEvidence],
    ) -> bool:
        mypy_targets = [
            str(d.relative_to(self.project_root))
            for d in get_source_dirs(self.project_root, self.config)
        ] or ["."]
        mypy_argv = [*mypy_cmd, "--ignore-missing-imports", *mypy_targets]
        try:
            result = run_process(mypy_argv, cwd=self.project_root)
        except Exception as exc:
            self._record_tool_exception(evidence, mypy_argv, exc)
            errors.append(f"Mypy could not execute: {type(exc).__name__}: {exc}")
            return False
        tool_record = self._record_process(evidence, mypy_argv, result)
        if result.timed_out:
            message = "Mypy timed out"
            tool_record.error = message
            errors.append(message)
            return False
        if result.truncated:
            message = "Mypy output was truncated"
            tool_record.error = message
            errors.append(message)
            return False
        if not isinstance(result.returncode, int) or result.returncode < 0:
            message = "Mypy terminated before producing a result"
            tool_record.error = message
            errors.append(message)
            return False
        if result.returncode >= 2:
            self._parse_mypy_diagnostics(result.stdout, result.stderr, targets)
            message = f"Mypy tool failed with exit code {result.returncode}"
            tool_record.error = message
            errors.append(message)
            return False
        if result.returncode == 1:
            if not self._parse_mypy_diagnostics(result.stdout, result.stderr, targets):
                message = "Mypy diagnostics were not parseable"
                tool_record.error = message
                errors.append(message)
            return False
        if result.stderr.strip():
            message = "Mypy emitted unexpected stderr on success"
            tool_record.error = message
            errors.append(message)
            return False
        if not self._is_valid_mypy_success(result.stdout):
            message = "Mypy success output was not parseable"
            tool_record.error = message
            errors.append(message)
            return False
        return True

    def _parse_mypy_diagnostics(
        self,
        stdout: str,
        stderr: str,
        targets: list[InspectionTarget],
    ) -> bool:
        has_error = False
        malformed = False
        for raw_line in (stdout + "\n" + stderr).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            match = _MYPY_DIAGNOSTIC_RE.fullmatch(line)
            if match:
                has_error = has_error or match.group("kind") == "error"
                targets.append(
                    InspectionTarget(
                        file_path=self._diagnostic_path(match.group("file")),
                        start_line=int(match.group("line")),
                        target_name=("MypyError" if match.group("kind") == "error" else "MypyNote"),
                        status=(
                            EngineStatus.FAIL
                            if match.group("kind") == "error"
                            else EngineStatus.WARN
                        ),
                        message=match.group("message"),
                    )
                )
                continue
            if _MYPY_SUMMARY_RE.fullmatch(line):
                continue
            malformed = True
        return has_error and not malformed

    def _append_mypy_target(self, line: str, targets: list[InspectionTarget]) -> None:
        match = _MYPY_DIAGNOSTIC_RE.fullmatch(line.strip())
        if not match:
            return
        kind = match.group("kind")
        try:
            rel_path = self._diagnostic_path(match.group("file"))
        except (TypeError, ValueError):
            rel_path = match.group("file")
        targets.append(
            InspectionTarget(
                file_path=rel_path,
                start_line=int(match.group("line")),
                target_name="MypyError" if kind == "error" else "MypyNote",
                status=EngineStatus.FAIL if kind == "error" else EngineStatus.WARN,
                message=match.group("message"),
            )
        )

    @staticmethod
    def _is_valid_mypy_success(output: str) -> bool:
        match = _MYPY_SUCCESS_RE.fullmatch(output)
        if not match:
            return False
        count = int(match.group("count"))
        if count < 1:
            return False
        expected = "source file" if count == 1 else "source files"
        normalized = output.rstrip("\r\n")
        return normalized == f"Success: no issues found in {count} {expected}"

    @staticmethod
    def _record_process(
        evidence: list[ToolEvidence], command: list[str], result: ProcessResult
    ) -> ToolEvidence:
        error = ""
        if result.timed_out:
            error = "timed out"
        elif result.truncated:
            error = "output truncated"
        elif not isinstance(result.returncode, int) or result.returncode < 0:
            error = "process failed to start or terminated by signal"
        item = ToolEvidence(
            name="mypy",
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
        evidence: list[ToolEvidence], command: list[str], exc: Exception
    ) -> None:
        evidence.append(
            ToolEvidence(
                name="mypy",
                path=command[0],
                argv=command,
                error=f"{type(exc).__name__}: {exc}",
            )
        )

    def _diagnostic_path(self, value: str) -> str:
        path = Path(value.strip())
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return str(path)

    def _mark_cpp_type_check_skipped(
        self, targets: list[InspectionTarget], warnings: list[str], count: int
    ) -> None:
        for source in get_all_cpp_sources(self.project_root, self.config):
            targets.append(
                InspectionTarget(
                    file_path=str(source.relative_to(self.project_root)),
                    start_line=1,
                    target_name="C++TypeCheck",
                    status=EngineStatus.SKIP,
                    message="C++ type checking is not implemented; source was not type-checked",
                )
            )
        warnings.append(
            f"C++ type checking is skipped for {count} source file(s); evidence is ESTIMATED"
        )

    def _mark_python_type_check_skipped(
        self, targets: list[InspectionTarget], warnings: list[str]
    ) -> None:
        targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="Mypy",
                status=EngineStatus.SKIP,
                message="No applicable Python source files were selected; Mypy was not run",
            )
        )
        warnings.append(
            "Python type checking is skipped: no applicable Python source files; "
            "evidence is ESTIMATED"
        )

    def _check_python_annotations(
        self, targets: list[InspectionTarget], python_sources: list[Path] | None = None
    ) -> bool:
        source_files = (
            python_sources
            if python_sources is not None
            else get_all_python_sources(self.project_root, self.config)
        )
        for py_file in source_files:
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
