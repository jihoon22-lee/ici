"""Focused regression tests for reporter and CLI hardening."""

import json
import re
from pathlib import Path

import pytest

from ici.core.models import (
    AnalysisMode,
    EngineResult,
    EngineStatus,
    EngineSupport,
    EvidenceState,
    FindingConfidence,
    InspectionTarget,
    SupportLanguage,
    SupportMatrix,
    ToolEvidence,
    VerificationSuiteResult,
    exit_code_for_status,
)
from ici.reporters.console import make_terminal_link, print_suite_dashboard
from ici.reporters.html import _get_status_theme, generate_html_report
from ici.reporters.html_assets import HTML_JS
from ici.reporters.json_rep import save_json_report
from ici.reporters.markdown import (
    emit_github_actions_annotations,
    generate_markdown_report,
    write_github_step_summary,
)


def _malicious_suite() -> VerificationSuiteResult:
    target = InspectionTarget(
        file_path="src/a'b</script>&.py",
        start_line=7,
        end_line=9,
        target_name="rule `name` <bad>",
        status=EngineStatus.ERROR,
        message="message | <script>\nnext `tick` % : ,",
        snippet="```\n</script>\n~~~\n",
        metrics={"line|count": 2},
    )
    result = EngineResult(
        engine_name="unsafe|engine",
        status=EngineStatus.ERROR,
        summary="summary | <script>\nnext `tick`",
        score=1.5,
        max_score=5.0,
        duration=0.25,
        targets=[target],
        raw_output="raw\noutput",
        extra={"nested": {"quote": "'&"}},
        required=True,
        evidence=EvidenceState.NOT_RUN,
        tool_evidence=[
            ToolEvidence(
                name="tool",
                path="/opt/tool",
                version="1.2",
                argv=["tool", "--name", "a'b"],
                returncode=2,
                timed_out=True,
                truncated=True,
                error="stderr\nerror",
            )
        ],
    )
    skipped = EngineResult(
        engine_name="optional",
        status=EngineStatus.SKIP,
        summary="not run",
        required=False,
        evidence=EvidenceState.NOT_RUN,
    )
    return VerificationSuiteResult(
        suite_status=EngineStatus.ERROR,
        results=[result, skipped],
        duration=1.25,
        tem_score=2.5,
    )


def test_json_v2_serializes_complete_engine_contract(tmp_path: Path):
    output = tmp_path / "report.json"
    save_json_report(_malicious_suite(), output)

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["schema_version"] == "ici.result/v3"
    assert data["error_count"] == 1
    assert data["skipped_count"] == 1
    result = data["results"][0]
    assert result["raw_output"] == "raw\noutput"
    assert result["required"] is True
    assert result["evidence"] == "NOT_RUN"
    assert result["tool_evidence"][0] == {
        "name": "tool",
        "path": "/opt/tool",
        "version": "1.2",
        "argv": ["tool", "--name", "a'b"],
        "returncode": 2,
        "timed_out": True,
        "truncated": True,
        "error": "stderr\nerror",
    }
    assert result["targets"][0]["snippet"] == "```\n</script>\n~~~\n"
    assert result["targets"][0]["metrics"] == {"line|count": 2}


def test_html_uses_data_attributes_and_escapes_untrusted_values(tmp_path: Path):
    output = tmp_path / "report.html"
    generate_html_report(_malicious_suite(), output, project_name="<Project>", base_dir=tmp_path)

    content = output.read_text(encoding="utf-8")
    assert "data-abs-path=" in content
    assert 'data-rel-path="src/a&#x27;b&lt;/script&gt;&amp;.py"' in content
    assert 'data-line="7"' in content
    assert "&lt;script&gt;" in content
    assert "<script>alert" not in content
    assert "</script>.py" not in content
    assert "onclick=" not in content
    assert "onchange=" not in content
    assert "onkeyup=" not in content
    assert re.search(r"\bon[a-z]+\s*=", content, re.IGNORECASE) is None
    assert "javascript:void" not in content
    assert "ERROR" in content
    assert "SKIP" in content
    assert "openLoc('" not in content
    assert "copyLoc('" not in content


