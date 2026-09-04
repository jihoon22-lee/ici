"""Tests for the bounded, deterministic SARIF reporter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici import __version__
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingDelta,
    FindingSeverity,
    FindingSuppression,
    SourceLocation,
    SuppressionKind,
    VerificationSuiteResult,
)
from ici.reporters import sarif


def _finding(
    *,
    path: str = "src/app.py",
    line: int = 4,
    rule_id: str = "ici.security.secret",
    severity: FindingSeverity = FindingSeverity.HIGH,
    message: str = "secret detected",
    suppressed: bool = False,
) -> Finding:
    location = SourceLocation(path=path, start_line=line, label="load_config")
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.SECURITY,
        severity=severity,
        confidence=FindingConfidence.HIGH,
        fingerprint=finding_fingerprint(rule_id, location, symbol=location.label),
        primary_location=location,
        related_locations=[SourceLocation(path="src/config.py", start_line=8, label="source")],
        message=message,
        explanation="A credential-like value is present.",
        remediation="Remove the credential from source control.",
        tool_name="scanner",
        tool_version="1.2",
        suppression=FindingSuppression(
            suppressed=suppressed,
            kind=SuppressionKind.CONFIG if suppressed else SuppressionKind.NONE,
            reason="accepted for the fixture" if suppressed else "",
        ),
    )


def _result(
    finding: Finding,
    *,
    engine_name: str = "security",
    status: EngineStatus = EngineStatus.FAIL,
) -> EngineResult:
    return EngineResult(
        engine_name=engine_name,
        status=status,
        summary="one finding",
        findings=[finding],
    )


def _suite(
    results: list[EngineResult],
    *,
    baseline: BaselineComparison | None = None,
    status: EngineStatus = EngineStatus.FAIL,
) -> VerificationSuiteResult:
    return VerificationSuiteResult(
        suite_status=status,
        results=results,
        baseline_comparison=baseline,
    )


def test_sarif_uses_canonical_findings_and_maps_fields(tmp_path: Path):
    finding = _finding(suppressed=True)
    suite = _suite([_result(finding)])

    payload = sarif.serialize_sarif(suite, project_root=tmp_path)
    run = payload["runs"][0]
    result = run["results"][0]

    assert payload["$schema"] == sarif.SARIF_SCHEMA_URL
    assert payload["version"] == "2.1.0"
    assert run["tool"]["driver"]["version"] == __version__
    assert result["ruleId"] == "ici.security.secret"
    assert result["level"] == "error"
    assert result["message"] == {"text": "secret detected"}
    assert result["fingerprints"]["ici/v3"] == finding.fingerprint
    assert result["locations"][0]["physicalLocation"] == {
        "artifactLocation": {"uri": "src/app.py", "uriBaseId": "%SRCROOT%"},
        "region": {"startLine": 4},
    }
    assert result["relatedLocations"][0]["id"] == 1
    assert result["relatedLocations"][0]["message"] == {"text": "source"}
    assert result["suppressions"] == [
        {"kind": "external", "justification": "accepted for the fixture"}
    ]
    assert result["properties"] == {
        "category": "security",
        "confidence": "high",
        "engine": "security",
        "suppressed": True,
        "tool": "scanner",
        "tool_version": "1.2",
    }
    assert run["tool"]["driver"]["rules"][0]["id"] == "ici.security.secret"
    assert run["tool"]["driver"]["rules"][0]["defaultConfiguration"] == {"level": "error"}


def test_sarif_orders_rules_and_results_independently_of_input_order(tmp_path: Path):
    first = _finding(path="src/z.py", line=20, rule_id="ici.z.rule")
    second = _finding(path="src/a.py", line=2, rule_id="ici.a.rule", severity=FindingSeverity.LOW)

    forward = sarif.serialize_sarif(
        _suite([_result(first), _result(second)]), project_root=tmp_path
    )
    reverse = sarif.serialize_sarif(
        _suite([_result(second), _result(first)]), project_root=tmp_path
    )

    assert forward == reverse
    assert [item["id"] for item in forward["runs"][0]["tool"]["driver"]["rules"]] == [
        "ici.a.rule",
        "ici.z.rule",
    ]
    assert [item["ruleId"] for item in forward["runs"][0]["results"]] == [
        "ici.a.rule",
        "ici.z.rule",
    ]


def test_sarif_artifact_locations_are_valid_percent_encoded_uris(tmp_path: Path):
    payload = sarif.serialize_sarif(
        _suite([_result(_finding(path="src/space #한글%.py"))]), project_root=tmp_path
    )

    artifact = payload["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]
    assert artifact["uri"] == "src/space%20%23%ED%95%9C%EA%B8%80%25.py"


def test_sarif_maps_baseline_delta_and_emits_resolved_result(tmp_path: Path):
    current = _finding(path="src/current.py", line=7)
    old = _finding(path="src/old.py", line=3)
    moved = FindingDelta(
        state=DeltaState.MOVED,
        engine_name="security",
        fingerprint=current.fingerprint,
        rule_id=current.rule_id,
        message=current.message,
        current_location=current.primary_location,
        baseline_location=old.primary_location,
        current_severity=current.severity,
        baseline_severity=FindingSeverity.MEDIUM,
        regressed=True,
        gated=True,
    )
    resolved = FindingDelta(
        state=DeltaState.RESOLVED,
        engine_name="lint",
        fingerprint="sha256:" + "a" * 64,
        rule_id="ici.lint.old",
        message="old warning",
        baseline_location=SourceLocation(path="src/removed.py", start_line=9),
        baseline_severity=FindingSeverity.MEDIUM,
    )
    baseline = BaselineComparison(
        source_path="baseline.json",
        entries=[moved, resolved],
    )

    payload = sarif.serialize_sarif(
        _suite([_result(current)], baseline=baseline), project_root=tmp_path
    )
    results = payload["runs"][0]["results"]

    assert len(results) == 2
    current_result = next(item for item in results if item["ruleId"] == current.rule_id)
    assert current_result["baselineState"] == "updated"
    assert current_result["properties"]["delta_state"] == "moved"
    assert current_result["properties"]["delta_regressed"] is True
    assert current_result["properties"]["delta_gated"] is True

    resolved_result = next(item for item in results if item["ruleId"] == "ici.lint.old")
    assert resolved_result["baselineState"] == "absent"
    assert resolved_result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == (
        "src/removed.py"
    )
    assert payload["runs"][0]["properties"]["finding_count"] == 2


def test_sarif_save_is_json_and_atomic(tmp_path: Path):
    output = tmp_path / "nested" / "result.sarif"
    sarif.save_sarif_report(
        _suite([_result(_finding())]),
        output,
        project_root=tmp_path,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["version"] == "2.1.0"
    assert not list(output.parent.glob("*.tmp"))


def test_sarif_enforces_result_bound(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sarif, "MAX_SARIF_RESULTS", 0)

    with pytest.raises(sarif.SarifBoundsError, match="result count"):
        sarif.serialize_sarif(_suite([_result(_finding())]), project_root=tmp_path)


def test_sarif_enforces_rule_bound(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sarif, "MAX_SARIF_RULES", 0)

    with pytest.raises(sarif.SarifBoundsError, match="rule count"):
        sarif.serialize_sarif(_suite([_result(_finding())]), project_root=tmp_path)
