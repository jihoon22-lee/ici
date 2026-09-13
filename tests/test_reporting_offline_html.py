"""One self-contained page for one stored result.

#206 item 5 has two hard edges — *"외부 URL이 없다"* and *"분석과 render 호출은
분리한다"* — and the tests for both are written to catch the general case rather
than a known example. A list of forbidden CDNs would pass the day somebody used
a different one.
"""

from __future__ import annotations

import re

import pytest

from ici.domain.enums import EvidenceLevel, GateVerdict, ScopeKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.result import (
    ExecutionSummary,
    GateOutcome,
    Producer,
    RunIdentity,
    RunResult,
    ScopeSelection,
)
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.workspace import SourceSnapshot
from ici.reporting.offline_html import render

DIGEST = "sha256:" + "a" * 64


def _finding(message: str = "`os` imported but unused") -> Finding:
    return Finding(
        fingerprint="sha256:" + "b" * 64,
        rule_id="ruff.F401",
        message=message,
        severity="warning",
        confidence="high",
        provider="ruff",
        primary_location=SourceSpan(path="src/app.py", start_line=3, start_column=8),
        evidence=EvidenceLevel.MEASURED,
    )


def _result(
    gate: GateOutcome | None = None,
    findings: tuple[Finding, ...] = (),
    metrics: tuple[Measurement, ...] = (),
    limitations: tuple[str, ...] = (),
    complete: bool = True,
) -> RunResult:
    return RunResult(
        run_id="run-1",
        producer=Producer(ici_version="0.0.0-next"),
        identity=RunIdentity(
            source=SourceSnapshot(digest=DIGEST), policy_digest=DIGEST, toolchain_digest=DIGEST
        ),
        scope=ScopeSelection(kind=ScopeKind.PARTIAL, selected_components=("app",)),
        execution=ExecutionSummary(required_complete=complete),
        gate=gate if gate is not None else GateOutcome(selected=GateVerdict.PASS),
        findings=findings,
        metrics=metrics,
        limitations=limitations,
    )


# --- offline --------------------------------------------------------------

# Any absolute URL, any scheme, plus protocol-relative ones. Not a list of
# known CDNs: that would pass the day somebody reached for a different one.
_URL = re.compile(r"""(?:[a-z][a-z0-9+.-]*:)?//[^\s"'<>]+""", re.IGNORECASE)


def test_the_page_contains_no_url_at_all() -> None:
    page = render(
        _result(
            gate=GateOutcome(
                selected=GateVerdict.FAIL, has_violations=True, reasons=("1 violation",)
            ),
            findings=(_finding(),),
            metrics=(Measurement(name="lines_code", value=12, unit="lines"),),
            limitations=("python.advice: not installed",),
        )
    )

    assert not _URL.findall(page), f"the page reaches outside: {_URL.findall(page)[:3]}"


@pytest.mark.parametrize("tag", ["<script", "<link", "<img", "<iframe", "@import"])
def test_the_page_loads_nothing(tag: str) -> None:
    assert tag not in render(_result()).lower()


def test_the_page_is_one_file(tmp_path) -> None:
    # Written alone into an empty directory and still complete.
    page = tmp_path / "report.html"
    page.write_text(render(_result()), encoding="utf-8")

    assert [p.name for p in tmp_path.iterdir()] == ["report.html"]
    assert page.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


# --- derived from the stored result, never re-measured --------------------


def test_rendering_a_result_that_was_saved_and_reloaded_is_identical() -> None:
    # The report is of a *stored* result: saving and reading back must not
    # change what the page says, or the JSON and the HTML beside it could
    # disagree while both looked official.
    result = _result(
        gate=GateOutcome(selected=GateVerdict.FAIL, has_violations=True, reasons=("1 violation",)),
        findings=(_finding(),),
        metrics=(Measurement(name="lines_code", value=12, unit="lines"),),
    )

    assert render(result) == render(loads(dumps(run_result_to_dict(result))))


def test_the_renderer_cannot_reach_the_executor() -> None:
    # The architecture rule: reporter는 provider를 실행하지 않는다. A reporter
    # that could run a tool would make looking at a result able to change it.
    import ast
    from pathlib import Path

    source = Path("src/ici/reporting/offline_html.py").read_text(encoding="utf-8")
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(name.startswith("ici.execution") for name in imported)
    assert not any(name.startswith("ici.adapters") for name in imported)


# --- nothing is dropped ---------------------------------------------------


def test_an_incomplete_run_says_why_on_the_page() -> None:
    # An INCOMPLETE run that does not say why is the report equivalent of an
    # empty pass.
    result = _result(
        gate=GateOutcome(
            selected=GateVerdict.INCOMPLETE, reasons=("python.lint: ruff is not available",)
        ),
        complete=False,
    )

    page = render(result)

    assert "INCOMPLETE" in page
    assert "ruff is not available" in page


def test_findings_carry_their_place() -> None:
    page = render(
        _result(
            gate=GateOutcome(
                selected=GateVerdict.FAIL, has_violations=True, reasons=("1 violation",)
            ),
            findings=(_finding(),),
        )
    )

    assert "ruff.F401" in page
    assert "src/app.py:3" in page


def test_a_recorded_limitation_is_on_the_page() -> None:
    page = render(_result(limitations=("python.advice: not installed",)))

    assert "not installed" in page


def test_no_findings_is_said_rather_than_left_blank() -> None:
    assert "none recorded" in render(_result())


def test_a_measurements_raw_pair_is_shown_beside_it() -> None:
    page = render(
        _result(
            metrics=(
                Measurement(name="lines_code", value=8, unit="lines", numerator=8, denominator=12),
            )
        )
    )

    assert "8 of 12" in page


# --- untrusted text -------------------------------------------------------


def test_a_finding_message_cannot_inject_markup() -> None:
    # The message comes from a tool reading somebody's source.
    page = render(
        _result(
            gate=GateOutcome(
                selected=GateVerdict.FAIL, has_violations=True, reasons=("1 violation",)
            ),
            findings=(_finding("<script>alert(1)</script>"),),
        )
    )

    assert "<script>" not in page
    assert "&lt;script&gt;" in page
