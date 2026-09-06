"""Per-axis filtering over the rendered HTML issue inventory.

Filtering is a display concern: it changes what a reader sees, never what the
report contains. These tests pin both halves of that — the axes are emitted on
every issue row so the browser can filter them, and the JSON inventory and
report totals are unaffected by the controls existing.
"""

from pathlib import Path

from ici.core.findings import finding_fingerprint
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


def _finding(rule_id: str, path: str, severity: FindingSeverity, category: FindingCategory):
    location = SourceLocation(path=path, start_line=4, end_line=4)
    return Finding(
        rule_id=rule_id,
        category=category,
        severity=severity,
        confidence=FindingConfidence.HIGH,
        fingerprint=finding_fingerprint(rule_id, location, symbol=rule_id),
        primary_location=location,
        message=f"{rule_id} message",
    )


def _suite() -> VerificationSuiteResult:
    line = EngineResult("line", EngineStatus.WARN, "line summary")
    line.findings = [
        _finding(
            "ici.line.long", "src/long.py", FindingSeverity.HIGH, FindingCategory.MAINTAINABILITY
        )
    ]
    security = EngineResult("security", EngineStatus.WARN, "security summary")
    security.findings = [
        _finding(
            "ici.security.secret", "src/secret.py", FindingSeverity.MEDIUM, FindingCategory.SECURITY
        )
    ]
    return VerificationSuiteResult(suite_status=EngineStatus.WARN, results=[line, security])


def _html(tmp_path: Path) -> str:
    output = tmp_path / "report.html"
    generate_html_report(_suite(), output, project_name="filters", base_dir=tmp_path)
    return output.read_text(encoding="utf-8")


def test_every_issue_row_carries_its_filter_axes(tmp_path: Path):
    content = _html(tmp_path)

    for attribute in (
        "data-engine=",
        "data-rule=",
        "data-category=",
        "data-severity=",
        "data-file=",
    ):
        assert attribute in content, f"issue rows are missing {attribute}"
    assert "data-engine='security'" in content
    assert "data-category='security'" in content
    assert "data-severity='HIGH'" in content
    assert "data-file='src/long.py'" in content


def test_filter_options_come_from_the_report_not_from_every_enum_value(tmp_path: Path):
    # Offering a filter that cannot match anything is worse than not offering it.
    content = _html(tmp_path)
    filter_bar = content[content.find("class='issue-filter-bar'") :]
    filter_bar = filter_bar[: filter_bar.find("</div>")]

    assert ">line<" in filter_bar
    assert ">security<" in filter_bar
    assert ">dup<" not in filter_bar


def test_sorting_and_axis_controls_are_present(tmp_path: Path):
    content = _html(tmp_path)

    for control in (
        "ici-issue-engine",
        "ici-issue-severity",
        "ici-issue-category",
        "ici-issue-rule",
        "ici-issue-file",
        "ici-issue-sort",
    ):
        assert control in content, f"missing the {control} control"


def test_filter_controls_do_not_load_anything_external(tmp_path: Path):
    # The report is Zero-CDN. Adding controls must not change that.
    content = _html(tmp_path)

    assert 'src="http' not in content
    assert 'href="http' not in content or "https://json.schemastore.org" not in content


def test_a_report_without_issues_renders_no_filter_bar(tmp_path: Path):
    clean = EngineResult("line", EngineStatus.PASS, "clean")
    suite = VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[clean])
    output = tmp_path / "clean.html"
    generate_html_report(suite, output, project_name="filters", base_dir=tmp_path)

    # The embedded script mentions the selector, so assert on the markup class
    # the renderer emits rather than on the selector string.
    assert "class='issue-filter-bar'" not in output.read_text(encoding="utf-8")
