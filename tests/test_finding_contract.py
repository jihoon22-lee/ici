import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from ici.core.findings import canonical_project_path, finding_fingerprint, findings_for_result
from ici.core.models import (
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    FindingSuppression,
    InspectionTarget,
    SourceLocation,
    ToolEvidence,
    VerificationSuiteResult,
)
from ici.core.redaction import REDACTED, redact_suite
from ici.reporters.html import generate_html_report
from ici.reporters.json_rep import migrate_report_payload, serialize_suite_result
from ici.reporters.markdown import generate_markdown_report


def _legacy_result(file_path: str = "src/service.py", start_line: int = 7) -> EngineResult:
    return EngineResult(
        engine_name="security",
        status=EngineStatus.FAIL,
        summary="one issue",
        targets=[
            InspectionTarget(
                file_path=file_path,
                start_line=start_line,
                end_line=start_line + 1,
                start_column=3,
                end_column=18,
                target_name="load_config",
                status=EngineStatus.FAIL,
                message="hardcoded credential",
                snippet="token = value",
                metrics={"entropy": 4.2, "line_coverage": 91, "ignored": "text"},
            )
        ],
        tool_evidence=[ToolEvidence(name="scanner", path="/usr/bin/scanner", version="1.2")],
    )


def _suite(result: EngineResult) -> VerificationSuiteResult:
    return VerificationSuiteResult(
        suite_status=result.status,
        results=[result],
        duration=0.5,
        tem_score=4.5,
    )


def test_v3_writer_retains_targets_and_adds_complete_findings(tmp_path):
    payload = serialize_suite_result(_suite(_legacy_result()), project_root=tmp_path)

    assert payload["schema_version"] == "ici.result/v3"
    engine = payload["results"][0]
    assert engine["schema_version"] == "ici.result/v3"
    assert engine["targets"][0]["start_column"] == 3
    assert engine["targets"][0]["end_column"] == 18
    assert len(engine["findings"]) == 1
    finding = engine["findings"][0]
    assert finding["rule_id"] == "ici.legacy.security.target"
    assert finding["category"] == "security"
    assert finding["severity"] == "high"
    assert finding["confidence"] == "high"
    assert finding["primary_location"]["path"] == "src/service.py"
    assert finding["primary_location"]["start_column"] == 3
    assert finding["tool_name"] == "scanner"
    assert finding["metrics"] == {
        "entropy": {"value": 4.2, "unit": ""},
        "line_coverage": {"value": 91, "unit": "percent"},
    }
    assert finding["fingerprint"].startswith("sha256:")