def test_markdown_escapes_table_and_fenced_content():
    markdown = generate_markdown_report(_malicious_suite())

    assert "summary &#124; &lt;script&gt;" in markdown
    assert "unsafe&#124;engine" in markdown
    assert (chr(96) * 4 + "diff") in markdown
    assert "</script>" in markdown
    assert "**0 Failed**" in markdown
    assert "error_count" not in markdown
    assert "Errors" in markdown
    assert "Skipped" in markdown


@pytest.mark.parametrize("repo_url", [None, "https://github.com/owner/repo"])
def test_markdown_untrusted_delimiters_cannot_break_tables_or_links(repo_url: str | None):
    tick = chr(96)
    result = EngineResult(
        engine_name=f"x{tick}|y",
        status=EngineStatus.FAIL,
        summary=f"summary ](https://evil) {tick} | <unsafe>",
        extra={"metrics_summary": f"metrics ](https://evil) {tick} |"},
        targets=[
            InspectionTarget(
                file_path=f"x](https://evil) [z|{tick} .py",
                start_line=1,
                target_name=f"target ](evil) {tick}|",
                status=EngineStatus.FAIL,
                message=f"detail ](evil) {tick}|",
            )
        ],
    )
    suite = VerificationSuiteResult(suite_status=EngineStatus.FAIL, results=[result])

    markdown = generate_markdown_report(
        suite,
        repo_url=repo_url,
        commit_sha="abc123" if repo_url else None,
    )

    assert "](https://evil)" not in markdown
    assert "|y" not in markdown
    assert "&#124;" in markdown
    assert f"<code>x{tick}&#124;y</code>" in markdown
    assert "metrics ](https://evil)" not in markdown


def test_html_attribute_context_encodes_line_breaks_and_distinguishes_error(
    tmp_path: Path,
):
    target = InspectionTarget(
        file_path="src/a\r\nb.py",
        start_line=1,
        status=EngineStatus.ERROR,
        message="error",
    )
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.ERROR,
        results=[
            EngineResult(
                engine_name="error-engine",
                status=EngineStatus.ERROR,
                summary="error",
                targets=[target],
            )
        ],
    )

    output = tmp_path / "report.html"
    generate_html_report(suite, output, base_dir=tmp_path)
    content = output.read_text(encoding="utf-8")

    assert 'data-rel-path="src/a&#13;&#10;b.py"' in content
    assert 'data-rel-path="src/a\r\nb.py"' not in content
    assert _get_status_theme(EngineStatus.ERROR) != _get_status_theme(EngineStatus.FAIL)
    assert "WARN 및 FAIL 항목" not in content
    assert "ERROR/SKIP" in content


def test_html_support_matrix_is_issues_first_and_escapes_every_field(tmp_path: Path):
    matrix = SupportMatrix(
        project_languages=[SupportLanguage.PYTHON],
        project_frameworks=["qt<script>", "framework & name"],
        entries=[
            EngineSupport(
                engine_name="lint</script>",
                language=SupportLanguage.PYTHON,
                mode=AnalysisMode.TOOL_BACKED,
                active_mode=AnalysisMode.HEURISTIC,
                applicable=True,
                enabled=True,
                evidence=EvidenceState.ESTIMATED,
                confidence=FindingConfidence.MEDIUM,
                frameworks=["web'&"],
                required_tools=["ruff<tool>"],
                optional_tools=['tool"quote'],
                fallback_mode=AnalysisMode.HEURISTIC,
                limitations=["limit <one>", "limit & two"],
                reason="reason <script>alert('x')</script>",
            ),
            EngineSupport(
                engine_name="line",
                language=SupportLanguage.PYTHON,
                mode=AnalysisMode.EXACT,
                active_mode=AnalysisMode.EXACT,
                applicable=True,
                enabled=True,
                evidence=EvidenceState.MEASURED,
                confidence=FindingConfidence.EXACT,
                reason="measured",
            ),
        ],
    )
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
        support_matrix=matrix,
    )

    output = tmp_path / "support-report.html"
    generate_html_report(suite, output, base_dir=tmp_path)
    content = output.read_text(encoding="utf-8")

    assert 'id="tab-support"' in content
    assert "Project languages" in content
    assert "Project frameworks" in content
    assert "Declared mode" in content
    assert "Active mode" in content
    assert "Required tools" in content
    assert "Optional tools" in content
    assert "Fallback" in content
    assert "Limitations" in content
    assert "Reason" in content
    support_content = content[content.index('id="tab-support"') :]
    assert support_content.index("lint&lt;/script&gt;") < support_content.index(">line</strong>")
    assert "qt&lt;script&gt;" in content
    assert "framework &amp; name" in content
    assert "reason &lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in content
    assert "<script>alert('x')</script>" not in content
    assert "lint</script>" not in content
    assert "onclick=" not in content


