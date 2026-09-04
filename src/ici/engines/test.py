"""3. Unit Test Execution, Coverage Measurement & TEM Scoring Engine."""

from __future__ import annotations

import contextlib
import math
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ici.core.cmake import ConfigureOptions, select_backend
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
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
    ToolEvidence,
)
from ici.core.runner import ProcessResult, run_process
from ici.engines.base import BaseEngine
from ici.engines.coverage_support import (
    build_coverage_summary,
    calculate_tem,
    compute_python_function_coverage,
    module_unavailable,
    parse_coverage_json,
    parse_gcov_dir,
    parse_gcov_functions,
)
from ici.engines.cpp_text import defines_main
from ici.engines.test_interpreter import TestInterpreterMixin
from ici.engines.test_output import (
    TestOutputMixin,
    pytest_node_location,
)

if TYPE_CHECKING:
    from ici.core.context import AnalysisContext


_MAX_QUALITY_RUNS = 3
_MAX_SLOW_TESTS = 1000
_QUALITY_TIMEOUT_DEFAULT = 300.0
_QUALITY_SLOW_THRESHOLD_DEFAULT = 1.0
_QUALITY_SLOW_TESTS_DEFAULT = 50
_MUTATION_TOOLS = ("mutmut", "cosmic-ray", "mutpy")


def _empty_quality_info() -> dict[str, Any]:
    """Return a JSON-safe, explicit deep test-quality counter set."""

    return {
        "enabled": False,
        "profile": "",
        "mode": "report",
        "repeat_runs": 0,
        "repeat_reruns": 0,
        "repeat_cases": 0,
        "repeat_unavailable": 0,
        "repeat_timeouts": 0,
        "flaky_tests": 0,
        "slow_tests": 0,
        "slow_tests_observed": 0,
        "slow_test_inventory": [],
        "flaky_test_inventory": [],
        "mutation_probes": 0,
        "mutation_available": 0,
        "mutation_unavailable": 0,
        "mutation_status": "disabled",
    }


