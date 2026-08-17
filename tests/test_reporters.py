"""Tests for Console, Markdown, and HTML Reporters."""

from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, InspectionTarget, VerificationSuiteResult
from ici.reporters.html import generate_html_report
from ici.reporters.json_rep import save_json_report
from ici.reporters.markdown import generate_markdown_report


def test_reporters_output_generation(tmp_path: Path):
    target = InspectionTarget(
        file_path="src/pkg/core.py",
        start_line=10,
        end_line=25,
        target_name="sample()",
        status=EngineStatus.PASS,
        message="Valid structure",
    )
    result = EngineResult(
        engine_name="line",
        status=EngineStatus.PASS,
        summary="All files pass",
        targets=[target],
    )
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[result],
        duration=1.23,
        tem_score=4.88,
        max_tem_score=5.0,
    )

    # 1. Markdown
    md = generate_markdown_report(
        suite, repo_url="https://github.com/owner/repo", commit_sha="abc1234"
    )
    assert "`ici` Verification Report" in md
    assert "TEM: **`4.88 / 5.0`**" in md
    assert "https://github.com/owner/repo/blob/abc1234/src/pkg/core.py#L10-L25" in md

    # 2. HTML
    html_out = tmp_path / "report.html"
    generate_html_report(suite, html_out, project_name="TestProject", base_dir=tmp_path)
    assert html_out.exists()
    html_content = html_out.read_text(encoding="utf-8")
    assert "TestProject" in html_content
    assert "4.88" in html_content
    assert "openLoc(" in html_content
    assert "copyLoc(" in html_content
    assert "editorSelect" in html_content

    # 3. JSON
    json_out = tmp_path / "report.json"
    save_json_report(suite, json_out)
    assert json_out.exists()