def test_html_omits_support_tab_for_legacy_suite_without_matrix(tmp_path: Path):
    suite = VerificationSuiteResult(
        suite_status=EngineStatus.PASS,
        results=[EngineResult("line", EngineStatus.PASS, "ok")],
    )

    output = tmp_path / "legacy-report.html"
    generate_html_report(suite, output, base_dir=tmp_path)
    content = output.read_text(encoding="utf-8")

    assert 'id="tab-support"' not in content
    assert "Support &amp; Capabilities" not in content


def test_html_location_protocols_encode_path_segments_safely():
    assert "function encodeLocationPath(absPath)" in HTML_JS
    assert "replace(/\\\\/g, '/')" in HTML_JS
    assert ".split('/')" in HTML_JS
    assert "const encodedPath = encodeLocationPath(absPath);" in HTML_JS
    assert "const encodedQueryPath = encodeURIComponent(absPath)" in HTML_JS
    assert "const fileUri = toFileUri(encodedPath);" in HTML_JS
    assert "window.location.href = 'vscode://file/' + encodedPath + ':' + lineNo;" in HTML_JS
    assert "window.location.href = 'subl://' + encodedPath + ':' + lineNo;" in HTML_JS
    assert (
        "window.location.href = 'idea://open?file=' + encodedQueryPath + '&line=' + lineNo;"
        in HTML_JS
    )
    assert "window.open(fileUri, '_blank');" in HTML_JS


def test_console_counts_do_not_double_count_errors(capsys, tmp_path: Path):
    print_suite_dashboard(_malicious_suite(), tmp_path)

    output = capsys.readouterr().out
    assert "Fail: 0" in output
    assert "Error: 1" in output
    assert "Skip: 1" in output


