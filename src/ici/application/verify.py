"""Judging what a run did, once the graph has run it.

This is where #196's gate rules are applied, and the order of the questions is
the whole of it:

1. **Did every required check complete?** If not the verdict is INCOMPLETE, and
   it stays INCOMPLETE even when violations were also found.
2. **Were violations found?** If so the verdict is FAIL, and ``has_violations``
   is true either way.
3. Otherwise PASS.

Asking them the other way round is the failure the spec names. A run that could
not finish but found nothing would report PASS -- clean, on the strength of
having not looked. And a run that could not finish *and* found something would
report FAIL, which is a complete verdict the run never reached; so INCOMPLETE
outranks FAIL, and the findings are kept regardless. *"미완료가 있다는 이유로
확인한 문제를 버리지 않는다."*

The running itself is the task graph's job (#208): ``verify`` wires the
selected checks into it and lets :func:`~ici.application.schedule.run_graph`
decide what executes, what shares, and what blocks. Nothing here decides
whether a tool's exit code was an answer — that was settled in ``execution``
and read through the provider's contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from ici.adapters.providers.base import Provider
from ici.application.graph import build_graph
from ici.application.plan import Plan, PlannedCheck
from ici.application.schedule import (
    Analysis,
    Execution,
    Identify,
    OnExecution,
    OnStarted,
    Runner,
    run_graph,
)
from ici.application.suppressions import apply_suppressions
from ici.config.composition import EffectiveSuppression
from ici.domain.enums import GateVerdict, TaskState
from ici.domain.finding import Finding
from ici.domain.observation import Observation
from ici.domain.result import GateOutcome
from ici.execution.cache import ObservationCache
from ici.execution.cancellation import Cancellation
from ici.execution.process import run_task

__all__ = ["Analysis", "Runner", "Verification", "verify"]


@dataclass(frozen=True)
class Verification:
    """What a run did, what it found, and what that means."""

    gate: GateOutcome
    observations: tuple[Observation, ...]
    findings: tuple[Finding, ...]
    #: One record per executed unit — run counts, durations, cache reuse and
    #: skip reasons (#209 item 7: hit/miss/disabled must be reportable).
    executions: tuple[Execution, ...] = ()

    @property
    def exit_code(self) -> int:
        return self.gate.exit_code


def verify(
    plan: Plan | Iterable[Plan],
    providers: Mapping[str, Provider],
    analyses: Mapping[str, Analysis],
    runner: Runner = run_task,
    environment: Mapping[str, str] | None = None,
    *,
    max_parallel: int = 4,
    cancellation: Cancellation | None = None,
    cache: ObservationCache | None = None,
    identify: Identify | None = None,
    run_id: str = "",
    on_execution: OnExecution | None = None,
    on_started: OnStarted | None = None,
    suppressions: Iterable[EffectiveSuppression] = (),
    task_components: Mapping[str, str] = {},
) -> Verification:
    """Run everything the plans intend to run, then judge it once.

    A workspace run is several component plans judged as one gate: an
    INCOMPLETE anywhere holds the verdict, and findings pool across
    components. Plans stay per-component because check ids are unique only
    within one component's selection; inside the graph they collapse to shared
    units where the work is identical.

    ``task_components`` maps a planned task id to the component it was planned
    under. Providers may stamp ``component_id`` on findings themselves; where
    they do not, this map is what keeps a pooled finding attributable to the
    component it was measured in (#224).
    """

    plans = (plan,) if isinstance(plan, Plan) else tuple(plan)
    checks = tuple(item for p in plans for item in p.checks)

    scheduled = run_graph(
        build_graph(checks),
        providers,
        analyses,
        runner=runner,
        environment=environment,
        max_parallel=max_parallel,
        cancellation=cancellation,
        cache=cache,
        identify=identify,
        run_id=run_id,
        on_execution=on_execution,
        on_started=on_started,
    )
    by_id = {item.task_id: item for item in scheduled.observations}
    observations = tuple(by_id[planned.task_id] for planned in checks)

    incomplete = tuple(
        reason for planned in checks if (reason := _incompleteness(planned, by_id[planned.task_id]))
    )
    findings = apply_suppressions(
        _unique(
            attributed
            for observation in observations
            for attributed in _attribute(observation, task_components)
        ),
        suppressions,
    )
    required_fingerprints = {
        item.fingerprint
        for planned in checks
        if planned.check.required
        for item in by_id[planned.task_id].findings
    }
    return Verification(
        gate=_judge(incomplete, findings, required_fingerprints),
        observations=observations,
        findings=findings,
        executions=scheduled.executions,
    )


def _attribute(observation: Observation, components: Mapping[str, str]) -> tuple[Finding, ...]:
    """Give a finding the component its task was planned under.

    A provider that knows its component stamps it; one that does not — a
    parser reading shared tool output — still gets attribution from the plan,
    so a consumer never has to reverse-engineer it out of a task id.
    """

    component = components.get(observation.task_id)
    if component is None:
        return observation.findings
    return tuple(
        item if item.component_id is not None else replace(item, component_id=component)
        for item in observation.findings
    )


def _unique(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Drop findings a shared execution reported to several consumers.

    ``fingerprint`` is exactly the identity a finding carries for this — the
    first report is kept, later duplicates of the same physical finding are
    the same fact told again.
    """

    seen: set[str] = set()
    kept: list[Finding] = []
    for finding in findings:
        if finding.fingerprint in seen:
            continue
        seen.add(finding.fingerprint)
        kept.append(finding)
    return tuple(kept)


def _incompleteness(planned: PlannedCheck, observation: Observation) -> str:
    """Why this check leaves the run unfinished, or an empty string.

    Read from the observation rather than from whether anything raised: a task
    that succeeded but was truncated or timed out produced partial evidence,
    and a check resting on it has not completed.
    """

    if not planned.check.required:
        return ""
    if observation.state is TaskState.SUCCEEDED and observation.evidence_is_complete:
        return ""
    detail = observation.limitations[0] if observation.limitations else observation.state.value
    return f"{planned.task_id}: {detail}"


def _judge(
    incomplete: tuple[str, ...],
    findings: tuple[Finding, ...],
    required_fingerprints: set[str],
) -> GateOutcome:
    """The verdict, asked in the order the spec allows it to be asked.

    Only a *blocking* finding fails the gate: one that was measured, not
    suppressed, and came from a check the policy requires. Estimated or
    suppressed findings — and findings reported only by checks the root
    marked optional — are kept in the result but stay advisory; counting
    them would let a heuristic or an opt-out fail a run it was never
    allowed to fail (#219 item 4).
    """

    blocking = tuple(
        item
        for item in findings
        if item.counts_against_gate and item.fingerprint in required_fingerprints
    )
    has_violations = bool(blocking)
    if incomplete:
        # Kept together deliberately. The findings are real whether or not the
        # run finished, and INCOMPLETE is what stops a partial run being
        # reported as a verdict somebody can act on.
        return GateOutcome(
            selected=GateVerdict.INCOMPLETE,
            has_violations=has_violations,
            reasons=incomplete,
        )
    if has_violations:
        return GateOutcome(
            selected=GateVerdict.FAIL,
            has_violations=True,
            reasons=(f"{len(blocking)} violation(s) found",),
        )
    return GateOutcome(selected=GateVerdict.PASS)
