"""Deep-profile quality observations for the unit-test engine."""

from __future__ import annotations

import math
import shutil
from typing import Any

from ici.core.models import (
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
)
from ici.core.runner import ProcessResult
from ici.engines.test_output import pytest_node_location

_MAX_QUALITY_RUNS = 3
_MAX_SLOW_TESTS = 1000
_QUALITY_TIMEOUT_DEFAULT = 300.0
_QUALITY_SLOW_THRESHOLD_DEFAULT = 1.0
_QUALITY_SLOW_TESTS_DEFAULT = 50
_MUTATION_TOOLS = ("mutmut", "cosmic-ray", "mutpy")


def empty_quality_info() -> dict[str, Any]:
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


class TestQualityMixin:
    """Provide bounded, opt-in deep-profile pytest quality observations."""

    __test__ = False

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

    def _deep_profile_selected(self) -> bool:
        """Return true only for the immutable context's deep profile."""

        return self.analysis_context is not None and self.analysis_context.profile == "deep"  # type: ignore[attr-defined]

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

        raw = self.get_config("test").get("quality", {})  # type: ignore[attr-defined]
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
            if not isinstance(mutation_tool, str) or mutation_tool not in {
                "auto",
                *_MUTATION_TOOLS,
            }:
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

        mode_value = raw.get("mode", "report")
        return {
            "enabled": enabled,
            "mode": mode_value
            if isinstance(mode_value, str) and mode_value in {"report", "warn"}
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
        self._last_pytest_output = output  # type: ignore[attr-defined]
        self._last_pytest_outcomes = self._parse_pytest_outcomes(output)  # type: ignore[attr-defined]

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
        """Run opt-in, bounded Python quality observations for deep analysis."""

        info = empty_quality_info()
        if self.analysis_context is not None:  # type: ignore[attr-defined]
            info["profile"] = self.analysis_context.profile  # type: ignore[attr-defined]
        if not self._deep_profile_selected() or proj_type not in ("python", "hybrid"):
            return info

        settings = self._quality_config()
        if not settings["enabled"] or not self._python_test_attempted:  # type: ignore[attr-defined]
            return info
        info["enabled"] = True
        info["mode"] = settings["mode"]

        threshold = float(settings["slow_test_threshold"])
        max_slow_tests = int(settings["max_slow_tests"])
        if threshold > 0 and self._last_pytest_output:  # type: ignore[attr-defined]
            slow_rows, slow_observed = self._parse_pytest_duration_summary(  # type: ignore[attr-defined]
                self._last_pytest_output,  # type: ignore[attr-defined]
                self.project_root,  # type: ignore[attr-defined]
                threshold=threshold,
                max_items=max_slow_tests,
            )
            info["slow_tests_observed"] = slow_observed
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
        base_outcomes_available = bool(self._last_pytest_outcomes)  # type: ignore[attr-defined]
        if base_outcomes_available:
            outcome_runs.append(dict(self._last_pytest_outcomes))  # type: ignore[attr-defined]
        elif repeat_runs > 1:
            info["repeat_unavailable"] += 1
            targets.append(
                InspectionTarget(
                    file_path="tests",
                    start_line=1,
                    target_name="[test-quality] Base run outcomes",
                    status=EngineStatus.WARN,
                    message=(
                        "base pytest run had no per-test outcome evidence; "
                        "flaky comparison is unavailable"
                    ),
                    metrics={"run": 1},
                )
            )

        if repeat_runs > 1:
            for run_number in range(2, repeat_runs + 1):
                result = self._run_quality_pytest(  # type: ignore[attr-defined]
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
                outcomes = self._parse_pytest_outcomes(  # type: ignore[attr-defined]
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

        if base_outcomes_available and outcome_runs:
            info["repeat_cases"] = len(set().union(*(run.keys() for run in outcome_runs)))
            self._append_flaky_targets(outcome_runs, targets, info)

        mutation = self._probe_mutation_capability(settings, targets)
        info.update(mutation)
        self._quality_info = info  # type: ignore[attr-defined]
        return info

    def _run_quality_pytest(
        self,
        run_number: int,
        *,
        timeout: float,
        include_durations: bool,
    ) -> ProcessResult:
        command = [*self._resolve_python(), "-m", "pytest", "-o", "addopts=", "-v"]  # type: ignore[attr-defined]
        if include_durations:
            command.extend(["--durations=0"])
        command.append("tests")
        result = self._run_test_process(  # type: ignore[attr-defined]
            command,
            cwd=self.project_root,  # type: ignore[attr-defined]
            env=self._build_python_test_env(),  # type: ignore[attr-defined]
            timeout=timeout,
            max_output_chars=1_000_000,
        )
        self._record_tool(f"pytest repeat {run_number}", command, result)  # type: ignore[attr-defined]
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
            file_path, start_line = pytest_node_location(nodeid, self.project_root)  # type: ignore[attr-defined]
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

        probe = self._run_test_process(  # type: ignore[attr-defined]
            command,
            cwd=self.project_root,  # type: ignore[attr-defined]
            timeout=float(settings["timeout"]),
            max_output_chars=8192,
        )
        self._record_tool("mutation capability", command, probe)  # type: ignore[attr-defined]
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