def test_github_annotations_escape_command_data(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _malicious_suite().results[0]
    result.targets = [
        InspectionTarget(
            file_path="src/a,b:c.py",
            start_line="7\n::error file=evil",  # type: ignore[arg-type]
            status=EngineStatus.ERROR,
            message="message %\r\nnext",
        )
    ]
    emit_github_actions_annotations(
        VerificationSuiteResult(suite_status=EngineStatus.ERROR, results=[result])
    )

    output = capsys.readouterr().out
    assert "::error file=src/a%2Cb%3Ac.py,line=7%0A%3A%3Aerror file=evil::" in output
    assert "%0A" in output
    assert "%25" in output
    assert "%3A" in output
    assert "%2C" in output
    assert "::warning" not in output


def test_github_step_summary_is_deterministically_bounded_at_utf8_boundary(
    monkeypatch, tmp_path: Path
):
    """A pathological report must stay below GitHub's one MiB step-summary limit."""
    content = "## 결과 요약\n" + ("한글🙂/경로/" * 300_000)
    paths = [tmp_path / "summary-a.md", tmp_path / "summary-b.md"]
    rendered = []

    for path in paths:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
        write_github_step_summary(content)
        raw = path.read_bytes()
        # Reading as text also verifies that byte-oriented truncation did not
        # split a multi-byte UTF-8 scalar.
        decoded = raw.decode("utf-8")
        assert len(raw) < 1_048_576
        assert decoded.startswith("\n## 결과 요약")
        assert re.search(r"(?i)truncat", decoded)
        rendered.append(raw)

    assert rendered[0] == rendered[1]


def test_github_markdown_bounds_target_rows_and_keeps_actionable_first():
    targets = [
        InspectionTarget(
            file_path=f"src/pass_{index}.py",
            start_line=index + 1,
            status=EngineStatus.PASS,
            message=f"pass-{index}",
        )
        for index in range(150)
    ]
    targets.append(
        InspectionTarget(
            file_path="src/must_keep.py",
            start_line=1,
            status=EngineStatus.FAIL,
            message="must-keep-actionable",
        )
    )
    result = EngineResult(
        "bounded",
        EngineStatus.FAIL,
        "many targets",
        targets=targets,
    )

    markdown = generate_markdown_report(
        VerificationSuiteResult(suite_status=EngineStatus.FAIL, results=[result])
    )

    assert "must-keep-actionable" in markdown
    assert "pass-149" not in markdown
    assert "51 target row(s) omitted from this bounded GitHub view" in markdown
    assert "JSON and HTML reports retain the full inventory" in markdown


def test_github_annotations_are_bounded_and_prioritize_failures(monkeypatch, capsys):
    """Annotation volume is capped while actionable FAIL/ERROR targets survive."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    warning_targets = [
        InspectionTarget(
            file_path=f"src/warn_{index}.py",
            start_line=index + 1,
            status=EngineStatus.WARN,
            message=f"warning-{index}",
        )
        for index in range(200)
    ]
    skipped_targets = [
        InspectionTarget(
            file_path=f"src/skip_{index}.py",
            start_line=index + 1,
            status=EngineStatus.SKIP,
            message=f"skip-{index}",
        )
        for index in range(200)
    ]
    high_priority_targets = [
        InspectionTarget(
            file_path="src/error.py",
            start_line=10,
            status=EngineStatus.ERROR,
            message="must-keep-error",
        ),
        InspectionTarget(
            file_path="src/fail.py",
            start_line=20,
            status=EngineStatus.FAIL,
            message="must-keep-fail",
        ),
    ]
    result = EngineResult(
        "bounded",
        EngineStatus.ERROR,
        "many findings",
        targets=warning_targets + skipped_targets + high_priority_targets,
    )

    emit_github_actions_annotations(
        VerificationSuiteResult(suite_status=EngineStatus.ERROR, results=[result])
    )

    output = capsys.readouterr().out
    lines = [line for line in output.splitlines() if line]
    annotation_lines = [
        line for line in lines if line.startswith("::error") or line.startswith("::warning")
    ]
    assert len(annotation_lines) <= 50
    assert "::error file=src/error.py,line=10::[bounded] must-keep-error" in output
    assert "::error file=src/fail.py,line=20::[bounded] must-keep-fail" in output
    first_warning = output.index("::warning")
    assert output.index("must-keep-error") < first_warning
    assert output.index("must-keep-fail") < first_warning
    assert re.search(r"(?i)(omitt|truncat)", output)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (EngineStatus.PASS, 0),
        (EngineStatus.WARN, 0),
        (EngineStatus.FAIL, 1),
        (EngineStatus.ERROR, 1),
        (EngineStatus.SKIP, 2),
    ],
)
def test_exit_code_for_all_statuses(status: EngineStatus, expected: int):
    assert exit_code_for_status(status) == expected


def test_terminal_link_quotes_path_and_rich_markup(tmp_path: Path):
    link = make_terminal_link("src/a[b] c.py", 3, tmp_path)
    assert "a%5Bb%5D%20c.py" in link
    assert "a\\[b] c.py:3" in link
