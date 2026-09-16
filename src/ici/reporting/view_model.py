"""The stored result as renderable data, and nothing else (#222 PR A).

Two rules govern everything here, and both are about who is allowed to know
what:

- The view model is built once from the saved ``RunResult``. A renderer
  receives this and only this — no re-running, no re-computation, no source
  reads — so the page cannot disagree with the JSON it claims to show.
- The model keeps every fact the run recorded, including the awkward ones:
  blocked and cancelled work, suppressed findings, the findings a baseline
  could not classify. A template may *fold* a fact; it may not discover the
  fact was never offered.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.domain.enums import BaselineState, GateVerdict
from ici.domain.finding import Finding
from ici.domain.result import RunResult

__all__ = ["FindingRow", "MetricRow", "RunView", "view_model"]

_VERDICT_NOTE = {
    GateVerdict.PASS: "required checks completed with no violations",
    GateVerdict.FAIL: "required checks completed and found violations",
    GateVerdict.INCOMPLETE: "the run did not finish what it was asked to do",
    GateVerdict.NOT_EVALUATED: "no verdict was reached for this scope",
}


@dataclass(frozen=True)
class FindingRow:
    """One finding flattened for display — identity first, marks kept."""

    rule: str
    location: str
    severity: str
    message: str
    provider: str
    component: str
    evidence: str
    confidence: str
    fingerprint: str
    suppressed: bool
    suppression_reason: str
    suppression_origin: str
    baseline_state: str  # "new" | "unchanged" | ""


@dataclass(frozen=True)
class MetricRow:
    name: str
    value: str
    unit: str
    raw: str  # "numerator of denominator", when the counts travelled


@dataclass(frozen=True)
class BaselineView:
    """The comparison outcome, or the reason there is none."""

    state: str  # "none" | "comparable" | "incompatible"
    origin: str = ""
    reason: str = ""
    new: int = 0
    unchanged: int = 0
    resolved: int = 0
    carried: int = 0


@dataclass(frozen=True)
class RunView:
    """Everything a result page may show, in display order."""

    run_id: str
    ici_version: str
    bundle_digest: str
    verdict: str
    verdict_note: str
    exit_code: int
    has_violations: bool
    scope_kind: str
    selected_components: tuple[str, ...]
    omitted_components: tuple[str, ...]
    required_components: tuple[str, ...]
    full_required_satisfied: bool
    selected_languages: tuple[str, ...]
    required_complete: bool
    cancelled: bool
    blocked_tasks: tuple[str, ...]
    failed_tasks: tuple[str, ...]
    reused_tasks: tuple[str, ...]
    gate_reasons: tuple[str, ...]
    findings: tuple[FindingRow, ...]
    suppressed_count: int
    metrics: tuple[MetricRow, ...]
    limitations: tuple[str, ...]
    baseline: BaselineView


def view_model(result: RunResult) -> RunView:
    """Project the stored result into display data. Pure; reads no files."""

    states = _baseline_states(result)
    findings = tuple(
        _row(finding, states.get(finding.fingerprint, "")) for finding in result.findings
    )
    baseline = result.baseline
    if baseline is None:
        baseline_view = BaselineView(state="none")
    elif baseline.state is BaselineState.COMPARABLE:
        baseline_view = BaselineView(
            state="comparable",
            origin=baseline.origin,
            new=len(baseline.new),
            unchanged=len(baseline.unchanged),
            resolved=len(baseline.resolved),
            carried=len(baseline.carried),
        )
    else:
        baseline_view = BaselineView(
            state="incompatible", origin=baseline.origin, reason=baseline.reason
        )
    return RunView(
        run_id=result.run_id,
        ici_version=result.producer.ici_version,
        bundle_digest=result.producer.bundle_digest or "",
        verdict=result.gate.selected.value,
        verdict_note=_VERDICT_NOTE[result.gate.selected],
        exit_code=result.gate.exit_code,
        has_violations=result.gate.has_violations,
        scope_kind=result.scope.kind.value,
        selected_components=result.scope.selected_components,
        omitted_components=result.scope.omitted_components,
        required_components=result.scope.required_components,
        full_required_satisfied=result.scope.full_required_satisfied,
        selected_languages=result.scope.selected_languages,
        required_complete=result.execution.required_complete,
        cancelled=result.execution.cancelled,
        blocked_tasks=result.execution.blocked_task_ids,
        failed_tasks=result.execution.failed_task_ids,
        reused_tasks=result.execution.reused_task_ids,
        gate_reasons=result.gate.reasons,
        findings=findings,
        suppressed_count=sum(1 for row in findings if row.suppressed),
        metrics=tuple(
            MetricRow(
                name=item.name,
                value=_number(item.value),
                unit=item.unit,
                raw=(
                    f"{item.numerator} of {item.denominator}"
                    if item.numerator is not None and item.denominator
                    else ""
                ),
            )
            for item in result.metrics
        ),
        limitations=tuple(item for item in result.limitations if item),
        baseline=baseline_view,
    )


def _row(finding: Finding, baseline_state: str) -> FindingRow:
    location = f"{finding.primary_location.path}:{finding.primary_location.start_line}"
    return FindingRow(
        rule=finding.rule_id,
        location=location,
        severity=finding.severity,
        message=finding.message,
        provider=finding.provider,
        component=finding.component_id or "",
        evidence=finding.evidence.value,
        confidence=finding.confidence,
        fingerprint=finding.fingerprint,
        suppressed=finding.suppression.suppressed,
        suppression_reason=finding.suppression.reason,
        suppression_origin=finding.suppression.origin,
        baseline_state=baseline_state,
    )


def _baseline_states(result: RunResult) -> dict[str, str]:
    if result.baseline is None or result.baseline.state is not BaselineState.COMPARABLE:
        return {}
    states = {fingerprint: "new" for fingerprint in result.baseline.new}
    states.update({fingerprint: "unchanged" for fingerprint in result.baseline.unchanged})
    return states


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
