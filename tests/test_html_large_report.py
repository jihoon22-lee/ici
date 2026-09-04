"""Focused contracts for bounded lazy HTML issue rendering."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ici.core.models import (
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SourceLocation,
    VerificationSuiteResult,
)
from ici.reporters.html import generate_html_report
from ici.reporters.html import large as large_report
from ici.reporters.html import report as html_report


def _suite(finding_count: int, *, first_message: str | None = None) -> VerificationSuiteResult:
    findings = [
        Finding(
            rule_id="ici.synthetic.large",
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.MEDIUM,
            confidence=FindingConfidence.EXACT,
            fingerprint=f"synthetic-{index}",
            primary_location=SourceLocation(
                f"src/file-{index:06d}.py",
                1,
            ),
            message=(
                first_message
                if index == 0 and first_message is not None
                else f"synthetic finding {index}"
            ),
        )
        for index in range(finding_count)
    ]
    return VerificationSuiteResult(
        suite_status=EngineStatus.WARN,
        results=[
            EngineResult(
                engine_name="synthetic",
                status=EngineStatus.WARN,
                summary=f"{finding_count} findings",
                findings=findings,
            )
        ],
    )


def _data_body(content: str) -> str:
    match = re.search(
        r'<script type="application/json" id="ici-report-data">(.*?)</script>',
        content,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_report_at_threshold_keeps_server_rendered_contract(tmp_path: Path):
    output = tmp_path / "threshold.html"
    generate_html_report(_suite(2_000), output, base_dir=tmp_path)

    content = output.read_text(encoding="utf-8")
    assert 'id="ici-report-data"' not in content
    assert "id='ici-report-search'" not in content
    assert len(re.findall(r"class='issue-item'", content)) == 2_000


def test_large_report_is_bounded_and_hydrates_full_inventory(tmp_path: Path):
    output = tmp_path / "large.html"
    generate_html_report(_suite(100_000), output, base_dir=tmp_path)

    content = output.read_text(encoding="utf-8")
    assert len(re.findall(r"class='issue-item'", content)) <= 50
    payload = json.loads(_data_body(content))
    assert payload["schema_version"] == "ici.html-report/v1"
    assert payload["finding_count"] == 100_000
    assert len(payload["findings"]) == 100_000
    assert payload["findings"][0]["message"] == "synthetic finding 0"

    assert "<script src=" not in content
    assert "fetch(" not in content
    assert re.search(r"\bon[a-z]+\s*=", content, re.IGNORECASE) is None
    assert "textContent" in content
    assert "addEventListener" in content
    assert "innerHTML" not in content


def test_large_report_escapes_script_terminators_and_round_trips_data(tmp_path: Path):
    output = tmp_path / "escaped.html"
    malicious = "</script><script>alert('x')</script>&"
    generate_html_report(_suite(2_001, first_message=malicious), output, base_dir=tmp_path)

    content = output.read_text(encoding="utf-8")
    body = _data_body(content)
    assert "</script>" not in body
    assert r"\u003c/script\u003e" in body
    payload = json.loads(body)
    assert payload["findings"][0]["message"] == malicious


def test_large_report_rejects_oversized_embedded_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(large_report, "MAX_EMBEDDED_JSON_BYTES", 64)

    with pytest.raises(ValueError, match="64 MiB embedded JSON limit"):
        generate_html_report(_suite(2_001), tmp_path / "too-large.html", base_dir=tmp_path)


def test_html_report_replaces_output_symlink_without_touching_referent(tmp_path: Path):
    referent = tmp_path / "referent.html"
    referent.write_text("keep this report", encoding="utf-8")
    output = tmp_path / "report.html"
    try:
        output.symlink_to(referent)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    generate_html_report(_suite(1), output, base_dir=tmp_path)

    assert not output.is_symlink()
    assert referent.read_text(encoding="utf-8") == "keep this report"
    assert output.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_html_report_cleans_temporary_file_when_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "failed.html"

    def fail_fsync(_fd: int) -> None:
        raise OSError("injected fsync failure")

    monkeypatch.setattr(html_report.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="injected fsync failure"):
        generate_html_report(_suite(1), output, base_dir=tmp_path)

    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_html_report_cleans_temporary_file_when_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "cancelled.html"

    def cancel_fsync(_fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(html_report.os, "fsync", cancel_fsync)
    with pytest.raises(KeyboardInterrupt):
        generate_html_report(_suite(1), output, base_dir=tmp_path)

    assert not output.exists()
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))
