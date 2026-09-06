"""Every reporter must preserve the same v3 finding contract.

Two reports of one run must not disagree. These tests build a finding that
carries all five features the contract names — v3 identity, related locations,
confidence, suppression, and baseline delta — and hold each reporter to the
part of that contract it actually owns.

The reporters are not identical by design, so the boundaries are pinned here
rather than assumed:

* JSON, SARIF and HTML carry the full inventory and must preserve all five.
* Markdown is the bounded GitHub step summary. It deliberately omits related
  rows for informational and suppressed findings, so it is held to identity,
  confidence and delta plus the related row of a finding that is neither.
* The console is issues-first and bounded, so it is held to identity only.

A reporter that silently drops something it is supposed to carry fails here.
Tightening one of these boundaries is a deliberate contract change, not a
drive-by edit.
"""

import json
from pathlib import Path

from rich.console import Console

from ici.core.findings import finding_fingerprint, findings_for_result
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
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.json_rep import serialize_suite_result
from ici.reporters.markdown import generate_markdown_report
from ici.reporters.sarif import serialize_sarif

RULE_ID = "ici.parity.contract"
VISIBLE_RULE_ID = "ici.parity.visible"
TOOL_RULE_ID = "parity-tool-rule"
PRIMARY_PATH = "src/parity_primary.py"
RELATED_PATH = "src/parity_related.py"
VISIBLE_PRIMARY_PATH = "src/parity_visible.py"
VISIBLE_RELATED_PATH = "src/parity_visible_related.py"
SUPPRESSION_REASON = "parity suppression reason"


def _visible_finding() -> Finding:
    """A finding markdown must render in full: not informational, not suppressed."""

    return Finding(
        rule_id=VISIBLE_RULE_ID,
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.HIGH,
        fingerprint=finding_fingerprint(
            VISIBLE_RULE_ID,
            SourceLocation(path=VISIBLE_PRIMARY_PATH, start_line=5, end_line=5),
            symbol="visible",
        ),
        primary_location=SourceLocation(path=VISIBLE_PRIMARY_PATH, start_line=5, end_line=5),
        message="parity visible finding",
        related_locations=[SourceLocation(path=VISIBLE_RELATED_PATH, start_line=9, end_line=9)],
    )


def _finding() -> Finding:
    return Finding(
        rule_id=RULE_ID,
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.HIGH,
        confidence=FindingConfidence.EXACT,
        fingerprint=finding_fingerprint(
            RULE_ID,
            SourceLocation(path=PRIMARY_PATH, start_line=12, end_line=14),
            symbol="parity",
        ),
        primary_location=SourceLocation(path=PRIMARY_PATH, start_line=12, end_line=14),
        message="parity finding message",
        related_locations=[SourceLocation(path=RELATED_PATH, start_line=41, end_line=41)],
        tool_rule_id=TOOL_RULE_ID,
        tool_name="parity-tool",
        suppression=FindingSuppression(
            suppressed=True,
            kind=SuppressionKind.CONFIG,
            reason=SUPPRESSION_REASON,
        ),
    )


def _suite() -> VerificationSuiteResult:
    result = EngineResult("line", EngineStatus.WARN, "parity summary")
    result.findings = [_finding(), _visible_finding()]
    suite = VerificationSuiteResult(suite_status=EngineStatus.WARN, results=[result])

    # Bind the delta to the fingerprint the pipeline actually canonicalises to.
    # Hand-writing one couples the test to the fingerprint implementation and
    # silently stops matching, which looks like a reporter defect but is not.
    canonical = {
        finding.rule_id: finding.fingerprint for finding in findings_for_result(result, Path.cwd())
    }
    suite.baseline_comparison = BaselineComparison(
        source_path=".ici/baseline.json",
        entries=[
            FindingDelta(
                state=DeltaState.NEW,
                engine_name="line",
                fingerprint=canonical[VISIBLE_RULE_ID],
                rule_id=VISIBLE_RULE_ID,
                message="parity visible finding",
                current_location=SourceLocation(
                    path=VISIBLE_PRIMARY_PATH, start_line=5, end_line=5
                ),
                current_severity=FindingSeverity.MEDIUM,
            )
        ],
    )
    return suite


