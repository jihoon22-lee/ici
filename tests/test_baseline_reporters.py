"""Reporter coverage for optional v3 baseline comparisons."""

from pathlib import Path

from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineResult,
    EngineStatus,
    FindingDelta,
    FindingSeverity,
    SourceLocation,
    VerificationSuiteResult,
)
from ici.reporters.console import print_suite_dashboard
from ici.reporters.html import generate_html_report
from ici.reporters.markdown import generate_markdown_report


def _location(path: str, line: int) -> SourceLocation:
    return SourceLocation(path=path, start_line=line, end_line=line + 1)


def _comparison() -> BaselineComparison:
    return BaselineComparison(
        source_path=".ici/baseline.json",
        warnings=["baseline <metadata> differs & should be reviewed"],
        fail_on_new=True,
        gate_failed=True,
        entries=[
            FindingDelta(
                state=DeltaState.UNCHANGED,
                engine_name="line",
                fingerprint="sha256:" + "1" * 64,
                rule_id="ici.line.clean",
                message="unchanged finding",
                current_location=_location("src/unchanged.py", 3),
                baseline_location=_location("src/unchanged.py", 3),
                current_severity=FindingSeverity.LOW,
                baseline_severity=FindingSeverity.LOW,
            ),
            FindingDelta(
                state=DeltaState.NEW,
                engine_name="security<script>",
                fingerprint="sha256:" + "2" * 64,
                rule_id="ici.security<new>",
                message="new <script>alert('x')</script>",
                current_location=_location("src/current.py", 8),
                current_severity=FindingSeverity.CRITICAL,
                gated=True,
            ),
            FindingDelta(
                state=DeltaState.MOVED,
                engine_name="lint",
                fingerprint="sha256:" + "3" * 64,
                rule_id="ici.lint.moved",
                message="moved finding",
                current_location=_location("src/new.py", 20),
                baseline_location=_location("src/old.py", 10),
                current_severity=FindingSeverity.CRITICAL,
                baseline_severity=FindingSeverity.HIGH,
                regressed=True,
                gated=True,
            ),
            FindingDelta(
                state=DeltaState.RESOLVED,
                engine_name="line",
                fingerprint="sha256:" + "4" * 64,
                rule_id="ici.line.fixed",
                message="resolved finding",
                baseline_location=_location("src/fixed.py", 4),
                baseline_severity=FindingSeverity.MEDIUM,
            ),
        ],
    )


def _suite(with_comparison: bool = True) -> VerificationSuiteResult:
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "clean")],
    )
    if with_comparison:
        suite.baseline_comparison = _comparison()
    return suite


def test_markdown_baseline_is_compact_issues_first_and_escaped():
    markdown = generate_markdown_report(_suite())

    assert "### Baseline finding delta" in markdown
    assert ".ici/baseline.json" in markdown
    assert "| New | **1** |" in markdown
    assert "| Unchanged | **1** |" in markdown
    assert "| Moved | **1** |" in markdown
    assert "| Resolved | **1** |" in markdown
    assert "| Regressed | **1** |" in markdown
    assert "| Gated | **2** |" in markdown
    assert "FAILED" in markdown
    assert "baseline &lt;metadata&gt; differs &amp; should be reviewed" in markdown
    assert "src/current.py" in markdown and "src/old.py" in markdown
    assert "high → critical" in markdown
    assert "security&lt;script&gt;" in markdown
    assert "<script>alert('x')</script>" not in markdown

    # The gated NEW/MOVED rows lead the details, and the unchanged row is not
    # allowed to drown out the actionable inventory.
    details_start = markdown.index("Issues-first delta details")
    assert markdown.index("security&lt;script&gt;", details_start) < markdown.index(
        "ici.lint.moved", details_start
    )


def test_html_baseline_tab_is_zero_cdn_and_escapes_delta_fields(tmp_path: Path):
    output = tmp_path / "baseline.html"
    generate_html_report(_suite(), output, base_dir=tmp_path)
    content = output.read_text(encoding="utf-8")

    assert 'id="tab-baseline"' in content
    assert "Baseline Finding Delta" in content
    assert "Current location" in content and "Baseline location" in content
    assert "Severity transition" in content
    assert ".ici/baseline.json" in content
    assert "baseline &lt;metadata&gt; differs &amp; should be reviewed" in content
    assert "security&lt;script&gt;" in content
    assert "new &lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in content
    assert "<script>alert('x')</script>" not in content
    assert 'src="http' not in content and "href=\"http" not in content


def test_console_baseline_summary_and_gated_details(capsys):
    print_suite_dashboard(_suite(), Path.cwd())
    output = capsys.readouterr().out

    assert "Baseline Finding Delta" in output
    assert ".ici/baseline.json" in output
    assert "New 1" in output and "Unchanged 1" in output
    assert "Moved 1" in output and "Resolved 1" in output
    assert "Regressed 1" in output and "Gated 2" in output
    assert "FAILED" in output and "Compatibility warnings" in output
    assert "src/current.py" in output and "src/old.py" in output
    assert "high → critical" in output
    assert output.index("security<script>") < output.index("ici.lint.moved")


def test_absent_comparison_keeps_reporters_without_baseline_sections(tmp_path: Path, capsys):
    suite = _suite(with_comparison=False)
    markdown = generate_markdown_report(suite)
    assert "Baseline finding delta" not in markdown

    output = tmp_path / "legacy.html"
    generate_html_report(suite, output, base_dir=tmp_path)
    assert 'id="tab-baseline"' not in output.read_text(encoding="utf-8")

    print_suite_dashboard(suite, tmp_path)
    assert "Baseline Finding Delta" not in capsys.readouterr().out