def test_checked_in_schema_declares_v3_finding_contract():
    schema_path = (
        Path(__file__).parents[1] / "src" / "ici" / "schemas" / "ici-result-v3.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["title"] == "ici.result/v3"
    finding = schema["$defs"]["finding"]
    required = set(finding["required"])
    assert {
        "rule_id",
        "category",
        "severity",
        "confidence",
        "fingerprint",
        "primary_location",
        "related_locations",
        "message",
        "explanation",
        "remediation",
        "tool_rule_id",
        "tool_name",
        "tool_version",
        "suppression",
        "metrics",
    } <= required
    assert "findings" in schema["$defs"]["engine"]["required"]
    assert "targets" in schema["$defs"]["engine"]["required"]


def test_v2_migration_preserves_extensions_and_adds_findings():
    v2 = {
        "schema_version": "ici.result/v2",
        "suite_status": "WARN",
        "producer_extension": {"kept": True},
        "results": [
            {
                "schema_version": "ici.result/v2",
                "engine_name": "lint",
                "status": "WARN",
                "summary": "one warning",
                "required": True,
                "evidence": "MEASURED",
                "producer_field": 42,
                "targets": [
                    {
                        "file_path": "src/a.py",
                        "start_line": 4,
                        "target_name": "rule-x",
                        "status": "WARN",
                        "message": "warning",
                    }
                ],
                "tool_evidence": [],
            }
        ],
    }

    migrated = migrate_report_payload(v2)

    assert migrated["schema_version"] == "ici.result/v3"
    assert migrated["producer_extension"] == {"kept": True}
    engine = migrated["results"][0]
    assert engine["schema_version"] == "ici.result/v3"
    assert engine["producer_field"] == 42
    assert engine["targets"][0]["start_column"] is None
    assert engine["findings"][0]["primary_location"]["path"] == "src/a.py"


def test_canonical_path_and_fingerprint_are_checkout_and_separator_independent():
    posix = _legacy_result("/tmp/checkout-a/src/service.py")
    windows = _legacy_result(r"C:\work\checkout-b\src\service.py")

    posix_finding = findings_for_result(posix, "/tmp/checkout-a")[0]
    windows_finding = findings_for_result(windows, r"c:\work\checkout-b")[0]

    assert canonical_project_path("src/feature/../service.py") == "src/service.py"
    assert posix_finding.primary_location.path == "src/service.py"
    assert windows_finding.primary_location.path == "src/service.py"
    assert posix_finding.fingerprint == windows_finding.fingerprint


@pytest.mark.parametrize(
    ("path", "root"),
    [
        ("../outside.py", None),
        ("/tmp/outside.py", None),
        ("/tmp/other/outside.py", "/tmp/project"),
        (r"D:\other\outside.cpp", r"C:\project"),
    ],
)
def test_canonical_path_rejects_escape_and_unscoped_absolute_paths(path, root):
    with pytest.raises(ValueError):
        canonical_project_path(path, root)


def test_symbol_fingerprint_survives_line_move_but_region_fingerprint_does_not():
    symbol_a = SourceLocation(path="src/a.py", start_line=10, label="run")
    symbol_b = SourceLocation(path="src/a.py", start_line=90, label="run")
    region_a = SourceLocation(path="src/a.py", start_line=10)
    region_b = SourceLocation(path="src/a.py", start_line=90)

    assert finding_fingerprint("ici.test.rule", symbol_a, symbol="run") == finding_fingerprint(
        "ici.test.rule", symbol_b, symbol="run"
    )
    assert finding_fingerprint("ici.test.rule", region_a) != finding_fingerprint(
        "ici.test.rule", region_b
    )


def test_native_finding_wins_over_legacy_adapter_with_same_fingerprint():
    result = _legacy_result()
    adapted = findings_for_result(result)[0]
    native = Finding(
        rule_id=adapted.rule_id,
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        confidence=FindingConfidence.EXACT,
        fingerprint=adapted.fingerprint,
        primary_location=adapted.primary_location,
        message="native detail",
    )
    result.findings.append(native)

    findings = findings_for_result(result)

    assert len(findings) == 1
    assert findings[0].message == "native detail"
    assert findings[0].confidence == FindingConfidence.EXACT


def test_native_locations_are_canonicalized_and_fingerprint_is_rederived():
    finding = Finding(
        rule_id="ici.security.secret",
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.EXACT,
        fingerprint="sha256:" + "0" * 64,
        primary_location=SourceLocation(r"C:\repo\src\a.py", 4, label="token"),
        related_locations=[SourceLocation(r"C:\repo\include\a.hpp", 2)],
        message="secret",
    )
    result = _legacy_result()
    result.targets = []
    result.findings = [finding]

    normalized = findings_for_result(result, r"c:\repo")[0]

    assert normalized.primary_location.path == "src/a.py"
    assert normalized.related_locations[0].path == "include/a.hpp"
    assert normalized.fingerprint != finding.fingerprint
    assert normalized.fingerprint == finding_fingerprint(
        finding.rule_id, normalized.primary_location, symbol="token"
    )


def test_legacy_adapter_preserves_repeated_unqualified_symbols_by_region():
    result = _legacy_result()
    result.targets.append(
        InspectionTarget(
            file_path="src/service.py",
            start_line=70,
            target_name="load_config",
            status=EngineStatus.FAIL,
            message="second overload",
        )
    )

    findings = findings_for_result(result)

    assert len(findings) == len(result.targets) == 2
    assert len({finding.fingerprint for finding in findings}) == 2
    assert {finding.primary_location.start_line for finding in findings} == {7, 70}


def test_redaction_covers_all_result_text_and_every_reporter(tmp_path, monkeypatch):
    secrets = [
        "correct horse battery staple",
        "supersecret123",
        "tokenvalue123",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "AKIA1234567890ABCDEF",
        "sk-abcdefghijklmnopqrstuv",
        "PRIVATE-BODY",
        "TRUNCATED-BODY",
        "pathsecret",
        "toolsecret",
        "keysecret",
    ]
    result = _legacy_result()
    result.summary = 'password="correct horse battery staple"'
    result.targets[0].message = "api_key=supersecret123"
    result.targets[0].snippet = "client_secret='tokenvalue123'"
    result.raw_output = "Authorization: Bearer tokenvalue123"
    result.extra = {
        "pem": "-----BEGIN PRIVATE KEY-----\nPRIVATE-BODY\n-----END PRIVATE KEY-----",
        "truncated": "-----BEGIN RSA PRIVATE KEY-----\nTRUNCATED-BODY",
        "password=keysecret": "Bearer xy",
    }
    result.tool_evidence[0].path = "/tmp/api_key=pathsecret"
    result.tool_evidence[0].argv = ["scanner", "--token", "tokenvalue123"]
    result.tool_evidence[0].error = "credential AKIA1234567890ABCDEF"
    result.findings = [
        Finding(
            rule_id="ici.security.secret",
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.HIGH,
            confidence=FindingConfidence.EXACT,
            fingerprint="sha256:" + "a" * 64,
            primary_location=SourceLocation("src/service.py", 7, label="load_config"),
            message="ghp_abcdefghijklmnopqrstuvwxyz",
            explanation="api_key=supersecret123",
            remediation="replace --password tokenvalue123",
            tool_name="token=toolsecret",
            snippet="sk-abcdefghijklmnopqrstuv",
            suppression=FindingSuppression(reason="password=supersecret123"),
        )
    ]
    suite = _suite(result)

    serialized = json.dumps(serialize_suite_result(suite), ensure_ascii=False)
    markdown = generate_markdown_report(suite)
    html_path = tmp_path / "report.html"
    generate_html_report(suite, html_path)
    html = html_path.read_text(encoding="utf-8")

    import ici.reporters.console as console_reporter

    stream = io.StringIO()
    monkeypatch.setattr(console_reporter, "console", Console(file=stream, force_terminal=False))
    console_reporter.print_suite_dashboard(suite, tmp_path)
    rendered_console = stream.getvalue()

    safe_suite = redact_suite(suite)
    combined = "\n".join([serialized, markdown, html, rendered_console, repr(safe_suite)])
    for secret in secrets:
        assert secret not in combined
    assert REDACTED in combined
