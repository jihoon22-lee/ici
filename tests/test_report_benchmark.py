"""Contracts for the synthetic large-report reporter benchmark."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_report.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("benchmark_report", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark_report = _load_module()


def test_synthetic_suite_produces_the_requested_finding_count():
    suite = benchmark_report.build_suite(1_000, 7, 40)

    total = sum(len(result.findings) for result in suite.results)
    assert total == 1_000
    assert len(suite.results) == 7
    # An uneven split must not silently drop or duplicate findings.
    assert {len(result.findings) for result in suite.results} == {142, 143}


def test_synthetic_findings_fan_out_across_every_display_axis():
    suite = benchmark_report.build_suite(500, 2, 25)
    findings = [finding for result in suite.results for finding in result.findings]

    assert len({finding.fingerprint for finding in findings}) == 500
    assert len({finding.rule_id for finding in findings}) == 2 * 17
    assert len({finding.severity for finding in findings}) == 5
    assert len({finding.category for finding in findings}) == 4
    assert len({finding.confidence for finding in findings}) == 4
    assert len({finding.primary_location.path for finding in findings}) == 25


def test_rejected_shapes_fail_closed():
    with pytest.raises(ValueError):
        benchmark_report.build_suite(-1, 1, 1)
    with pytest.raises(ValueError):
        benchmark_report.build_suite(10, 0, 1)
    with pytest.raises(ValueError):
        benchmark_report.build_suite(10, 1, 0)


def test_every_measured_stage_carries_a_declared_budget():
    record = benchmark_report.run_benchmark(200, 2, 10)

    assert record["schema"] == "ici.benchmark.report/v1"
    stages = [item["stage"] for item in record["measurements"]]
    assert stages == ["console-default", "console-verbose", "html", "json", "sarif"]
    assert set(stages) == set(benchmark_report.BUDGET_SECONDS)
    for item in record["measurements"]:
        assert item["budget_seconds"] == benchmark_report.BUDGET_SECONDS[item["stage"]]
        assert item["output_bytes"] > 0
        assert item["seconds"] >= 0.0
        assert item["within_budget"] is (item["seconds"] <= item["budget_seconds"])


def test_default_run_is_a_trend_artifact_and_never_gates(tmp_path: Path, monkeypatch):
    record_path = tmp_path / "nested" / "benchmark.json"
    # A budget nothing can meet: the default run still exits 0 because CI wall
    # clock is too noisy to gate on, and --enforce is the deliberate opt-in.
    monkeypatch.setitem(benchmark_report.BUDGET_SECONDS, "json", 0.0)

    exit_code = benchmark_report.main(
        ["--findings", "200", "--engines", "2", "--files", "10", "--json", str(record_path)]
    )
    assert exit_code == 0

    written = json.loads(record_path.read_text(encoding="utf-8"))
    assert written["within_budget"] is False

    assert (
        benchmark_report.main(["--findings", "200", "--engines", "2", "--files", "10", "--enforce"])
        == 1
    )


def test_browser_payload_describes_what_a_browser_is_handed(tmp_path: Path):
    """CI cannot open a browser, but it can hold the inputs that decide the cost.

    Startup time and memory need a real browser and are measured by
    `scripts/benchmark_browser.py` locally. What travels in the trend artifact
    is the payload: rows rendered, inline JSON to parse, total bytes. A
    regression in those is a regression in what the browser has to do.
    """

    from ici.reporters.html import generate_html_report

    output = tmp_path / "large.html"
    generate_html_report(benchmark_report.build_suite(3_000, 2, 100), output, "b", tmp_path)

    payload = benchmark_report.measure_browser_payload(output)

    assert payload["html_bytes"] == output.stat().st_size
    assert payload["hydrated"] is True
    # Above the threshold the server renders a bounded view and the rest is
    # hydrated from inline JSON.
    assert payload["initial_issue_rows"] == 50  # keep
    assert payload["inline_json_bytes"] > 0


def test_a_small_report_is_not_hydrated_and_reports_no_inline_json(tmp_path: Path):
    from ici.reporters.html import generate_html_report

    output = tmp_path / "small.html"
    generate_html_report(benchmark_report.build_suite(10, 1, 5), output, "b", tmp_path)

    payload = benchmark_report.measure_browser_payload(output)

    assert payload["hydrated"] is False
    assert payload["inline_json_bytes"] == 0
    # A row is a display group, not a finding: the issues projection merges
    # findings that share a cause, so 10 findings render as at most 10 rows.
    # Below the threshold none are withheld, which is what "not hydrated" means.
    assert 0 < payload["initial_issue_rows"] <= 10


def test_the_trend_record_carries_the_browser_payload():
    record = benchmark_report.run_benchmark(200, 2, 10)

    assert set(record["browser_payload"]) == {
        "html_bytes",
        "initial_issue_rows",
        "inline_json_bytes",
        "hydrated",
    }
    assert record["browser_payload"]["html_bytes"] > 0
