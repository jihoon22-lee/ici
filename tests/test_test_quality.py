"""Focused contracts for deep-profile Python test-quality observations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ici.config_schema import ConfigError, validate_config
from ici.core.findings import findings_for_result
from ici.core.models import EngineStatus
from ici.core.runner import ProcessResult
from ici.engines.test import TestEngine
from ici.engines.test_output import parse_pytest_durations, parse_pytest_outcomes


def _deep_engine(tmp_path: Path, quality: dict | None = None) -> TestEngine:
    config = {"engines": {"test": {"quality": quality or {}}}}
    engine = TestEngine(tmp_path, config)
    # The production orchestrator supplies a full immutable AnalysisContext.
    # The focused unit contracts only need its profile identity.
    engine.analysis_context = SimpleNamespace(profile="deep")
    return engine


def test_parse_pytest_durations_is_sorted_bounded_and_source_aware(tmp_path: Path):
    output = (
        "============================= slowest 4 durations =============================\n"
        "1.20s call     tests/test_slow.py:18::test_later\n"
        "2.50s call     tests/test_slow.py::test_first\n"
        "0.02s setup    tests/test_fast.py::test_fast\n"
        "2.50s call     tests/test_slow.py::test_first\n"
    )

    rows = parse_pytest_durations(output, tmp_path, threshold=0.1, max_items=2)

    assert [row["nodeid"] for row in rows] == [
        "tests/test_slow.py::test_first",
        "tests/test_slow.py:18::test_later",
    ]
    assert rows[0]["file_path"] == "tests/test_slow.py"
    assert rows[0]["start_line"] == 1
    assert rows[1]["start_line"] == 18
    assert rows[0]["duration"] == 2.5


def test_duration_observed_count_precedes_inventory_cap_and_nonfinite_is_rejected(
    tmp_path: Path,
):
    engine = _deep_engine(
        tmp_path,
        {"slow_test_threshold": 0.1, "max_slow_tests": 1},
    )
    engine._python_test_attempted = True
    engine._last_pytest_output = (
        "3.0s call tests/test_slow.py::test_three\n"
        "2.0s call tests/test_slow.py::test_two\n"
        "1.0s call tests/test_slow.py::test_one\n"
        + "9" * 400
        + "s call tests/test_slow.py::test_invalid\n"
    )

    info = engine._run_deep_test_quality("python", [])

    assert info["slow_tests_observed"] == 3
    assert info["slow_tests"] == 1
    assert len(info["slow_test_inventory"]) == 1
    assert info["slow_test_inventory"][0]["duration"] == 3.0


def test_test_engine_runtime_observations_are_not_cache_reusable():
    assert TestEngine.CACHE_REUSE_SAFE is False


def test_parse_pytest_outcomes_replaces_duplicate_report_with_last_verdict():
    output = (
        "tests/test_flaky.py::test_toggle PASSED\n"
        "tests/test_flaky.py::test_toggle FAILED\n"
        "tests/test_ok.py::test_ok XFAIL (known)\n"
    )

    assert parse_pytest_outcomes(output) == {
        "tests/test_flaky.py::test_toggle": "FAILED",
        "tests/test_ok.py::test_ok": "XFAIL",
    }


def test_quality_repeat_is_capped_at_three_total_runs_and_reports_flaky_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = _deep_engine(tmp_path, {"repeat_runs": 99, "slow_test_threshold": 0})
    engine._python_test_attempted = True
    engine._last_pytest_outcomes = {"tests/test_flaky.py:7::test_toggle": "PASSED"}
    repeat_results = iter(
        [
            ProcessResult(
                1,
                "tests/test_flaky.py:7::test_toggle FAILED\n",
                "",
                0.01,
            ),
            ProcessResult(
                0,
                "tests/test_flaky.py:7::test_toggle PASSED\n",
                "",
                0.01,
            ),
        ]
    )
    calls: list[int] = []

    def fake_repeat(run_number: int, **_kwargs) -> ProcessResult:
        calls.append(run_number)
        return next(repeat_results)

    monkeypatch.setattr(engine, "_run_quality_pytest", fake_repeat)

    targets = []
    info = engine._run_deep_test_quality("python", targets)

    assert calls == [2, 3]
    assert info["repeat_runs"] == 3
    assert info["repeat_reruns"] == 2
    assert info["repeat_cases"] == 1
    assert info["flaky_tests"] == 1
    flaky = next(target for target in targets if "Flaky test" in target.target_name)
    assert flaky.status is EngineStatus.WARN
    assert flaky.file_path == "tests/test_flaky.py"
    assert flaky.start_line == 7


def test_quality_is_deep_context_only_and_default_has_no_repeat_or_mutation_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = TestEngine(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(engine, "_run_quality_pytest", lambda *_args, **_kwargs: calls.append(1))

    assert engine._pytest_duration_args() == []
    info = engine._run_deep_test_quality("python", [])

    assert info["enabled"] is False
    assert info["repeat_runs"] == 0
    assert info["mutation_probes"] == 0
    assert calls == []


def test_mutation_unavailable_is_a_skip_without_tool_error_or_gate_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = _deep_engine(tmp_path, {"mutation": {"enabled": True, "tool": "mutmut"}})
    monkeypatch.setattr("ici.engines.test.shutil.which", lambda _name: None)
    targets = []

    info = engine._probe_mutation_capability(engine._quality_config(), targets)

    assert info == {
        "mutation_probes": 1,
        "mutation_available": 0,
        "mutation_unavailable": 1,
        "mutation_status": "unavailable",
    }
    assert engine._tool_errors == []
    assert len(targets) == 1
    assert targets[0].status is EngineStatus.SKIP
    assert "base test gate unchanged" in targets[0].message


@pytest.mark.parametrize("tool", [[], {}, 42, None])
def test_direct_malformed_mutation_tool_falls_back_safely(tmp_path: Path, tool: object):
    engine = _deep_engine(tmp_path, {"mutation": {"enabled": True, "tool": tool}})

    settings = engine._quality_config()

    assert settings["mutation_tool"] == "auto"


def test_quality_findings_are_native_and_report_mode_does_not_change_engine_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = _deep_engine(
        tmp_path,
        {
            "mode": "report",
            "repeat_runs": 2,
            "slow_test_threshold": 0.5,
            "max_slow_tests": 5,
        },
    )
    engine.analysis_context = SimpleNamespace(
        profile="deep",
        project=SimpleNamespace(root=tmp_path, project_type="python"),
    )

    def fake_project_tests(_project_type: str, _targets: list) -> tuple[int, int, bool]:
        engine._python_test_attempted = True
        engine._last_pytest_output = "0.75s call tests/test_slow.py:12::test_flaky\n"
        engine._last_pytest_outcomes = {"tests/test_slow.py:12::test_flaky": "PASSED"}
        return 1, 1, False

    monkeypatch.setattr(engine, "_run_project_tests", fake_project_tests)
    monkeypatch.setattr(engine, "_apply_coverage_policy", lambda _cfg: (False, False))
    monkeypatch.setattr(engine, "_measure_coverage", lambda *_args: (100.0, 100.0, []))
    monkeypatch.setattr(
        engine,
        "_run_quality_pytest",
        lambda *_args, **_kwargs: ProcessResult(
            1,
            "tests/test_slow.py:12::test_flaky FAILED\n",
            "",
            0.01,
        ),
    )

    result = engine.run()

    assert result.status is EngineStatus.PASS
    assert result.extra["test_quality"]["mode"] == "report"
    assert {
        target.target_name
        for target in result.targets
        if target.target_name.startswith("[test-quality]")
    } == {
        "[test-quality] Slow test: tests/test_slow.py:12::test_flaky (call)",
        "[test-quality] Flaky test: tests/test_slow.py:12::test_flaky",
    }
    assert {finding.rule_id for finding in result.findings} == {
        "ici.test.slow-test",
        "ici.test.flaky-test",
    }
    slow = next(finding for finding in result.findings if finding.rule_id == "ici.test.slow-test")
    flaky = next(finding for finding in result.findings if finding.rule_id == "ici.test.flaky-test")
    assert slow.primary_location.path == "tests/test_slow.py"
    assert slow.primary_location.start_line == 12
    assert slow.metrics["duration"].value == 0.75
    assert flaky.primary_location.start_line == 12
    projected = findings_for_result(result, tmp_path)
    assert len([finding for finding in projected if finding.rule_id.startswith("ici.test.")]) == 2


def test_quality_warn_mode_uses_normal_engine_mode_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = _deep_engine(
        tmp_path,
        {"mode": "warn", "repeat_runs": 2, "slow_test_threshold": 0.5},
    )
    engine.analysis_context = SimpleNamespace(
        profile="deep",
        project=SimpleNamespace(root=tmp_path, project_type="python"),
    )

    def fake_project_tests(_project_type: str, _targets: list) -> tuple[int, int, bool]:
        engine._python_test_attempted = True
        engine._last_pytest_outcomes = {"tests/test_flaky.py::test_toggle": "PASSED"}
        return 1, 1, False

    monkeypatch.setattr(engine, "_run_project_tests", fake_project_tests)
    monkeypatch.setattr(engine, "_apply_coverage_policy", lambda _cfg: (False, False))
    monkeypatch.setattr(engine, "_measure_coverage", lambda *_args: (100.0, 100.0, []))
    monkeypatch.setattr(
        engine,
        "_run_quality_pytest",
        lambda *_args, **_kwargs: ProcessResult(
            1,
            "tests/test_flaky.py::test_toggle FAILED\n",
            "",
            0.01,
        ),
    )

    result = engine.run()

    assert result.status is EngineStatus.FAIL
    assert result.extra["test_quality"]["mode"] == "warn"


def test_quality_schema_accepts_modes_and_rejects_unknown_mode():
    validate_config({"engines": {"test": {"quality": {"mode": "report"}}}})
    validate_config({"engines": {"test": {"quality": {"mode": "warn"}}}})
    with pytest.raises(ConfigError, match=r"engines\.test\.quality\.mode"):
        validate_config({"engines": {"test": {"quality": {"mode": "fail"}}}})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("repeat_runs", 4),
        ("timeout", 3601.0),
        ("slow_test_threshold", -0.1),
        ("max_slow_tests", 1001),
    ],
)
def test_quality_schema_rejects_unbounded_values(key: str, value: object):
    with pytest.raises(ConfigError, match=rf"engines\.test\.quality\.{key}"):
        validate_config({"engines": {"test": {"quality": {key: value}}}})


def test_quality_schema_is_strict_for_nested_mutation_settings():
    with pytest.raises(ConfigError, match=r"engines\.test\.quality\.mutation\.unknown"):
        validate_config(
            {"engines": {"test": {"quality": {"mutation": {"enabled": True, "unknown": True}}}}}
        )

    validate_config(
        {
            "engines": {
                "test": {
                    "quality": {
                        "enabled": True,
                        "repeat_runs": 3,
                        "timeout": 10.0,
                        "slow_test_threshold": 0.25,
                        "max_slow_tests": 20,
                        "mutation": {
                            "enabled": True,
                            "tool": "auto",
                            "command": ["mutmut", "--version"],
                        },
                    }
                }
            }
        }
    )