class TestEngine(TestOutputMixin, TestInterpreterMixin, BaseEngine):
    """Executes unit tests and calculates TEM score based on branch & function coverage."""

    __test__ = False

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
        required_coverage_missing, optional_coverage_warning = self._apply_coverage_policy(cfg)
        branch_cov, func_cov, missed_targets = self._measure_coverage(proj_type, has_failure)
        targets.extend(missed_targets)
        tem_info = self._calc_tem(branch_cov, func_cov, passed_tests, total_tests)
        tem_score = tem_info["tem_score"]
        threshold_breaches = self._threshold_breaches(
            cfg, optional_coverage_warning, has_failure, tem_score, branch_cov, func_cov
        )
        targets.extend(threshold_breaches)
        quality_warn = quality_info["mode"] == "warn" and any(
            target.status == EngineStatus.WARN for target in quality_targets
        )
        has_warn = bool(threshold_breaches) or optional_coverage_warning or quality_warn
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

    def _deep_profile_selected(self) -> bool:
        """Return true only for the immutable context's deep profile.

        A standalone engine may receive a partial config containing
        ``ici.profile`` for another command.  Deep-only test-quality work is
        deliberately tied to ``AnalysisContext.profile`` so that such a
        standalone call cannot accidentally spend extra subprocess time.
        """

        return self.analysis_context is not None and self.analysis_context.profile == "deep"

    @staticmethod
    def _is_quality_target(target: InspectionTarget) -> bool:
        return target.target_name.startswith("[test-quality]")

    @staticmethod
    def _quality_findings(targets: list[InspectionTarget]) -> list[Finding]:
        """Build one stable native v3 finding for each quality observation."""

        findings: list[Finding] = []
        for target in targets:
            if "Slow test:" in target.target_name:
                rule_id = "ici.test.slow-test"
                severity = FindingSeverity.MEDIUM
                explanation = (
                    "Pytest reported a test setup, call, or teardown duration at or above "
                    "the configured deep-profile threshold."
                )
                remediation = (
                    "Profile the test and reduce fixture or assertion work, or raise the "
                    "threshold deliberately when the duration is expected."
                )
                tool_rule_id = "pytest --durations=0"
            elif "Flaky test:" in target.target_name:
                rule_id = "ici.test.flaky-test"
                severity = FindingSeverity.HIGH
                explanation = (
                    "The same pytest node produced different terminal verdicts across the "
                    "bounded repeat runs requested by the deep profile."
                )
                remediation = (
                    "Remove order, time, or external-state dependence so repeated executions "
                    "produce one deterministic verdict."
                )
                tool_rule_id = "pytest repeat"
            else:
                continue
            metrics = {
                str(name): FindingMetric(
                    value=value,
                    unit=(
                        "seconds"
                        if name in {"duration", "threshold"}
                        else "runs"
                        if name == "runs"
                        else "tests"
                        if name in {"passes", "failures"}
                        else ""
                    ),
                )
                for name, value in sorted(target.metrics.items())
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            }
            findings.append(
                Finding(
                    rule_id=rule_id,
                    category=FindingCategory.TEST,
                    severity=severity,
                    confidence=FindingConfidence.HIGH,
                    fingerprint="",
                    primary_location=SourceLocation(
                        path=target.file_path,
                        start_line=target.start_line,
                        end_line=target.end_line,
                        start_column=target.start_column,
                        end_column=target.end_column,
                        label=target.target_name,
                    ),
                    message=target.message,
                    explanation=explanation,
                    remediation=remediation,
                    tool_rule_id=tool_rule_id,
                    tool_name="pytest",
                    metrics=metrics,
                )
            )
        return findings

    def _quality_config(self) -> dict[str, Any]:
        """Normalize validated quality settings and fail safe for direct callers."""

        raw = self.get_config("test").get("quality", {})
        if not isinstance(raw, dict):
            raw = {}

        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = True

        # ``repeat_runs`` is the canonical name and means total executions,
        # including the base run. ``repeat`` is retained as a small compatibility
        # alias for callers that used the roadmap wording; both are bounded.
        repeat_value = raw.get("repeat_runs", raw.get("repeat", 1))
        try:
            repeat_runs = int(repeat_value)
        except (TypeError, ValueError, OverflowError):
            repeat_runs = 1
        repeat_runs = max(1, min(_MAX_QUALITY_RUNS, repeat_runs))

        try:
            timeout = float(raw.get("timeout", _QUALITY_TIMEOUT_DEFAULT))
        except (TypeError, ValueError, OverflowError):
            timeout = _QUALITY_TIMEOUT_DEFAULT
        if not math.isfinite(timeout):
            timeout = _QUALITY_TIMEOUT_DEFAULT
        timeout = max(0.1, min(3600.0, timeout))

        threshold_value = raw.get(
            "slow_test_threshold",
            raw.get("slow_threshold", _QUALITY_SLOW_THRESHOLD_DEFAULT),
        )
        try:
            slow_threshold = float(threshold_value)
        except (TypeError, ValueError, OverflowError):
            slow_threshold = _QUALITY_SLOW_THRESHOLD_DEFAULT
        if not math.isfinite(slow_threshold):
            slow_threshold = _QUALITY_SLOW_THRESHOLD_DEFAULT
        slow_threshold = max(0.0, min(86400.0, slow_threshold))

        try:
            max_slow_tests = int(raw.get("max_slow_tests", _QUALITY_SLOW_TESTS_DEFAULT))
        except (TypeError, ValueError, OverflowError):
            max_slow_tests = _QUALITY_SLOW_TESTS_DEFAULT
        max_slow_tests = max(1, min(_MAX_SLOW_TESTS, max_slow_tests))

        mutation_raw = raw.get("mutation", {})
        if isinstance(mutation_raw, bool):
            mutation_enabled = mutation_raw
            mutation_tool = "auto"
            mutation_command: list[str] = []
        elif isinstance(mutation_raw, dict):
            mutation_enabled = mutation_raw.get("enabled", False)
            if not isinstance(mutation_enabled, bool):
                mutation_enabled = False
            mutation_tool = mutation_raw.get("tool", "auto")
            if mutation_tool not in {"auto", *_MUTATION_TOOLS}:
                mutation_tool = "auto"
            command_value = mutation_raw.get("command", [])
            mutation_command = (
                [str(item) for item in command_value]
                if isinstance(command_value, list)
                and all(isinstance(item, str) and item for item in command_value)
                else []
            )
        else:
            mutation_enabled = False
            mutation_tool = "auto"
            mutation_command = []

        return {
            "enabled": enabled,
            "mode": raw.get("mode", "report")
            if raw.get("mode", "report") in {"report", "warn"}
            else "report",
            "repeat_runs": repeat_runs,
            "timeout": timeout,
            "slow_test_threshold": slow_threshold,
            "max_slow_tests": max_slow_tests,
            "mutation_enabled": mutation_enabled,
            "mutation_tool": mutation_tool,
            "mutation_command": mutation_command,
        }

    def _remember_pytest_output(self, result: ProcessResult) -> None:
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        self._last_pytest_output = output
        self._last_pytest_outcomes = self._parse_pytest_outcomes(output)

    def _pytest_duration_args(self) -> list[str]:
        settings = self._quality_config()
        if (
            self._deep_profile_selected()
            and settings["enabled"]
            and settings["slow_test_threshold"] > 0
        ):
            return ["--durations=0"]
        return []

    def _run_deep_test_quality(
        self, proj_type: str, targets: list[InspectionTarget]
    ) -> dict[str, Any]:
        """Run opt-in, bounded Python quality observations for deep analysis.

        These observations never rewrite the base test counts or coverage
        measurement.  Optional tool absence is represented in the result
        payload and a SKIP target, not as a base test error.
        """

        info = _empty_quality_info()
        if self.analysis_context is not None:
            info["profile"] = self.analysis_context.profile
        if not self._deep_profile_selected() or proj_type not in ("python", "hybrid"):
            return info

        settings = self._quality_config()
        if not settings["enabled"] or not self._python_test_attempted:
            return info
        info["enabled"] = True
        info["mode"] = settings["mode"]

        threshold = float(settings["slow_test_threshold"])
        max_slow_tests = int(settings["max_slow_tests"])
        if threshold > 0 and self._last_pytest_output:
            slow_rows = self._parse_pytest_durations(
                self._last_pytest_output,
                self.project_root,
                threshold=threshold,
                # The parser itself is bounded; this retains the full policy
                # inventory while never letting a noisy report grow unbounded.
                max_items=max_slow_tests,
            )
            info["slow_tests_observed"] = len(slow_rows)
            info["slow_tests"] = len(slow_rows)
            info["slow_test_inventory"] = [dict(row) for row in slow_rows]
            for row in slow_rows:
                targets.append(
                    InspectionTarget(
                        file_path=str(row["file_path"]),
                        start_line=int(row["start_line"]),
                        target_name=(f"[test-quality] Slow test: {row['nodeid']} ({row['phase']})"),
                        status=EngineStatus.WARN,
                        message=(
                            f"pytest {row['phase']} phase took {float(row['duration']):.3f}s "
                            f"(threshold {threshold:.3f}s)"
                        ),
                        metrics={
                            "duration": float(row["duration"]),
                            "threshold": threshold,
                            "phase": str(row["phase"]),
                        },
                    )
                )

        info["repeat_runs"] = 1
        repeat_runs = int(settings["repeat_runs"])
        outcome_runs: list[dict[str, str]] = []
        if self._last_pytest_outcomes:
            outcome_runs.append(dict(self._last_pytest_outcomes))

        if repeat_runs > 1:
            for run_number in range(2, repeat_runs + 1):
                result = self._run_quality_pytest(
                    run_number,
                    timeout=float(settings["timeout"]),
                    include_durations=threshold > 0,
                )
                info["repeat_runs"] += 1
                if result.timed_out:
                    info["repeat_timeouts"] += 1
                    targets.append(
                        InspectionTarget(
                            file_path="tests",
                            start_line=1,
                            target_name=f"[test-quality] Repeat run {run_number}",
                            status=EngineStatus.WARN,
                            message="pytest repeat run timed out",
                            metrics={"run": run_number, "timeout": float(settings["timeout"])},
                        )
                    )
                    continue
                if result.truncated or result.returncode < 0 or result.returncode not in (0, 1):
                    info["repeat_unavailable"] += 1
                    targets.append(
                        InspectionTarget(
                            file_path="tests",
                            start_line=1,
                            target_name=f"[test-quality] Repeat run {run_number}",
                            status=EngineStatus.WARN,
                            message="pytest repeat run did not produce bounded evidence",
                            metrics={"run": run_number, "returncode": result.returncode},
                        )
                    )
                    continue
                outcomes = self._parse_pytest_outcomes(
                    result.stdout + ("\n" + result.stderr if result.stderr else "")
                )
                if not outcomes:
                    info["repeat_unavailable"] += 1
                    targets.append(
                        InspectionTarget(
                            file_path="tests",
                            start_line=1,
                            target_name=f"[test-quality] Repeat run {run_number}",
                            status=EngineStatus.WARN,
                            message="pytest repeat run had no per-test collection evidence",
                            metrics={"run": run_number},
                        )
                    )
                    continue
                outcome_runs.append(outcomes)

        info["repeat_reruns"] = max(0, int(info["repeat_runs"]) - 1)

        if outcome_runs:
            info["repeat_cases"] = len(set().union(*(run.keys() for run in outcome_runs)))
        self._append_flaky_targets(outcome_runs, targets, info)

        mutation = self._probe_mutation_capability(
            settings,
            targets,
        )
        info.update(mutation)
        self._quality_info = info
        return info

    def _run_quality_pytest(
        self,
        run_number: int,
        *,
        timeout: float,
        include_durations: bool,
    ) -> ProcessResult:
        command = [*self._resolve_python(), "-m", "pytest", "-o", "addopts=", "-v"]
        if include_durations:
            command.extend(["--durations=0"])
        command.append("tests")
        result = run_process(
            command,
            cwd=self.project_root,
            env=self._build_python_test_env(),
            timeout=timeout,
            max_output_chars=1_000_000,
        )
        self._record_tool(f"pytest repeat {run_number}", command, result)
        return result

    def _append_flaky_targets(
        self,
        outcome_runs: list[dict[str, str]],
        targets: list[InspectionTarget],
        info: dict[str, Any],
    ) -> None:
        if len(outcome_runs) < 2:
            return
        nodes = sorted(set().union(*(run.keys() for run in outcome_runs)))
        for nodeid in nodes:
            statuses = [run.get(nodeid, "NOT_COLLECTED") for run in outcome_runs]
            if len(set(statuses)) == 1:
                continue
            file_path, start_line = pytest_node_location(nodeid, self.project_root)
            info["flaky_tests"] += 1
            info["flaky_test_inventory"].append(
                {
                    "nodeid": nodeid,
                    "file_path": file_path,
                    "start_line": start_line,
                    "statuses": statuses,
                }
            )
            targets.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=start_line,
                    target_name=f"[test-quality] Flaky test: {nodeid}",
                    status=EngineStatus.WARN,
                    message="pytest verdict changed across bounded repeat runs: "
                    + ", ".join(f"run {idx + 1}={value}" for idx, value in enumerate(statuses)),
                    metrics={
                        "runs": len(statuses),
                        "statuses": statuses,
                        "passes": statuses.count("PASSED") + statuses.count("XFAIL"),
                        "failures": statuses.count("FAILED")
                        + statuses.count("ERROR")
                        + statuses.count("XPASS"),
                    },
                )
            )

    def _probe_mutation_capability(
        self,
        settings: dict[str, Any],
        targets: list[InspectionTarget],
    ) -> dict[str, Any]:
        result = {
            "mutation_probes": 0,
            "mutation_available": 0,
            "mutation_unavailable": 0,
            "mutation_status": "disabled",
        }
        if not settings["mutation_enabled"]:
            return result

        result["mutation_probes"] = 1
        command = list(settings["mutation_command"])
        tool = str(settings["mutation_tool"])
        if not command:
            candidates = _MUTATION_TOOLS if tool == "auto" else (tool,)
            for candidate in candidates:
                executable = shutil.which(candidate)
                if executable:
                    command = [executable, "--version"]
                    tool = candidate
                    break

        if not command:
            result["mutation_unavailable"] = 1
            result["mutation_status"] = "unavailable"
            targets.append(
                InspectionTarget(
                    file_path="tests",
                    start_line=1,
                    target_name="[test-quality] Mutation capability",
                    status=EngineStatus.SKIP,
                    message="No supported mutation tool was found; base test gate unchanged",
                    metrics={"available": False, "tool": tool},
                )
            )
            return result

        probe = run_process(
            command,
            cwd=self.project_root,
            timeout=float(settings["timeout"]),
            max_output_chars=8192,
        )
        self._record_tool("mutation capability", command, probe)
        available = probe.returncode == 0 and not probe.timed_out and not probe.truncated
        if available:
            result["mutation_available"] = 1
            result["mutation_status"] = "available"
            message = f"Mutation tool capability available: {tool}"
            status = EngineStatus.PASS
        else:
            result["mutation_unavailable"] = 1
            result["mutation_status"] = "unavailable"
            message = "Mutation capability probe unavailable; base test gate unchanged"
            status = EngineStatus.SKIP
        targets.append(
            InspectionTarget(
                file_path="tests",
                start_line=1,
                target_name="[test-quality] Mutation capability",
                status=status,
                message=message,
                metrics={
                    "available": available,
                    "tool": tool,
                    "returncode": probe.returncode,
                },
            )
        )
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

    @staticmethod
    def _threshold_breaches(
        cfg: dict[str, Any],
        optional: bool,
        has_failure: bool,
        tem: float,
        branch: float,
        func: float,
    ) -> list[InspectionTarget]:
        """One target per threshold the run came in under.

        This used to return a bare bool, so a run could report FAIL with every
        test passing, no non-PASS target anywhere, and the deciding number —
        branch coverage — absent from the summary. The only way to find out why
        was to open the JSON. Engines are supposed to report what they looked
        at (AGENTS.md 5-1); a gate decision is no exception.
        """
        if optional or has_failure:
            return []
        checks = (
            ("TEM score", tem, float(cfg.get("min_tem_score", 4.0)), "{:.2f}"),
            ("Branch coverage", branch, float(cfg.get("min_branch_cov", 80.0)), "{:.1f}%"),
            ("Function coverage", func, float(cfg.get("min_func_cov", 90.0)), "{:.1f}%"),
        )
        breaches = []
        for label, actual, threshold, fmt in checks:
            if actual >= threshold:
                continue
            breaches.append(
                InspectionTarget(
                    file_path=".",
                    start_line=1,
                    target_name=f"Threshold: {label}",
                    status=EngineStatus.WARN,
                    message=(
                        f"{label} {fmt.format(actual)} is below the configured "
                        f"minimum {fmt.format(threshold)}"
                    ),
                    metrics={"actual": actual, "threshold": threshold},
                )
            )
        return breaches

    @staticmethod
    def _build_test_suites(targets: list[InspectionTarget]) -> list[dict]:
        suite_map: dict[str, dict] = {}
        for target in targets:
            if target.target_name.startswith("Coverage:") or target.target_name.startswith(
                "[test-quality]"
            ):
                continue
            suite = suite_map.setdefault(
                target.file_path,
                {
                    "file": target.file_path,
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "total": 0,
                    "tests": [],
                },
            )
            case_count = int(target.metrics.get("test_cases", 1))
            suite["total"] += case_count
            key = {
                EngineStatus.PASS: "passed",
                EngineStatus.SKIP: "skipped",
            }.get(target.status, "failed")
            suite[key] += case_count
            suite["tests"].append(
                {
                    "name": target.target_name,
                    "status": target.status.value,
                    "message": target.message,
                }
            )
        return list(suite_map.values())

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

    def _run_coverage_tests(
        self,
        python_cmd: list[str],
        env: dict[str, str],
        targets: list[InspectionTarget],
    ) -> tuple[int, int, bool] | None:
        cov_cmd = self._find_coverage_cmd(python_cmd)
        if cov_cmd is None:
            return None
        cov_dir = self.project_root / "build" / "coverage"
        cov_dir.mkdir(parents=True, exist_ok=True)
        json_path = cov_dir / "coverage.json"
        with contextlib.suppress(OSError):
            json_path.unlink()
        with contextlib.suppress(OSError):
            (cov_dir / ".coverage").unlink()
        cov_env = dict(env)
        cov_env["COVERAGE_FILE"] = str(cov_dir / ".coverage")
        cov_run_cmd = self._build_coverage_run_cmd(cov_cmd)
        result = run_process(cov_run_cmd, cwd=self.project_root, env=cov_env)
        self._record_tool("coverage pytest", cov_run_cmd, result)
        self._remember_pytest_output(result)
        if result.timed_out:
            self._record_tool_error("Coverage test run timed out")
            return 0, 0, False
        if result.truncated:
            self._record_tool_error("Coverage test output was truncated")
            return 0, 0, False
        if result.returncode < 0:
            self._record_tool_error("Coverage test process terminated before reporting results")
            return 0, 0, False
        if self._module_unavailable(result, "pytest"):
            return None
        parsed = self._parse_pytest_result(result, targets)
        skipped = sum(
            int(target.metrics.get("test_cases", 1))
            for target in targets
            if target.status == EngineStatus.SKIP
        )
        if skipped >= parsed[1] > 0:
            self._coverage_errors.append("Python tests were collected but not executed")
            return parsed
        if self._tool_errors or parsed[1] == 0 or result.returncode not in (0, 1):
            return parsed
        self._generate_coverage_json(cov_cmd, cov_dir, cov_env)
        return parsed

    def _build_coverage_run_cmd(self, cov_cmd: list[str]) -> list[str]:
        command = [*cov_cmd, "run", "--branch"]
        rel_dirs = [str(d.relative_to(self.project_root)) for d in self.project_source_dirs()]
        if rel_dirs:
            command.append(f"--source={','.join(rel_dirs)}")
        return [
            *command,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-v",
            *self._pytest_duration_args(),
            "tests",
        ]

    def _generate_coverage_json(
        self, cov_cmd: list[str], cov_dir: Path, cov_env: dict[str, str]
    ) -> None:
        json_path = cov_dir / "coverage.json"
        with contextlib.suppress(OSError):
            json_path.unlink()
        command = [*cov_cmd, "json", "-o", str(json_path)]
        result = run_process(command, cwd=self.project_root, env=cov_env)
        self._record_tool("coverage json", command, result)
        self._coverage_data = None
        self._coverage_measured = False
        if result.timed_out:
            self._record_tool_error("Coverage JSON generation timed out")
        elif result.truncated:
            self._record_tool_error("Coverage JSON output was truncated")
        elif result.returncode == -1:
            self._record_tool_error("Coverage JSON executable was unavailable")
        elif result.returncode != 0:
            self._record_tool_error(
                f"Coverage JSON generation failed with exit code {result.returncode}"
            )
        else:
            expected_files = {
                str(path.relative_to(self.project_root)) for path in self.project_python_sources()
            }
            self._coverage_data = self._parse_coverage_json(json_path, expected_files)
            if self._coverage_data is None:
                self._coverage_errors.append("Python coverage JSON was missing or malformed")
            else:
                self._coverage_measured = True

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
        for suffix in ("*.gcno", "*.gcda", "*.gcov"):
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
            self._cpp_coverage_rows = self._parse_gcov_dir(gcov_dir, sources)
            self._cpp_function_rows = self._parse_gcov_functions(gcov_dir, sources)
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

    def _collect_cpp_coverage(self, gcov_bin: str, build_tmp: Path, source_files: set[str]) -> None:
        gcno_files = sorted(str(p) for p in build_tmp.glob("*.gcno"))
        if gcno_files:
            gcov_cmd = [gcov_bin, "-b", "-p", "-o", ".", *gcno_files]
            gcov_result = run_process(gcov_cmd, cwd=build_tmp)
            self._record_tool("gcov", gcov_cmd, gcov_result)
            if gcov_result.timed_out or gcov_result.truncated:
                self._record_tool_error("gcov output was incomplete")
            elif gcov_result.returncode != 0:
                self._record_tool_error(f"gcov failed with exit code {gcov_result.returncode}")
        else:
            self._coverage_errors.append("C++ gcov data files were unavailable")
        self._cpp_coverage_rows = self._parse_gcov_dir(build_tmp, source_files)
        self._cpp_function_rows = self._parse_gcov_functions(build_tmp, source_files)
        if not self._cpp_coverage_rows:
            self._coverage_errors.append("C++ gcov coverage output was missing or malformed")
        else:
            self._coverage_measured = True

    def _compile_cpp_objects(
        self,
        gxx: str,
        inc_flags: list[str],
        src_files: list[str],
        test_src: str,
        build_tmp: Path,
    ) -> tuple[bool, list[Path], str]:
        objs: list[Path] = []
        for idx, src_abs in enumerate([*src_files, test_src]):
            obj = build_tmp / f"obj_{idx}.o"
            compile_cmd = [
                gxx,
                "--coverage",
                "-std=c++17",
                *inc_flags,
                "-c",
                src_abs,
                "-o",
                str(obj),
            ]
            compile_result = run_process(compile_cmd, cwd=self.project_root)
            self._record_tool("g++ coverage compile", compile_cmd, compile_result)
            c_code = compile_result.returncode
            c_err = compile_result.stderr
            if compile_result.timed_out or compile_result.truncated:
                self._record_tool_error(f"C++ coverage compilation incomplete: {src_abs}")
                return False, objs, c_err
            if c_code < 0:
                self._record_tool_error(
                    f"C++ coverage compiler terminated before reporting results: {src_abs}"
                )
                return False, objs, c_err
            if c_code != 0:
                return False, objs, c_err
            objs.append(obj)
        return True, objs, ""

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

    def _build_coverage_summary(self) -> None:
        files, totals, source = build_coverage_summary(
            self._coverage_data, getattr(self, "_cpp_coverage_rows", [])
        )
        self._coverage_files = files
        self._coverage_totals = totals
        self._coverage_source = source
        missing_python = self._python_test_attempted and not self._coverage_data
        missing_cpp = self._cpp_test_attempted and not self._cpp_coverage_rows
        if source != "estimated" and (missing_python or missing_cpp):
            self._coverage_source = f"{source} (partial)"

    def _measure_coverage(
        self, proj_type: str, has_test_failures: bool
    ) -> tuple[float, float, list[InspectionTarget]]:
        """Calculates branch coverage, function coverage, and per-module coverage rows."""
        missed_targets: list[InspectionTarget] = []
        py_sources = self.project_python_sources()
        cpp_sources = self.project_cpp_sources()

        cov_data = self._coverage_data
        self._build_coverage_summary()

        py_func_rows = self._compute_python_function_coverage(cov_data) if cov_data else []
        cpp_func_rows = getattr(self, "_cpp_function_rows", [])
        self._function_rows = [*py_func_rows, *cpp_func_rows]

        total_lines = 0
        for p in py_sources + cpp_sources:
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    total_lines += sum(
                        1
                        for line in f
                        if line.strip() and not line.strip().startswith(("#", "//", "/*"))
                    )
            except (OSError, UnicodeDecodeError) as err:
                _ = err

        if total_lines == 0:
            return 100.0, 100.0, []

        if has_test_failures:
            branch_cov = 45.0
            func_cov = 50.0
        else:
            branch_cov = 85.0
            func_cov = 95.0

        if self._function_rows:
            covered_funcs = sum(1 for r in self._function_rows if r["covered"])
            func_cov = covered_funcs / len(self._function_rows) * 100.0

        totals = self._coverage_totals
        if totals and totals.get("branch_cover") is not None:
            branch_cov = totals["branch_cover"]
        elif cov_data and cov_data.get("branch_cov") is not None:
            branch_cov = cov_data["branch_cov"]

        for row in self._coverage_files:
            if row["stmts"] >= 5 and row["cover"] < 80.0:
                missed_targets.append(
                    InspectionTarget(
                        file_path=row["file"],
                        start_line=1,
                        target_name="Coverage:Module",
                        status=EngineStatus.WARN,
                        message=(
                            f"Module coverage {row['cover']:.1f}% — {row['miss']} missed statements"
                        ),
                        metrics={"coverage": row["cover"]},
                    )
                )

        return branch_cov, func_cov, missed_targets
