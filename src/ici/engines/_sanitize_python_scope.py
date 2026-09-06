"""Python ResourceWarning scope for the sanitize engine.

The sanitize engine covers two analyses that share only its shell: a C++
sanitizer replay against compiled tests, and this one, which re-runs the
project's own pytest suite with ``ResourceWarning`` promoted to an error.
They have no code in common, and keeping both in one module is what pushed
that module past this project's own file-size gate. The mixin keeps each
analysis readable on its own while the engine still presents one result.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult, run_process

_PYTEST_EXECUTED_RE = re.compile(
    r"\b(?P<count>\d+)\s+(?:passed|failed|xfailed|xpassed)\b", re.IGNORECASE
)
_RESOURCE_WARNING_RE = re.compile(r"(?P<file>.*?\.py):(?P<line>[1-9]\d*):[^\n]*ResourceWarning")


class PythonResourceWarningMixin:
    """Run the project's pytest suite with ResourceWarning promoted to an error."""

    # What the host engine must supply. These are declared and never defined:
    # the mixin sits ahead of BaseEngine in the MRO, so a stub body here would
    # shadow the real implementation at runtime. The ``TYPE_CHECKING`` guard is
    # what keeps the method declarations from becoming attributes.
    CONFIG_SECTION: str
    project_root: Path
    _tool_errors: list[str]
    _tool_evidence: list[ToolEvidence]
    _measured_scopes: int
    _skipped_scopes: int

    if TYPE_CHECKING:

        def get_config(self, engine_name: str) -> dict[str, Any]: ...

        def project_source_dirs(self) -> list[Path]: ...

        def _record_process(
            self, name: str, command: list[str], result: ProcessResult
        ) -> ToolEvidence: ...

        def _record_tool_exception(self, name: str, command: list[str], exc: Exception) -> None: ...

        def _incomplete_message(self, label: str, result: ProcessResult) -> str: ...

        def _tool_failure_message(self, label: str, result: ProcessResult) -> str: ...

        @staticmethod
        def _process_incomplete(result: ProcessResult, *, allow_signal: bool = False) -> bool: ...

        @staticmethod
        def _append_scope_error(
            targets: list[InspectionTarget], file_path: str, name: str, message: str
        ) -> None: ...

        @staticmethod
        def _snippet(output: str) -> str: ...

    @staticmethod
    def _has_python_tests(tests_root: Path) -> bool:
        return tests_root.is_dir() and any(tests_root.rglob("*.py"))

    def _check_python_resource_warnings(
        self, tests_root: Path, targets: list[InspectionTarget]
    ) -> tuple[bool, bool]:
        python_cmd = self._resolve_python()
        command = [
            *python_cmd,
            "-W",
            "error::ResourceWarning",
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "tests",
        ]
        if not tests_root.is_dir():
            message = "Python ResourceWarning check skipped: tests directory is missing"
            return self._missing_python_scope(targets, message, command, "tests")
        if not any(
            path.suffix == ".py"
            and (path.name.startswith("test_") or path.name.endswith("_test.py"))
            for path in tests_root.rglob("*")
        ):
            message = "Python ResourceWarning check skipped: no Python test files were selected"
            return self._missing_python_scope(targets, message, command, "tests")

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTEST_ADDOPTS"] = " ".join(
            part for part in (env.get("PYTEST_ADDOPTS", ""), "-p no:cacheprovider") if part
        )
        source_paths = [str(path) for path in self._source_dirs()]
        if source_paths:
            python_paths = [*source_paths, env.get("PYTHONPATH", "")]
            env["PYTHONPATH"] = os.pathsep.join(path for path in python_paths if path)
        if env.get("WSL_DISTRO_NAME") and Path("/tmp").is_dir():
            for key in ("TMPDIR", "TMP", "TEMP"):
                env[key] = "/tmp"
        try:
            result = run_process(command, cwd=self.project_root, env=env)
        except Exception as exc:
            self._record_tool_exception("pytest resource warnings", command, exc)
            self._append_scope_error(
                targets, "tests", "PythonResourceWarnings", f"Pytest could not execute: {exc}"
            )
            return False, False
        evidence = self._record_process("pytest resource warnings", command, result)
        if self._process_incomplete(result):
            message = self._incomplete_message("Pytest ResourceWarning check", result)
            evidence.error = message
            self._tool_errors.append(message)
            self._append_scope_error(targets, "tests", "PythonResourceWarnings", message)
            return False, False
        output = f"{result.stdout}\n{result.stderr}"
        if self._pytest_module_missing(output, result.returncode):
            message = f"Pytest is unavailable: {self._snippet(output)}"
            evidence.error = message
            return self._missing_python_scope(targets, message, command, "tests")
        if result.returncode == 5 or not self._pytest_has_executed_result(output):
            message = "Pytest returned success without parseable test results"
            if result.returncode == 5:
                message = "Pytest collected 0 tests"
            evidence.error = f"{message}: {self._snippet(output)}"
            return self._missing_python_scope(targets, message, command, "tests")
        if result.returncode == 0:
            self._measured_scopes += 1
            targets.append(
                InspectionTarget(
                    file_path="tests",
                    start_line=1,
                    target_name="PythonResourceWarnings",
                    status=EngineStatus.PASS,
                    message="pytest completed with ResourceWarning promoted to errors",
                )
            )
            return False, False
        if "ResourceWarning" in output:
            if not self._resource_warning_targets(output, targets):
                targets.append(
                    InspectionTarget(
                        file_path="tests",
                        start_line=1,
                        target_name="ResourceWarning",
                        status=EngineStatus.FAIL,
                        message=self._snippet(output),
                    )
                )
            self._measured_scopes += 1
            return True, False

        message = self._tool_failure_message("Pytest ResourceWarning check", result)
        evidence.error = message
        self._tool_errors.append(message)
        self._append_scope_error(targets, "tests", "PythonResourceWarnings", message)
        return False, False

    def _resolve_python(self) -> list[str]:
        """Use the same configured/project-venv/system interpreter order as Task 5."""

        configured = self.get_config("test").get("python")
        if configured:
            return [str(configured)]
        candidates = (
            self.project_root / ".venv" / "bin" / "python",
            self.project_root / ".venv" / "Scripts" / "python.exe",
        )
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return [str(candidate)]
            except OSError:
                continue
        return [sys.executable]

    def _source_dirs(self) -> list[Path]:
        return self.project_source_dirs()

    def _missing_python_scope(
        self,
        targets: list[InspectionTarget],
        message: str,
        command: list[str],
        file_path: str,
    ) -> tuple[bool, bool]:
        if not self._tool_evidence or self._tool_evidence[-1].name != "pytest resource warnings":
            self._tool_evidence.append(
                ToolEvidence(
                    name="pytest resource warnings",
                    path=command[0],
                    argv=command,
                    error=message,
                )
            )
        else:
            self._tool_evidence[-1].error = message
        required = bool(self.get_config(self.CONFIG_SECTION).get("required", True))
        if required:
            self._tool_errors.append(message)
            status = EngineStatus.ERROR
        else:
            self._skipped_scopes += 1
            status = EngineStatus.SKIP
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=1,
                target_name="PythonResourceWarnings",
                status=status,
                message=message,
            )
        )
        return False, False

    def _resource_warning_targets(self, output: str, targets: list[InspectionTarget]) -> bool:
        found = False
        for line in output.splitlines():
            match = _RESOURCE_WARNING_RE.search(line)
            if match is None:
                continue
            found = True
            path = self._normalize_output_path(match.group("file").strip())
            targets.append(
                InspectionTarget(
                    file_path=path,
                    start_line=int(match.group("line")),
                    target_name="ResourceWarning",
                    status=EngineStatus.FAIL,
                    message="ResourceWarning was promoted to an exception by the sanitizer",
                )
            )
        return found

    def _normalize_output_path(self, value: str) -> str:
        path = Path(value)
        try:
            return str(path.relative_to(self.project_root))
        except ValueError:
            return value

    @staticmethod
    def _pytest_has_executed_result(output: str) -> bool:
        return any(int(match.group("count")) > 0 for match in _PYTEST_EXECUTED_RE.finditer(output))

    @staticmethod
    def _pytest_module_missing(output: str, returncode: int) -> bool:
        return returncode != 0 and bool(
            re.search(r"No module named ['\"]pytest['\"]|No module named pytest", output)
        )
