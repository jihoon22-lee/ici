"""The view model and what each result state looks like on the page (#222).

The fixtures are the worked examples — every stored state gets rendered and
the page must say what the envelope said. The point of the layer split is
that these tests assert facts, not markup: ``view_model`` is what a template
sees, so an omission here is a fact no renderer could recover.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from ici.domain.serialization import run_result_from_dict
from ici.reporting.offline_html import render
from ici.reporting.view_model import view_model

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ici-next"


def _stored(name: str):
    return run_result_from_dict(json.loads((FIXTURES / f"{name}.json").read_text()))


def test_the_model_is_built_once_from_the_stored_result() -> None:
    result = _stored("run-code-fail")
    view = view_model(result)

    assert view.verdict == "FAIL"
    assert view.run_id == result.run_id
    assert len(view.findings) == len(result.findings)


def test_a_passed_full_run_says_its_scope_was_satisfied() -> None:
    view = view_model(_stored("run-success"))
    page = render(_stored("run-success"))

    assert view.verdict == "PASS"
    assert view.full_required_satisfied
    assert "required scope satisfied" in page
    assert ">yes<" in page or "yes" in page


def test_an_incomplete_run_names_the_work_that_did_not_finish() -> None:
    result = _stored("run-required-incomplete")
    view = view_model(result)
    page = render(result)

    assert view.verdict == "INCOMPLETE"
    assert not view.required_complete
    assert view.blocked_tasks or view.gate_reasons
    for task in view.blocked_tasks:
        assert task in page
    for reason in view.gate_reasons:
        assert html.escape(reason, quote=True) in page


def test_a_cancelled_run_says_it_was_cancelled() -> None:
    result = _stored("run-cancelled")
    view = view_model(result)
    page = render(result)

    assert view.cancelled
    assert "cancelled" in page


def test_a_partial_run_lists_the_components_it_did_not_select() -> None:
    result = _stored("run-partial-selection")
    view = view_model(result)
    page = render(result)

    assert view.scope_kind == "partial"
    assert view.omitted_components
    assert "not selected" in page
    for component in view.omitted_components:
        assert component in page


def test_a_finding_keeps_provider_evidence_and_location_on_the_page() -> None:
    result = _stored("run-code-fail")
    view = view_model(result)
    page = render(result)
    (row,) = view.findings

    assert row.provider == "ruff"
    assert row.location.endswith("demo.py:12")
    assert row.severity in page
    assert row.rule in page


def test_a_suppressed_finding_shows_its_reason_and_origin() -> None:
    from ici.domain import Finding, FindingSuppression, SourceSpan
    from ici.domain.enums import EvidenceLevel
    from test_next_serialization import minimal_result

    finding = Finding(
        fingerprint="fp-sup",
        rule_id="ruff.F401",
        message="unused import",
        severity="warning",
        confidence="high",
        provider="ruff",
        primary_location=SourceSpan(path="a.py", start_line=1),
        evidence=EvidenceLevel.MEASURED,
        suppression=FindingSuppression(
            suppressed=True, kind="config", reason="PROJ-9", origin="ici.toml"
        ),
    )
    result = minimal_result(findings=(finding,))
    view = view_model(result)
    page = render(result)

    assert view.suppressed_count == 1
    assert view.findings[0].suppressed
    assert "PROJ-9" in page
    assert "ici.toml" in page
    assert "suppressed" in page


def test_an_estimated_finding_is_not_flattened_to_measured() -> None:
    # The evidence column marks the exception: MEASURED is the quiet default,
    # anything else is stated — a heuristic result must not read as a
    # measurement on the page any more than it may at the gate.
    result = _stored("run-partial-selection")
    view = view_model(result)
    page = render(result)

    estimated = [row for row in view.findings if row.evidence != "MEASURED"]
    for row in estimated:
        assert row.evidence in page


def test_a_comparable_baseline_shows_its_delta() -> None:
    from ici.domain import BaselineComparison, BaselineState
    from test_next_serialization import minimal_result

    result = minimal_result(
        baseline=BaselineComparison(
            state=BaselineState.COMPARABLE,
            origin="old.json",
            new=("a",),
            unchanged=("b",),
            resolved=("c",),
            carried=("d",),
        )
    )
    page = render(result)

    assert "1 new, 1 unchanged, 1 resolved, 1 carried" in page
    assert "old.json" in page


def test_an_incompatible_baseline_shows_the_reason_not_a_delta() -> None:
    from ici.domain import BaselineComparison, BaselineState
    from test_next_serialization import minimal_result

    result = minimal_result(
        baseline=BaselineComparison(
            state=BaselineState.INCOMPATIBLE,
            origin="old.json",
            reason="policy changed",
        )
    )
    page = render(result)

    assert "incompatible" in page
    assert "policy changed" in page
    assert "resolved" not in page.split("incompatible")[1].split("Baseline")[0]


def test_rendering_is_deterministic_for_the_same_result() -> None:
    result = _stored("run-partial-selection")

    assert render(result) == render(result)


def test_every_recorded_limitation_is_on_the_page() -> None:
    result = _stored("run-partial-selection")
    view = view_model(result)
    page = render(result)

    for limitation in view.limitations:
        assert html.escape(limitation, quote=True) in page
