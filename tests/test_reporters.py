"""Tests for Console, Markdown, and HTML Reporters."""

from pathlib import Path

from ici.core.models import (
    EngineResult,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
    VerificationSuiteResult,
)
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
    assert "data-abs-path=" in html_content
    assert "data-rel-path=" in html_content
    assert "onclick=" not in html_content
    assert "editorSelect" in html_content

    # 3. JSON
    json_out = tmp_path / "report.json"
    save_json_report(suite, json_out)
    assert json_out.exists()


def test_html_and_markdown_render_related_finding_locations(tmp_path: Path):
    target = InspectionTarget(
        file_path="src/main.cpp",
        start_line=3,
        target_name="ClangTidy:modernize-use-nullptr",
        status=EngineStatus.WARN,
        message="warning: prefer nullptr",
    )
    finding = Finding(
        rule_id="ici.legacy.lint.target",
        category=FindingCategory.MAINTAINABILITY,
        severity=FindingSeverity.MEDIUM,
        confidence=FindingConfidence.EXACT,
        fingerprint="",
        primary_location=SourceLocation(
            "src/main.cpp",
            3,
            label="ClangTidy:modernize-use-nullptr",
        ),
        message="warning: prefer nullptr",
        tool_rule_id="modernize-use-nullptr",
        related_locations=[
            SourceLocation(
                "include/macros.hpp",
                9,
                start_column=7,
                label="note: expanded from <NULL> & reviewed",
            )
        ],
    )
    result = EngineResult(
        engine_name="lint",
        status=EngineStatus.WARN,
        summary="0 Errors, 1 Warnings Found",
        targets=[target],
        findings=[finding],
    )
    suite = VerificationSuiteResult(suite_status=EngineStatus.WARN, results=[result])

    markdown = generate_markdown_report(
        suite,
        repo_url="https://github.com/owner/repo",
        commit_sha="abc1234",
    )
    assert "Related diagnostic locations" in markdown
    assert "include/macros.hpp#L9" in markdown
    assert "note: expanded from &lt;NULL&gt; &amp; reviewed" in markdown

    html_out = tmp_path / "related.html"
    generate_html_report(suite, html_out, project_name="Related", base_dir=tmp_path)
    content = html_out.read_text(encoding="utf-8")
    assert "Active Quality Gate Issues (1 Findings)" in content
    assert "Related evidence" in content
    assert 'data-rel-path="include/macros.hpp"' in content
    assert "note: expanded from &lt;NULL&gt; &amp; reviewed" in content


def test_html_report_includes_module_coverage_table(tmp_path: Path):
    result = EngineResult(
        engine_name="test",
        status=EngineStatus.PASS,
        summary="2/2 Tests Passed | Branch: 66.7%, Func: 95.0% -> TEM: 3.96 / 5.0",
        score=3.96,
        max_score=5.0,
        extra={
            "passed_tests": 2,
            "total_tests": 2,
            "branch_coverage": 66.7,
            "function_coverage": 95.0,
            "tem_score": 3.96,
            "test_suites": [],
            "coverage_source": "coverage.py",
            "coverage_files": [
                {
                    "file": "src/pkg/core.py",
                    "stmts": 10,
                    "covered": 5,
                    "miss": 5,
                    "cover": 50.0,
                    "branch_cover": 40.0,
                    "missing_lines": [4, 5, 6],
                }
            ],
            "coverage_totals": {"stmts": 10, "miss": 5, "cover": 50.0, "branch_cover": 40.0},
            "function_rows": [
                {
                    "file": "src/pkg/core.py",
                    "name": "process",
                    "start_line": 1,
                    "end_line": 10,
                    "covered": True,
                    "missing_lines": [],
                },
                {
                    "file": "src/pkg/core.py",
                    "name": "unused",
                    "start_line": 12,
                    "end_line": 14,
                    "covered": False,
                    "missing_lines": [13],
                },
            ],
        },
    )
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[result],
        duration=1.0,
        tem_score=3.96,
    )
    html_out = tmp_path / "report.html"
    generate_html_report(suite, html_out, project_name="TestProject", base_dir=tmp_path)
    content = html_out.read_text(encoding="utf-8")
    assert "Module Coverage Table" in content
    assert "src/pkg/core.cpp" not in content and "src/pkg/core.py" in content
    assert "coverage.py 실측" in content
    assert "50.0%" in content
    assert 'class="cov-table"' in content
    assert "4, 5, 6" in content
    assert "Function Coverage Table" in content
    assert "process()" in content
    assert "unused()" in content
    assert "✓ 실행됨" in content
    assert "✗ 미실행" in content


