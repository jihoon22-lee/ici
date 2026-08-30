"""6. Memory safety and runtime resource-warning verification."""

import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ici.core.cmake import ConfigureOptions, select_backend
from ici.core.cmake import build as adapter_build
from ici.core.cmake import configure as adapter_configure
from ici.core.cmake import run_tests as adapter_run_tests
from ici.core.env import get_nas_cpp_lib_dir
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.project import (
    _iter_project_files,
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
    get_all_python_sources,
    get_compilable_cpp_sources,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine
from ici.engines.cpp_text import defines_main

_PYTEST_EXECUTED_RE = re.compile(
    r"\b(?P<count>\d+)\s+(?:passed|failed|xfailed|xpassed)\b", re.IGNORECASE
)
_RESOURCE_WARNING_RE = re.compile(r"(?P<file>.*?\.py):(?P<line>[1-9]\d*):[^\n]*ResourceWarning")
_SANITIZER_ERROR_RE = re.compile(
    r"(?mi)^(?:==\d+==)?\s*ERROR:\s*(?:AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer)\b"
)
_SANITIZER_SUMMARY_RE = re.compile(
    r"(?mi)^\s*SUMMARY:\s*(?:AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer)\b"
)
_UBSAN_RUNTIME_RE = re.compile(r"(?m)^.*:\d+(?::\d+)?:\s*runtime error:\s*\S+")


class SanitizeEngine(BaseEngine):
    """Run C++ sanitizers and Python ResourceWarning checks with evidence."""

    def __init__(
        self, project_root: Path | None = None, config: dict[str, Any] | None = None
    ) -> None:
        super().__init__(project_root, config)
        self._tool_errors: list[str] = []
        self._tool_evidence: list[ToolEvidence] = []
        self._measured_scopes = 0
        self._skipped_scopes = 0
        self._required_scope_missing = False

    def run(self) -> EngineResult:
        t0 = time.time()
        self._tool_errors = []
        self._tool_evidence = []
        self._measured_scopes = 0
        self._skipped_scopes = 0
        self._required_scope_missing = False
        targets: list[InspectionTarget] = []
        proj_type = detect_project_type(self.project_root)
        cpp_sources = get_all_cpp_sources(self.project_root, self.config)
        cpp_tests = self._cpp_test_sources()
        py_sources = get_all_python_sources(self.project_root, self.config)
        tests_root = self.project_root / "tests"
        has_python_scope = bool(py_sources) or self._has_python_tests(tests_root)
        has_cpp_scope = bool(cpp_sources) or bool(cpp_tests)
        if proj_type in ("python", "hybrid") and (py_sources or tests_root.exists()):
            has_python_scope = True
        if proj_type in ("cpp", "hybrid") and (cpp_sources or tests_root.exists()):
            has_cpp_scope = has_cpp_scope or bool(cpp_tests)

        has_failure = False
        has_warning = False
        if has_cpp_scope:
            # Scope is decided by all C++ sources, but only compilable ones are
            # linked into the sanitizer binaries.
            has_failure = self._run_cpp_sanitizer(
                cpp_tests, get_compilable_cpp_sources(self.project_root, self.config), targets
            )
        if has_python_scope:
            py_failure, py_warning = self._check_python_resource_warnings(tests_root, targets)
            has_failure = has_failure or py_failure
            has_warning = has_warning or py_warning
        if not has_cpp_scope and not has_python_scope:
            self._mark_scope_skip(
                targets,
                ".",
                "Sanitize",
                "No applicable Python or C++ sources were selected; sanitize was not run",
                required=False,
            )

        cfg = self.get_config("sanitize")
        mode = cfg.get("mode", "pass_fail")
        required = bool(cfg.get("required", True))
        duration = time.time() - t0
        if self._tool_errors:
            overall_status = EngineStatus.ERROR
            evidence = EvidenceState.NOT_RUN
            summary = "; ".join(self._tool_errors[:3])
        elif self._measured_scopes and self._skipped_scopes:
            overall_status = (
                self.evaluate_status(has_failure, has_warning, mode)
                if has_failure
                else EngineStatus.WARN
            )
            evidence = EvidenceState.ESTIMATED
            summary = "Sanitize partially executed: one or more applicable scopes were skipped"
        elif self._measured_scopes:
            overall_status = self.evaluate_status(has_failure, has_warning, mode)
            evidence = EvidenceState.MEASURED
            summary = (
                "Memory Safety & Sanitize Clean (0 Defects)"
                if overall_status == EngineStatus.PASS
                else f"{self._issue_count(targets)} Memory / Resource Defect(s) Detected"
            )
        else:
            overall_status = EngineStatus.SKIP
            # Two different situations reach here, and only one of them is
            # "not applicable".
            #
            # A scope was in play but produced no measurement — tests that all
            # skipped, say — is a hole in verification and must keep blocking.
            # Nothing in scope at all, as in a C++ project with no tests, is not:
            # the test engine already reports the missing tests, and escalating
            # here too named sanitize as the gate's reason when the real one was
            # "this project has no tests".
            evidence = (
                EvidenceState.ESTIMATED if self._skipped_scopes else EvidenceState.NOT_APPLICABLE
            )
            summary = "Sanitize skipped: no applicable checks were executed"

        return self.create_result(
            name="sanitize",
            status=overall_status,
            summary=summary,
            duration=duration,
            targets=targets,
            extra={"sanitize_issues": self._issue_count(targets)},
            required=required,
            evidence=evidence,
            tool_evidence=self._tool_evidence,
        )

    def _cpp_test_sources(self) -> list[Path]:
        tests_root = self.project_root / "tests"
        if not tests_root.is_dir():
            return []
        return sorted(_iter_project_files(tests_root, self.project_root, (".cpp",)))

    @staticmethod
    def _has_python_tests(tests_root: Path) -> bool:
        return tests_root.is_dir() and any(tests_root.rglob("*.py"))

    def _run_cpp_sanitizer(
        self,
        cpp_tests: list[Path],
        cpp_sources: list[Path],
        targets: list[InspectionTarget],
    ) -> bool:
        if not cpp_tests:
            self._mark_scope_skip(
                targets,
                "tests",
                "C++Sanitizer",
                "No C++ sanitizer test sources were selected; compilation was not run",
                required=False,
            )
            return False

        if select_backend(self.project_root).kind is not None:
            return self._run_cpp_sanitizer_via_adapter(targets)

        gxx = shutil.which("g++")
        if not gxx:
            message = "g++ is required when C++ sanitizer tests are present"
            self._record_tool_missing("sanitizer compile", message)
            for test_src in cpp_tests:
                self._append_error_target(
                    targets, test_src, "SanitizerCompile", f"{message}; sanitizer was not run"
                )
            return False

        has_failure = False
        inc_flags = get_all_cpp_includes(self.project_root, self.config)
        src_files = [str(path) for path in cpp_sources if not defines_main(path)]
        lib_flags = self._cpp_library_flags()
        with tempfile.TemporaryDirectory(prefix="ici-sanitize-") as temp_name:
            temp_root = Path(temp_name)
            for test_src in cpp_tests:
                runner_bin = temp_root / f"{test_src.stem}_asan"
                command = [
                    gxx,
                    "-std=c++17",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-g",
                    *inc_flags,
                    str(test_src),
                    *src_files,
                    *lib_flags,
                    "-o",
                    str(runner_bin),
                ]
                try:
                    compile_result = run_process(command, cwd=temp_root)
                except Exception as exc:
                    self._record_tool_exception("sanitizer compile", command, exc)
                    self._append_error_target(
                        targets,
                        test_src,
                        "SanitizerCompile",
                        f"Sanitizer compilation failed: {exc}",
                    )
                    continue
                evidence = self._record_process("sanitizer compile", command, compile_result)
                if self._process_incomplete(compile_result):
                    message = self._incomplete_message("Sanitizer compilation", compile_result)
                    evidence.error = message
                    self._tool_errors.append(message)
                    self._append_error_target(targets, test_src, "SanitizerCompile", message)
                    continue
                if compile_result.returncode != 0:
                    message = self._tool_failure_message("Sanitizer compilation", compile_result)
                    evidence.error = message
                    self._tool_errors.append(message)
                    self._append_error_target(targets, test_src, "SanitizerCompile", message)
                    continue

                run_command = [str(runner_bin)]
                try:
                    run_result = run_process(
                        # The binary is built into a temp directory but runs from
                        # the project root, matching the test engine. A test that
                        # reads a fixture must not behave differently depending on
                        # which engine launched it.
                        run_command,
                        cwd=self.project_root,
                        env=self._sanitizer_environment(),
                    )
                except Exception as exc:
                    self._record_tool_exception("sanitizer execution", run_command, exc)
                    self._append_error_target(
                        targets,
                        test_src,
                        "SanitizerExecution",
                        f"Sanitizer execution failed: {exc}",
                    )
                    continue
                run_evidence = self._record_process("sanitizer execution", run_command, run_result)
                if self._process_incomplete(run_result, allow_signal=True):
                    message = self._incomplete_message("Sanitizer execution", run_result)
                    run_evidence.error = message
                    self._tool_errors.append(message)
                    self._append_error_target(targets, test_src, "SanitizerExecution", message)
                    continue
                output = f"{run_result.stderr}\n{run_result.stdout}"
                has_diagnostic = self._contains_sanitizer_diagnostic(output)
                if has_diagnostic:
                    self._measured_scopes += 1
                    has_failure = True
                    targets.append(
                        InspectionTarget(
                            file_path=str(test_src.relative_to(self.project_root)),
                            start_line=1,
                            target_name="ASan/UBSan Error",
                            status=EngineStatus.FAIL,
                            message=f"Memory/Runtime defect detected: {self._snippet(output)}",
                        )
                    )
                elif run_result.returncode != 0:
                    message = self._tool_failure_message("Sanitizer execution", run_result)
                    run_evidence.error = message
                    self._tool_errors.append(message)
                    self._append_error_target(targets, test_src, "SanitizerExecution", message)
                else:
                    self._measured_scopes += 1
                    targets.append(
                        InspectionTarget(
                            file_path=str(test_src.relative_to(self.project_root)),
                            start_line=1,
                            target_name="ASan/UBSan",
                            status=EngineStatus.PASS,
                            message="AddressSanitizer and UndefinedBehaviorSanitizer completed",
                        )
                    )
        return has_failure

    def _run_cpp_sanitizer_via_adapter(self, targets: list[InspectionTarget]) -> bool:
        """Build and run the project's own tests under the sanitizers.

        The generic g++ path cannot do this once any test uses Qt: it has no moc
        step and no way to link Qt Test, so a Q_OBJECT test fails to compile
        before a sanitizer ever runs. The adapter builds the same targets the
        test engine does, only with -fsanitize instead of --coverage.
        """

        options = ConfigureOptions(
            coverage=False,
            extra_cxx_flags=("-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-g"),
            extra_link_flags=("-fsanitize=address,undefined",),
            shadow_suffix="-asan",
        )
        session = adapter_configure(self.project_root, options)
        self._tool_evidence.extend(session.tool_evidence)

        if not session.configured:
            self._fail_adapter_scope(
                targets, session, session.errors or ["sanitizer configure reported no reason"]
            )
            return False

        if not adapter_build(session):
            self._tool_evidence.extend(session.tool_evidence)
            self._fail_adapter_scope(
                targets, session, session.errors or ["sanitizer build reported no reason"]
            )
            return False

        results = adapter_run_tests(session, env=self._sanitizer_environment())
        self._tool_evidence.extend(session.tool_evidence)
        if not results:
            self._fail_adapter_scope(
                targets,
                session,
                ["The build system reported no tests, so nothing ran under the sanitizers"],
            )
            return False

        has_failure = False
        for case in results:
            status = EngineStatus.PASS if case.passed else EngineStatus.FAIL
            has_failure = has_failure or not case.passed
            # Each executed binary is a measured scope. Without this the engine
            # reports "no applicable checks were executed" and skips, which is
            # the silent-gap shape the gate exists to catch.
            self._measured_scopes += 1
            targets.append(
                InspectionTarget(
                    file_path=session.descriptor or ".",
                    start_line=1,
                    target_name=f"[C++ ASan/UBSan] {case.name}",
                    status=status,
                    message="Sanitizers reported no diagnostics"
                    if case.passed
                    else f"Sanitizer run failed: {case.message}",
                )
            )
        return has_failure

    def _fail_adapter_scope(self, targets, session, messages: list[str]) -> None:
        """Record an adapter failure as an unmeasured scope, not an absent one.

        Appending an ERROR target is not enough on its own: the status logic
        reads _tool_errors, and a run with neither measured nor skipped scopes
        falls through to SKIP/NOT_APPLICABLE. A sanitizer build that failed
        would then be reported as "this engine does not apply here" — the
        inverse of the §3.2 rule that a scope which existed and was not
        measured has to keep blocking the gate.
        """

        for message in messages:
            self._append_scope_error(targets, session.descriptor or ".", "C++Sanitizer", message)
            if message not in self._tool_errors:
                self._tool_errors.append(message)

    def _cpp_library_flags(self) -> list[str]:
        nas_cpp = get_nas_cpp_lib_dir()
        if nas_cpp.exists() and (nas_cpp / "lib").exists():
            return [f"-L{nas_cpp / 'lib'}", "-lips_core", f"-Wl,-rpath,{nas_cpp / 'lib'}"]
        return []

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
        from ici.core.project import get_source_dirs

        return get_source_dirs(self.project_root, self.config)

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
        required = bool(self.get_config("sanitize").get("required", True))
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

    @staticmethod
    def _contains_sanitizer_diagnostic(output: str) -> bool:
        return bool(
            _SANITIZER_ERROR_RE.search(output)
            or _SANITIZER_SUMMARY_RE.search(output)
            or _UBSAN_RUNTIME_RE.search(output)
        )

    @staticmethod
    def _process_incomplete(result: ProcessResult, *, allow_signal: bool = False) -> bool:
        return bool(
            result.timed_out
            or result.truncated
            or not isinstance(result.returncode, int)
            or (result.returncode < 0 and not allow_signal)
        )

    def _record_process(self, name: str, command: list[str], result: ProcessResult) -> ToolEvidence:
        item = ToolEvidence(
            name=name,
            path=command[0],
            argv=command,
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )
        self._tool_evidence.append(item)
        return item

    def _record_tool_missing(self, name: str, message: str) -> None:
        self._tool_evidence.append(ToolEvidence(name=name, path="", error=message))
        self._tool_errors.append(message)

    def _record_tool_exception(self, name: str, command: list[str], exc: Exception) -> None:
        message = f"{type(exc).__name__}: {exc}"
        if any(
            evidence.name == name and evidence.argv == command and evidence.error == message
            for evidence in self._tool_evidence
        ):
            return
        self._tool_evidence.append(
            ToolEvidence(name=name, path=command[0], argv=command, error=message)
        )
        self._tool_errors.append(message)

    def _append_error_target(
        self, targets: list[InspectionTarget], source: Path, name: str, message: str
    ) -> None:
        self._append_scope_error(targets, str(source.relative_to(self.project_root)), name, message)

    @staticmethod
    def _append_scope_error(
        targets: list[InspectionTarget], file_path: str, name: str, message: str
    ) -> None:
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=1,
                target_name=name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )

    def _mark_scope_skip(
        self,
        targets: list[InspectionTarget],
        file_path: str,
        target_name: str,
        message: str,
        required: bool | None = None,
    ) -> None:
        self._skipped_scopes += 1
        if required is None:
            required = bool(self.get_config("sanitize").get("required", True))
        if required:
            self._required_scope_missing = True
            self._tool_errors.append(message)
            status = EngineStatus.ERROR
        else:
            status = EngineStatus.SKIP
        targets.append(
            InspectionTarget(
                file_path=file_path,
                start_line=1,
                target_name=target_name,
                status=status,
                message=message,
            )
        )

    @staticmethod
    def _append_option(existing: str, option: str) -> str:
        return ":".join(part for part in (existing, option) if part)

    @classmethod
    def _sanitizer_environment(cls) -> dict[str, str]:
        env = os.environ.copy()
        env["ASAN_OPTIONS"] = cls._append_option(env.get("ASAN_OPTIONS", ""), "detect_leaks=1")
        env["UBSAN_OPTIONS"] = cls._append_option(env.get("UBSAN_OPTIONS", ""), "halt_on_error=1")
        return env

    @staticmethod
    def _issue_count(targets: list[InspectionTarget]) -> int:
        return sum(
            1 for target in targets if target.status in (EngineStatus.WARN, EngineStatus.FAIL)
        )

    @staticmethod
    def _snippet(output: str) -> str:
        return " ".join(output.split())[:300]

    def _incomplete_message(self, label: str, result: ProcessResult) -> str:
        if result.timed_out:
            return f"{label} timed out: {self._snippet(result.stderr or result.stdout)}"
        if result.truncated:
            return f"{label} output was truncated: {self._snippet(result.stderr or result.stdout)}"
        return f"{label} terminated before producing a result: {self._snippet(result.stderr or result.stdout)}"

    def _tool_failure_message(self, label: str, result: ProcessResult) -> str:
        return f"{label} failed with exit code {result.returncode}: {self._snippet(result.stderr or result.stdout)}"
