"""3. Unit Test Execution, Coverage Measurement & TEM Scoring Engine."""

from __future__ import annotations

import contextlib
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ici.core.cmake import ConfigureOptions, gcov_json_capability, select_backend
from ici.core.cmake import build as adapter_build
from ici.core.cmake import collect_coverage as adapter_collect_coverage
from ici.core.cmake import configure as adapter_configure
from ici.core.cmake import run_tests as adapter_run_tests
from ici.core.context import ArtifactManifest, BuildVariant
from ici.core.env import (
    find_uv,  # noqa: F401 - retained for callers patching the legacy probe
    get_nas_cpp_lib_dir,
)
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine
from ici.engines.coverage_support import (
    calculate_tem,
    compute_python_function_coverage,
    module_unavailable,
    parse_coverage_json,
    parse_gcov_dir,
    parse_gcov_functions,
    parse_gcov_json_dir,
)
from ici.engines.cpp_text import defines_main
from ici.engines.test_coverage import TestCoverageMixin
from ici.engines.test_interpreter import TestInterpreterMixin
from ici.engines.test_output import TestOutputMixin
from ici.engines.test_quality import TestQualityMixin
from ici.engines.test_quality import empty_quality_info as _empty_quality_info

if TYPE_CHECKING:
    from ici.core.context import AnalysisContext