def test_html_report_coverage_table_estimated_notice(tmp_path: Path):
    result = EngineResult(
        engine_name="test",
        status=EngineStatus.PASS,
        summary="ok",
        extra={
            "passed_tests": 0,
            "total_tests": 0,
            "branch_coverage": 85.0,
            "function_coverage": 95.0,
            "tem_score": 4.75,
            "test_suites": [],
            "coverage_source": "estimated",
            "coverage_files": [],
        },
    )
    suite = VerificationSuiteResult(suite_status=EngineStatus.PASS, results=[result])
    html_out = tmp_path / "report.html"
    generate_html_report(suite, html_out, project_name="TestProject", base_dir=tmp_path)
    content = html_out.read_text(encoding="utf-8")
    assert "Module Coverage Table" in content
    assert "추정" in content
    assert 'class="cov-table"' not in content


def _type_suite(targets: list[InspectionTarget]) -> VerificationSuiteResult:
    return VerificationSuiteResult(
        suite_status=EngineStatus.WARN,
        results=[
            EngineResult(
                engine_name="type",
                status=EngineStatus.WARN,
                summary="0 Type Findings, 1 Warnings",
                targets=targets,
            )
        ],
        duration=0.1,
        tem_score=4.0,
    )


def test_type_engine_has_its_own_tab(tmp_path: Path):
    """The summary links out to the tab instead of inlining the target list.

    On a C++ project the type engine emits one SKIP per source file. Without a
    tab those landed in the summary inside an open <details>, so a handful of
    real findings sat underneath a wall of "not checked" entries.
    """
    skipped = [
        InspectionTarget(
            file_path=f"src/mod{i}.cpp",
            start_line=1,
            target_name="C++TypeCheck",
            status=EngineStatus.SKIP,
            message="C++ type checking is not implemented; source was not type-checked",
        )
        for i in range(6)
    ]
    out = tmp_path / "report.html"
    generate_html_report(_type_suite(skipped), out, project_name="TypeTab", base_dir=tmp_path)
    page = out.read_text(encoding="utf-8")

    assert 'data-tab-target="tab-type"' in page
    assert 'id="tab-type"' in page
    # The summary row offers a jump, not the expanded list.
    assert "View Static Type Results" in page
    # Files that were never checked are collapsed: nothing there is actionable.
    assert "not type-checked" in page


def test_type_tab_separates_findings_from_unchecked_files(tmp_path: Path):
    targets = [
        InspectionTarget(
            file_path="src/a.py",
            start_line=12,
            target_name="a.assign",
            status=EngineStatus.FAIL,
            message="Incompatible assignment",
        ),
        InspectionTarget(
            file_path="src/b.cpp",
            start_line=1,
            target_name="C++TypeCheck",
            status=EngineStatus.SKIP,
            message="C++ type checking is not implemented; source was not type-checked",
        ),
    ]
    out = tmp_path / "report.html"
    generate_html_report(_type_suite(targets), out, project_name="TypeTab", base_dir=tmp_path)
    page = out.read_text(encoding="utf-8")

    assert "Incompatible assignment" in page
    assert "Findings" in page
    assert "Not checked" in page
