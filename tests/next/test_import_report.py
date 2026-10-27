"""Imported evidence keeps provenance and cannot manufacture execution success."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.import_report import import_report
from ici.domain.finding import SourceSpan
from ici.domain.serialization import loads
from ici.reporting.offline_html import render


@pytest.mark.parametrize(
    "format,text,count",
    [
        (
            "junit",
            '<testsuite><testcase name="ok"/><testcase name="bad" file="src.py" line="4"><failure message="oops"/></testcase></testsuite>',
            2,
        ),
        ("lcov", "SF:src.cpp\nDA:2,0\nDA:3,1\nend_of_record\n", 1),
        (
            "sarif",
            json.dumps(
                {
                    "version": "2.1.0",
                    "runs": [
                        {
                            "tool": {"driver": {"name": "external"}},
                            "results": [
                                {
                                    "ruleId": "rule",
                                    "message": {"text": "issue"},
                                    "locations": [
                                        {
                                            "physicalLocation": {
                                                "artifactLocation": {"uri": "src.py"},
                                                "region": {"startLine": 3},
                                            }
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ),
            1,
        ),
    ],
)
def test_import_preserves_source_time_scope_without_gate(tmp_path, format, text, count):
    source = tmp_path / "report"
    source.write_text(text)
    result = import_report(source, format, scope="commit abc, unit suite")
    assert len(result["records"]) == count
    assert result["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["observed_at"] is None
    assert result["scope"] == "commit abc, unit suite"
    assert result["gate"] == "NOT_EVALUATED"
    assert result["schema_id"] == "ici.auxiliary"


@pytest.mark.parametrize(
    "text",
    [
        '<!DOCTYPE a [<!ENTITY a "x">]><testsuite/>',
        "<not-junit/>",
        '<testsuite><testcase file="../secret"/></testsuite>',
    ],
)
def test_import_rejects_ambiguous_or_external_evidence(tmp_path, text):
    source = tmp_path / "report"
    source.write_text(text)
    with pytest.raises(ValueError):
        import_report(source, "junit", scope="unit")


def test_cli_import_and_attach_leave_original_run_unchanged(tmp_path, monkeypatch):
    fixture = Path(__file__).parents[1] / "fixtures/ici-next/run-code-fail.json"
    original = fixture.read_text()
    monkeypatch.chdir(tmp_path)
    Path("result.json").write_text(original)
    Path("junit.xml").write_text('<testsuite><testcase name="&lt;script&gt;"/></testsuite>')
    runner = CliRunner()
    imported = runner.invoke(
        app,
        [
            "import-report",
            "junit.xml",
            "--format",
            "junit",
            "--scope",
            "unit",
            "--output",
            "aux.json",
        ],
    )
    assert imported.exit_code == 0, imported.output
    report = runner.invoke(
        app,
        ["report", "--result", "result.json", "--out", "report.html", "--auxiliary", "aux.json"],
    )
    assert report.exit_code == 0, report.output
    assert Path("result.json").read_text() == original
    page = Path("report.html").read_text()
    assert "Supporting evidence" in page and "NOT_EVALUATED" in page
    assert "&lt;script&gt;" in page
    assert not Path("ici.toml").exists()


def test_report_preserves_related_locations(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/ici-next/run-code-fail.json"
    stored = loads(fixture.read_text())
    finding = replace(
        stored.findings[0], related_locations=(SourceSpan("other.py", 12, label="origin"),)
    )
    page = render(replace(stored, findings=(finding,)))
    assert "Related locations" in page and "other.py:12 origin" in page