class TestEngine(
    TestCoverageMixin, TestQualityMixin, TestOutputMixin, TestInterpreterMixin, BaseEngine
):
    """Executes unit tests and calculates TEM score based on branch & function coverage."""

    __test__ = False
    # Test outcomes, timings, flaky reruns, and mutation availability are
    # runtime observations rather than source-derived facts.
    CACHE_REUSE_SAFE = False
    CACHE_IMPLEMENTATION_MODULES = (
        "ici.engines.test",
        "ici.engines.test_coverage",
        "ici.engines.test_interpreter",
        "ici.engines.test_output",
        "ici.engines.test_quality",
    )

    def __init__(
        self,
        project_root: Path | None = None,
        config: dict[str, Any] | None = None,
        analysis_context: AnalysisContext | None = None,
    ) -> None:
        super().__init__(project_root, config, analysis_context)
        self._coverage_data: dict | None = None
        self._cpp_coverage_rows: list[dict] = []
        self._cpp_function_rows: list[dict] = []
        self._function_rows: list[dict] = []
        self._coverage_files: list[dict] = []
        self._coverage_totals: dict | None = None
        self._coverage_source = "estimated"
        self._coverage_provenance: dict[str, Any] = {}
        self._coverage_policy: dict[str, Any] = {}
        self._tool_errors: list[str] = []
        self._coverage_errors: list[str] = []
        self._tool_evidence: list[ToolEvidence] = []
        self._artifact_manifests: list[ArtifactManifest] = []
        self._coverage_measured = False
        self._python_test_attempted = False
        self._cpp_test_attempted = False
        self._last_pytest_output = ""
        self._last_pytest_outcomes: dict[str, str] = {}
        self._quality_info = _empty_quality_info()
        self._has_run = False

    def _reset_run_state(self) -> None:
        """Discard every run-specific measurement before a new execution."""

        self._coverage_data = None
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []
        self._function_rows = []
        self._coverage_files = []
        self._coverage_totals = None
        self._coverage_source = "estimated"
        self._coverage_provenance = {}
        self._coverage_policy = {}
        self._tool_errors = []
        self._coverage_errors = []
        self._tool_evidence = []
        self._artifact_manifests = []
        self._coverage_measured = False
        self._python_test_attempted = False
        self._cpp_test_attempted = False
        self._last_pytest_output = ""
        self._last_pytest_outcomes = {}
        self._quality_info = _empty_quality_info()

    def run(self) -> EngineResult:
        t0 = time.time()
        if self._has_run:
            self._reset_run_state()
        else:
            self._tool_errors = []
            self._coverage_errors = []
            self._tool_evidence = []
        proj_type = self.project_type()
        targets: list[InspectionTarget] = []
        passed_tests, total_tests, has_failure = self._run_project_tests(proj_type, targets)
        quality_info = self._run_deep_test_quality(proj_type, targets)
        quality_targets = [target for target in targets if self._is_quality_target(target)]
        cfg = self.get_config("test")
        skipped_tests = sum(
            int(target.metrics.get("test_cases", 1))
            for target in targets
            if target.status == EngineStatus.SKIP and not self._is_quality_target(target)
        )
        no_tests_executed = total_tests > 0 and not has_failure and skipped_tests >= total_tests
        branch_cov, func_cov, coverage_targets = self._measure_coverage(proj_type, has_failure)
        targets.extend(coverage_targets)
        for target in coverage_targets:
            if target.status == EngineStatus.ERROR:
                self._record_tool_error(target.message)
        required_coverage_missing, optional_coverage_warning = self._apply_coverage_policy(cfg)
        tem_info = self._calc_tem(branch_cov, func_cov, passed_tests, total_tests)
        tem_score = tem_info["tem_score"]
        threshold_breaches = self._threshold_breaches(
            cfg, optional_coverage_warning, has_failure, tem_score, branch_cov, func_cov
        )
        targets.extend(threshold_breaches)
        quality_warn = quality_info["mode"] == "warn" and any(
            target.status == EngineStatus.WARN for target in quality_targets
        )
        coverage_policy_warn = any(
            target.status == EngineStatus.WARN and bool(target.metrics.get("gated"))
            for target in coverage_targets
        )
        has_warn = (
            bool(threshold_breaches)
            or optional_coverage_warning
            or quality_warn
            or coverage_policy_warn
        )
        test_suites = self._build_test_suites(targets)
        overall_status = self._result_status(
            cfg,
            has_failure,
            has_warn,
            optional_coverage_warning,
            required_coverage_missing,
            no_tests_executed,
        )
        summary = self._result_summary(
            passed_tests,
            total_tests,
            func_cov,
            tem_score,
            tem_info,
            optional_coverage_warning,
            branch_cov=branch_cov,
            no_tests_executed=no_tests_executed,
        )
        duration = time.time() - t0
        line_cov = tem_info["line_coverage"]
        pass_rate = tem_info["pass_rate"]
        cov_label = tem_info["cov_label"]
        cov_shown = tem_info["cov_shown"]
        self._has_run = True
        evidence = self._result_evidence(
            optional_coverage_warning,
            required_coverage_missing,
            no_tests_executed,
            bool(cfg.get("required", True)),
        )
        result = self.create_result(
            name="test",
            status=overall_status,
            summary=summary,
            score=tem_score,
            max_score=5.0,
            duration=duration,
            # Keep quality observations in the established target/evidence
            # contract. ``findings_for_result`` canonicalizes the native
            # quality finding and replaces the matching legacy target adapter,
            # so reporters still receive one issue per observation.
            targets=targets,
            extra={
                "passed_tests": passed_tests,
                "total_tests": total_tests,
                "skipped_tests": skipped_tests,
                "pass_rate": pass_rate,
                "branch_coverage": branch_cov,
                "function_coverage": func_cov,
                "line_coverage": line_cov,
                "tem_score": tem_score,
                "test_suites": test_suites,
                "coverage_files": self._coverage_files,
                "coverage_source": self._coverage_source,
                "coverage_provenance": self._coverage_provenance,
                "coverage_policy": self._coverage_policy or None,
                "coverage_totals": self._coverage_totals,
                "function_rows": self._function_rows,
                # Keep quality counters both grouped and flat. The grouped
                # object is convenient for API consumers; flat fields retain
                # the established metrics style used by reporters and CI.
                "test_quality": quality_info,
                "quality_enabled": bool(quality_info["enabled"]),
                "repeat_runs": int(quality_info["repeat_runs"]),
                "repeat_reruns": int(quality_info["repeat_reruns"]),
                "repeat_cases": int(quality_info["repeat_cases"]),
                "repeat_unavailable": int(quality_info["repeat_unavailable"]),
                "repeat_timeouts": int(quality_info["repeat_timeouts"]),
                "flaky_tests": int(quality_info["flaky_tests"]),
                "slow_tests": int(quality_info["slow_tests"]),
                "slow_tests_observed": int(quality_info["slow_tests_observed"]),
                "slow_test_inventory": list(quality_info["slow_test_inventory"]),
                "flaky_test_inventory": list(quality_info["flaky_test_inventory"]),
                "mutation_probes": int(quality_info["mutation_probes"]),
                "mutation_available": int(quality_info["mutation_available"]),
                "mutation_unavailable": int(quality_info["mutation_unavailable"]),
                "mutation_status": str(quality_info["mutation_status"]),
                "metrics_summary": (
                    f"TEM: {tem_score:.2f}/5.0 ({cov_label}: {cov_shown:.0f}%, "
                    f"Func: {func_cov:.0f}%, PassRate: {pass_rate:.0%})"
                ),
            },
            required=bool(cfg.get("required", True)),
            evidence=evidence,
            tool_evidence=self._tool_evidence,
            artifact_manifests=self._artifact_manifests,
        )
        result.findings = self._quality_findings(quality_targets)
        return result

    def _run_project_tests(
        self, proj_type: str, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        tests_root = self.project_root / "tests"
        py_tests = list(tests_root.rglob("test_*.py")) if tests_root.exists() else []
        cpp_tests = list(tests_root.rglob("*.cpp")) if tests_root.exists() else []
        py_sources = self.project_python_sources()
        cpp_sources = self.project_cpp_sources()
        totals = [0, 0]
        has_failure = False
        if py_tests or (proj_type in ("python", "hybrid") and py_sources):
            self._python_test_attempted = True
            result = (
                self._run_python_tests(targets)
                if py_tests or tests_root.exists()
                else self._mark_zero_tests("Python", targets)
            )
            totals[0] += result[0]
            totals[1] += result[1]
            has_failure = has_failure or result[2]
        if cpp_tests or (proj_type in ("cpp", "hybrid") and cpp_sources):
            self._cpp_test_attempted = True
            result = (
                self._run_cpp_tests(targets) if cpp_tests else self._mark_zero_tests("C++", targets)
            )
            totals[0] += result[0]
            totals[1] += result[1]
            has_failure = has_failure or result[2]
        if not self._python_test_attempted and not self._cpp_test_attempted:
            has_failure = has_failure or self._mark_zero_tests("Project", targets)[2]
        return totals[0], totals[1], has_failure

    def _apply_coverage_policy(self, cfg: dict[str, Any]) -> tuple[bool, bool]:
        missing = self._coverage_failure_messages()
        required = bool(cfg.get("coverage_required", False)) and bool(missing)
        optional = bool(missing) and not required
        if required:
            for message in missing:
                self._record_tool_error(message)
        return required, optional

    def _result_status(
        self,
        cfg: dict[str, Any],
        has_failure: bool,
        has_warn: bool,
        optional: bool,
        required: bool,
        no_tests_executed: bool,
    ) -> EngineStatus:
        if self._tool_errors or required:
            return EngineStatus.ERROR
        if no_tests_executed:
            return EngineStatus.ERROR if bool(cfg.get("required", True)) else EngineStatus.SKIP
        if optional and not has_failure:
            return EngineStatus.WARN
        return self.evaluate_status(has_failure, has_warn, cfg.get("mode", "pass_fail"))

    def _result_summary(
        self,
        passed_tests: int,
        total_tests: int,
        func_cov: float,
        tem_score: float,
        tem_info: dict[str, Any],
        optional: bool,
        branch_cov: float = 0.0,
        no_tests_executed: bool = False,
    ) -> str:
        if self._tool_errors:
            return "; ".join(self._tool_errors[:3])
        if no_tests_executed:
            return f"0/{total_tests} Tests Executed; every collected test was skipped"
        summary = (
            f"{passed_tests}/{total_tests} Tests Passed | "
            f"{tem_info['cov_label']}: {tem_info['cov_shown']:.1f}%{tem_info['cov_suffix']}, "
            f"Func: {func_cov:.1f}% -> TEM: {tem_score:.2f} / 5.0"
        )
        # Branch coverage gates the result but is only shown when TEM happened to
        # be computed from it. A summary that omits the number the gate turned on
        # is how a FAIL could read as though everything passed.
        if tem_info["cov_label"] != "Branch":
            summary = summary.replace(" -> TEM:", f", Branch: {branch_cov:.1f}% -> TEM:", 1)
        if optional:
            summary += "; Coverage evidence ESTIMATED (optional; not threshold evidence)"
        return summary

    def _result_evidence(
        self,
        optional: bool,
        required: bool,
        no_tests_executed: bool,
        engine_required: bool,
    ) -> EvidenceState:
        if self._tool_errors or required:
            return EvidenceState.NOT_RUN
        if no_tests_executed:
            return EvidenceState.NOT_RUN if engine_required else EvidenceState.ESTIMATED
        return EvidenceState.ESTIMATED if optional else EvidenceState.MEASURED

    def _mark_zero_tests(
        self, language: str, targets: list[InspectionTarget]
    ) -> tuple[int, int, bool]:
        targets.append(
            InspectionTarget(
                file_path="tests",
                start_line=1,
                target_name=f"[{language}] Tests",
                status=EngineStatus.FAIL,
                message="No tests collected",
            )
        )
        return 0, 0, True

    def _coverage_failure_messages(self) -> list[str]:
        failures = list(self._coverage_errors)
        if not self._python_test_attempted and not self._cpp_test_attempted:
            failures.append("No project sources or tests were available")
        if self._python_test_attempted and not self._coverage_data:
            failures.append("Python coverage evidence was unavailable or malformed")
        if self._cpp_test_attempted and not self._cpp_coverage_rows:
            failures.append("C++ gcov coverage evidence was unavailable or malformed")
        return list(dict.fromkeys(failures))

    def _record_tool(self, name: str, argv: list[str], result) -> None:
        self._tool_evidence.append(
            ToolEvidence(
                name=name,
                path=argv[0],
                argv=argv,
                returncode=result.returncode,
                timed_out=bool(getattr(result, "timed_out", False)),
                truncated=bool(getattr(result, "truncated", False)),
            )
        )

    def _run_test_process(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        max_output_chars: int | None = None,
    ) -> ProcessResult:
        """Keep mixin probes on the same patchable, bounded process runner."""

        if env is None and timeout is None and max_output_chars is None:
            return run_process(argv, cwd=cwd)
        return run_process(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            max_output_chars=(max_output_chars if max_output_chars is not None else 1_000_000),
        )

    def _coverage_process(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None = None,
    ) -> ProcessResult:
        """Keep extracted coverage calls on the legacy patchable runner binding."""

        if env is None:
            return run_process(argv, cwd=cwd)
        return run_process(argv, cwd=cwd, env=env)

    def _record_tool_error(self, message: str) -> None:
        if message not in self._tool_errors:
            self._tool_errors.append(message)

    def _calc_tem(
        self, branch_cov: float, func_cov: float, passed_tests: int, total_tests: int
    ) -> dict[str, Any]:
        return calculate_tem(branch_cov, func_cov, passed_tests, total_tests, self._coverage_totals)

    def _run_python_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        python_cmd = self._resolve_python()
        pytest_cmd = [*python_cmd, "-m", "pytest"]
        env = self._build_python_test_env()
        coverage_result = self._run_coverage_tests(python_cmd, env, targets)
        if coverage_result is not None:
            return coverage_result
        pytest_result = self._run_plain_pytest(pytest_cmd, env, targets)
        if pytest_result is not None:
            return pytest_result
        return self._run_unittest(env, targets, python_cmd)

    def _run_plain_pytest(
        self,
        pytest_cmd: list[str],
        env: dict[str, str],
        targets: list[InspectionTarget],
    ) -> tuple[int, int, bool] | None:
        command = [
            *pytest_cmd,
            "-o",
            "addopts=",
            "-v",
            *self._pytest_duration_args(),
            "tests",
        ]
        result = run_process(command, cwd=self.project_root, env=env)
        self._record_tool("pytest", command, result)
        self._remember_pytest_output(result)
        if result.timed_out:
            self._record_tool_error("Pytest timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Pytest output was truncated")
            return 0, 0, False
        if result.returncode == -1:
            self._record_tool_error("Pytest executable was unavailable")
            return 0, 0, False
        if result.returncode < 0:
            self._record_tool_error("Pytest process terminated before reporting results")
            return 0, 0, False
        if self._module_unavailable(result, "pytest"):
            return None
        parsed = self._parse_pytest_result(result, targets)
        return parsed

    @staticmethod
    def _module_unavailable(result, module: str) -> bool:
        return module_unavailable(result, module)

    def _run_unittest(
        self,
        env: dict[str, str],
        targets: list[InspectionTarget],
        python_cmd: list[str] | None = None,
    ) -> tuple[int, int, bool]:
        interpreter = python_cmd or self._resolve_python()
        command = [*interpreter, "-m", "unittest", "discover", "-s", "tests", "-v"]
        result = run_process(command, cwd=self.project_root, env=env)
        self._record_tool("unittest", command, result)
        if result.timed_out:
            self._record_tool_error("Unittest timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Unittest output was truncated")
            return 0, 0, False
        if result.returncode == -1:
            self._record_tool_error("Unittest executable was unavailable")
            return 0, 0, False
        passed, total, has_failure = self._parse_unittest_stdout(result, targets)
        if result.returncode not in (0, 1):
            self._record_tool_error(f"Unittest failed with exit code {result.returncode}")
        elif total == 0:
            has_failure = True
        return passed, total, has_failure

    def _parse_coverage_json(
        self, json_path: Path, expected_files: set[str] | None = None
    ) -> dict | None:
        return parse_coverage_json(json_path, self.project_root, expected_files)

    def _run_cpp_tests(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        if select_backend(self.project_root, self.config).kind is not None:
            return self._run_cpp_tests_via_adapter(targets)
        gxx = shutil.which("g++")
        if not gxx:
            self._record_tool_error("g++ executable was unavailable")
            return 0, 0, False
        gcov_bin = shutil.which("gcov")

        passed = 0
        total = 0
        has_failure = False
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []

        inc_flags = self.project_cpp_include_flags()
        # Only sources ici can build itself: anything under
        # project.cpp_external_build_dirs (Qt widgets needing moc, CMake-driven
        # code) is still analysed by the other engines but cannot be linked here.
        src_files = [str(f) for f in self.project_compilable_cpp_sources() if not defines_main(f)]
        src_rel_set = {str(Path(f).relative_to(self.project_root)) for f in src_files}
        nas_cpp = get_nas_cpp_lib_dir()
        lib_flags = []
        if nas_cpp.exists() and (nas_cpp / "lib").exists():
            lib_flags = [f"-L{nas_cpp / 'lib'}", "-lips_core", f"-Wl,-rpath,{nas_cpp / 'lib'}"]

        build_tmp = self.project_root / "build/tests"
        build_tmp.mkdir(parents=True, exist_ok=True)
        for suffix in ("*.gcno", "*.gcda", "*.gcov", "*.gcov.json.gz"):
            for coverage_file in build_tmp.glob(suffix):
                with contextlib.suppress(OSError):
                    coverage_file.unlink()
        use_coverage = gcov_bin is not None and bool(src_files)
        if not gcov_bin:
            self._coverage_errors.append("gcov executable was unavailable")
        elif not src_files:
            self._coverage_errors.append("C++ source files for gcov were unavailable")

        cpp_tests = list((self.project_root / "tests").rglob("*.cpp"))
        for test_src in cpp_tests:
            total += 1
            case_passed, case_failed = self._run_cpp_test_case(
                gxx,
                inc_flags,
                src_files,
                lib_flags,
                build_tmp,
                test_src,
                use_coverage,
                targets,
            )
            passed += case_passed
            has_failure = has_failure or case_failed

        if use_coverage and gcov_bin:
            self._collect_cpp_coverage(gcov_bin, build_tmp, src_rel_set)

        return passed, total, has_failure

    def _resolve_test_source(self, test_name: str) -> str:
        """Map a CTest/QtTest name onto a file, as AGENTS.md 5-1 requires.

        Neither ctest nor QtTest reports the source file, so the stem is matched
        against tests/. When nothing matches, the build descriptor is the most
        specific location that is still true.
        """

        stem = test_name.split("::")[0]
        tests_root = self.project_root / "tests"
        if tests_root.is_dir():
            for candidate in sorted(tests_root.rglob("*.cpp")):
                if candidate.stem == stem:
                    return str(candidate.relative_to(self.project_root))
        return select_backend(self.project_root, self.config).descriptor or "."

    def _run_cpp_tests_via_adapter(self, targets: list[InspectionTarget]) -> tuple[int, int, bool]:
        self._cpp_coverage_rows = []
        self._cpp_function_rows = []

        session = adapter_configure(
            self.project_root,
            ConfigureOptions(BuildVariant.COVERAGE),
            self.config,
        )
        session.analysis_context = self.analysis_context

        if not session.configured:
            self._tool_evidence.extend(session.tool_evidence)
            messages = session.errors or [
                f"{session.backend} configure did not complete and reported no reason"
            ]
            for message in messages:
                self._record_tool_error(message)
            self._coverage_errors.append("C++ build was not configured")
            return 0, 0, False

        if not adapter_build(session):
            self._tool_evidence.extend(session.tool_evidence)
            self._coverage_errors.append("C++ build failed before tests could run")
            messages = session.errors or ["C++ build failed and reported no reason"]
            for message in messages:
                targets.append(
                    InspectionTarget(
                        file_path=session.descriptor or ".",
                        start_line=1,
                        target_name="[C++] build",
                        status=EngineStatus.FAIL,
                        message=message,
                    )
                )
            return 0, 0, True

        if session.artifact_manifest is not None:
            self._artifact_manifests.append(session.artifact_manifest)

        results = adapter_run_tests(session)

        passed = 0
        has_failure = False
        for case in results:
            relative = self._resolve_test_source(case.name)
            if not case.executed:
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=f"[C++] {case.name}",
                        status=EngineStatus.SKIP,
                        message=f"Execution skipped: {case.message or 'no reason reported'}",
                    )
                )
            elif case.passed:
                passed += 1
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=f"[C++] {case.name}",
                        status=EngineStatus.PASS,
                        message="C++ Test Passed",
                    )
                )
            else:
                has_failure = True
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=f"[C++] {case.name}",
                        status=EngineStatus.FAIL,
                        message=f"Execution Failed: {case.message}",
                    )
                )

        # gcov only after the tests ran: .gcda does not exist until then.
        gcov_dir = (
            adapter_collect_coverage(session) if any(case.executed for case in results) else None
        )
        if results and not any(case.executed for case in results):
            self._coverage_errors.append("C++ tests were collected but not executed")
        # One copy at the end, covering configure, build, ctest and gcov.
        self._tool_evidence.extend(session.tool_evidence)
        if gcov_dir is None:
            for message in session.errors:
                self._record_tool_error(message)
            self._coverage_errors.append("C++ gcov coverage output was missing or malformed")
        else:
            # cpp_external_build_dirs does not apply here — the build system
            # links everything — but entry points still do not. The g++ path
            # keeps main() out of the test link, so it never reached gcov;
            # counting it here would drop a project's coverage the moment it
            # moved to CMake, for code that did not change.
            sources = {
                str(path.relative_to(self.project_root))
                for path in self.project_cpp_sources()
                if not defines_main(path)
            }
            self._consume_cpp_coverage(gcov_dir, sources, session.coverage_format)
            if self._cpp_coverage_rows:
                self._coverage_measured = True
            else:
                self._coverage_errors.append("C++ gcov coverage output was missing or malformed")

        return passed, len(results), has_failure

    def _run_cpp_test_case(
        self,
        gxx: str,
        inc_flags: list[str],
        src_files: list[str],
        lib_flags: list[str],
        build_tmp: Path,
        test_src: Path,
        use_coverage: bool,
        targets: list[InspectionTarget],
    ) -> tuple[int, bool]:
        runner_bin = build_tmp / test_src.stem
        relative = str(test_src.relative_to(self.project_root))
        target_name = f"[C++] {test_src.name}"
        if use_coverage:
            ok, objects, compile_error = self._compile_cpp_objects(
                gxx, inc_flags, src_files, str(test_src), build_tmp
            )
            if not ok:
                self._coverage_errors.append("C++ gcov coverage compilation failed")
                tool_error = bool(self._tool_errors)
                targets.append(
                    InspectionTarget(
                        file_path=relative,
                        start_line=1,
                        target_name=target_name,
                        status=EngineStatus.ERROR if tool_error else EngineStatus.FAIL,
                        message=(
                            "Compiler process terminated before reporting results"
                            if tool_error
                            else f"Compilation Error: {compile_error[:200]}"
                        ),
                    )
                )
                return 0, not tool_error
            compile_cmd = [
                gxx,
                "--coverage",
                "-std=c++17",
                *(str(obj) for obj in objects),
                *lib_flags,
                "-o",
                str(runner_bin),
            ]
            compile_result = run_process(compile_cmd, cwd=build_tmp)
        else:
            compile_cmd = [
                gxx,
                "-std=c++17",
                *inc_flags,
                str(test_src),
                *src_files,
                *lib_flags,
                "-o",
                str(runner_bin),
            ]
            compile_result = run_process(compile_cmd, cwd=self.project_root)

        self._record_tool("g++ test compile", compile_cmd, compile_result)
        if compile_result.timed_out or compile_result.truncated:
            self._record_tool_error(f"C++ test compilation incomplete: {test_src.name}")
            self._append_cpp_tool_target(
                relative, target_name, targets, "Compilation output was incomplete"
            )
            return 0, False
        if compile_result.returncode < 0:
            self._record_tool_error(
                f"C++ test compilation terminated before reporting results: {test_src.name}"
            )
            self._append_cpp_tool_target(
                relative,
                target_name,
                targets,
                "Compiler process terminated before reporting results",
            )
            return 0, False
        if compile_result.returncode != 0:
            if use_coverage:
                self._coverage_errors.append("C++ gcov coverage compilation failed")
            targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name=target_name,
                    status=EngineStatus.FAIL,
                    message=f"Compilation Error: {compile_result.stderr[:200]}",
                )
            )
            return 0, True

        run_cmd = [str(runner_bin)]
        # Always the project root. This used to be build/tests when coverage was
        # enabled, so the same test binary saw a different working directory
        # depending on whether gcov happened to be installed — a test that opens
        # a fixture by relative path passed on one machine and failed on another.
        #
        # Nothing is lost by fixing it: gcov records the object's absolute path
        # at compile time, so .gcda files land beside the objects no matter where
        # the binary runs from.
        run_result = run_process(run_cmd, cwd=self.project_root)
        self._record_tool("C++ test", run_cmd, run_result)
        if run_result.timed_out or run_result.truncated:
            self._record_tool_error(f"C++ test execution incomplete: {test_src.name}")
            self._append_cpp_tool_target(
                relative, target_name, targets, "Execution output was incomplete"
            )
            return 0, False
        if run_result.returncode < 0:
            self._record_tool_error(
                f"C++ test process terminated before reporting results: {test_src.name}"
            )
            self._append_cpp_tool_target(
                relative, target_name, targets, "Test process terminated before reporting results"
            )
            return 0, False
        if run_result.returncode == 0:
            targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name=target_name,
                    status=EngineStatus.PASS,
                    message="C++ Test Passed",
                )
            )
            return 1, False
        targets.append(
            InspectionTarget(
                file_path=relative,
                start_line=1,
                target_name=target_name,
                status=EngineStatus.FAIL,
                message=f"Execution Failed: {run_result.stdout or run_result.stderr}",
            )
        )
        return 0, True

    @staticmethod
    def _append_cpp_tool_target(
        relative: str,
        target_name: str,
        targets: list[InspectionTarget],
        message: str,
    ) -> None:
        targets.append(
            InspectionTarget(
                file_path=relative,
                start_line=1,
                target_name=target_name,
                status=EngineStatus.ERROR,
                message=message,
            )
        )

    def _parse_gcov_dir(self, cov_dir: Path, source_files: set[str]) -> list[dict]:
        return parse_gcov_dir(cov_dir, source_files, self.project_root)

    def _compute_python_function_coverage(self, cov_data: dict) -> list[dict]:
        return compute_python_function_coverage(
            cov_data,
            self.project_root,
            self.config,
            self.project_python_sources(),
        )

    def _parse_gcov_functions(self, cov_dir: Path, source_files: set[str]) -> list[dict]:
        return parse_gcov_functions(cov_dir, source_files, self.project_root)

    def _parse_gcov_json_dir(
        self, cov_dir: Path, source_files: set[str]
    ) -> tuple[list[dict], list[dict], dict[str, Any]]:
        return parse_gcov_json_dir(cov_dir, source_files, self.project_root)

    @staticmethod
    def _gcov_json_capability(result: ProcessResult) -> bool | None:
        return gcov_json_capability(result)