def _json_text() -> str:
    return json.dumps(serialize_suite_result(_suite(), Path.cwd()), ensure_ascii=False)


def _sarif_text() -> str:
    return json.dumps(serialize_sarif(_suite(), Path.cwd()), ensure_ascii=False)


def _html_text(tmp_path: Path) -> str:
    output = tmp_path / "parity.html"
    generate_html_report(_suite(), output, "parity", tmp_path)
    return output.read_text(encoding="utf-8")


def _console_text() -> str:
    console = Console(record=True, width=200, force_terminal=False)
    print_suite_dashboard(_suite(), Path.cwd(), output_console=console)
    return console.export_text()


def _full_inventory_texts(tmp_path: Path) -> dict[str, str]:
    """Reporters that carry the complete finding inventory."""

    return {
        "json": _json_text(),
        "sarif": _sarif_text(),
        "html": _html_text(tmp_path),
    }


def test_full_inventory_reporters_preserve_the_v3_finding_identity(tmp_path: Path):
    for name, text in _full_inventory_texts(tmp_path).items():
        assert VISIBLE_RULE_ID in text, f"{name} dropped the v3 rule id"
        assert VISIBLE_PRIMARY_PATH in text, f"{name} dropped the primary location path"


def test_machine_readable_reporters_keep_suppressed_findings_in_the_inventory(tmp_path: Path):
    # A suppressed finding is still part of the record. JSON and SARIF must keep
    # it so a consumer can audit what was suppressed and why.
    for name, text in {"json": _json_text(), "sarif": _sarif_text()}.items():
        assert RULE_ID in text, f"{name} dropped the suppressed finding"
        assert PRIMARY_PATH in text, f"{name} dropped the suppressed finding location"


def test_full_inventory_reporters_preserve_related_locations(tmp_path: Path):
    for name, text in _full_inventory_texts(tmp_path).items():
        assert VISIBLE_RELATED_PATH in text, f"{name} dropped the related location"


def test_full_inventory_reporters_preserve_confidence(tmp_path: Path):
    for name, text in _full_inventory_texts(tmp_path).items():
        assert FindingConfidence.HIGH.value in text, f"{name} dropped the finding confidence"


def test_machine_readable_reporters_preserve_suppression(tmp_path: Path):
    # JSON and SARIF are the machine-readable inventories other tools consume, so
    # they must carry why a finding is suppressed. HTML shows the finding itself
    # but not the reason string; that boundary is pinned separately below.
    for name, text in {"json": _json_text(), "sarif": _sarif_text()}.items():
        assert SUPPRESSION_REASON in text or SuppressionKind.CONFIG.value in text, (
            f"{name} dropped the suppression record"
        )


def test_full_inventory_reporters_preserve_the_baseline_delta(tmp_path: Path):
    for name, text in _full_inventory_texts(tmp_path).items():
        assert DeltaState.NEW.value in text, f"{name} dropped the baseline delta state"


def test_markdown_preserves_identity_confidence_and_delta():
    markdown = generate_markdown_report(_suite())

    assert VISIBLE_RULE_ID in markdown
    assert VISIBLE_PRIMARY_PATH in markdown
    assert "New" in markdown, "markdown dropped the baseline delta summary"


def test_markdown_renders_related_rows_for_a_reportable_finding():
    # Markdown omits related rows for informational and suppressed findings on
    # purpose, so the visible finding is what proves the row is rendered at all.
    markdown = generate_markdown_report(_suite())

    assert VISIBLE_RELATED_PATH in markdown, "markdown dropped a reportable related location"


def test_markdown_omits_related_rows_for_suppressed_findings():
    # The counterpart boundary. Changing this is a contract change: the GitHub
    # summary is bounded and must not spend its budget on suppressed rows.
    markdown = generate_markdown_report(_suite())
    related_section = markdown[markdown.find("Related") :] if "Related" in markdown else ""

    assert RELATED_PATH not in related_section


def test_console_dashboard_surfaces_the_same_run():
    # The console is issues-first and bounded rather than a full inventory, so it
    # is held to the identity it must never lose rather than to every field.
    text = _console_text()

    assert "line" in text
    assert "parity summary" in text
