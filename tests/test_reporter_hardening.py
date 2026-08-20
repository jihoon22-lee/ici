"""Focused regression tests for reporter and CLI hardening."""

import json
from pathlib import Path

import pytest

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    InspectionTarget,
    ToolEvidence,
    VerificationSuiteResult,
    exit_code_for_status,
)
from ici.reporters.console import make_terminal_link
from ici.reporters.html import generate_html_report
from ici.reporters.json_rep import save_json_report
from ici.reporters.markdown import emit_github_actions_annotations, generate_markdown_report


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
    assert data["schema_version"] == "ici.result/v2"
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
    assert "data-rel-path=\"src/a&#x27;b&lt;/script&gt;&amp;.py\"" in content
    assert "data-line=\"7\"" in content
    assert "<script>" not in content
    assert "</script>.py" not in content
    assert "onclick=" not in content
    assert "onchange=" not in content
    assert "onkeyup=" not in content
    assert "javascript:void" not in content
    assert "ERROR" in content
    assert "SKIP" in content
    assert "openLoc('" not in content
    assert "copyLoc('" not in content


def test_markdown_escapes_table_and_fenced_content():
    markdown = generate_markdown_report(_malicious_suite())

    assert "summary \\| &lt;script&gt;" in markdown
    assert "unsafe\\|engine" in markdown
    assert "</script>" in markdown.split("~~~", 1)[-1]
    assert "error_count" not in markdown
    assert "Errors" in markdown
    assert "Skipped" in markdown


def test_github_annotations_escape_command_data(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    result = _malicious_suite().results[0]
    emit_github_actions_annotations(
        VerificationSuiteResult(suite_status=EngineStatus.ERROR, results=[result])
    )

    output = capsys.readouterr().out
    assert "::error file=src/a'b</script>&.py,line=7::" in output
    assert "%0A" in output
    assert "%25" in output
    assert "%3A" in output
    assert "%2C" in output
    assert "::warning" not in output


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
    assert "a\\[b\\] c.py:3" in link
