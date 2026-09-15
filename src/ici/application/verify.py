"""Running a plan and saying what the result means.

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

Nothing here decides whether a tool's exit code was an answer. That was settled
in ``execution`` and read through the provider's contract, so a check cannot
disagree with the executor about whether there was a result to judge.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ici.adapters.providers.base import Provider, observe, unavailable
from ici.application.plan import Plan, PlannedCheck
from ici.domain.enums import GateVerdict, TaskState
from ici.domain.finding import Finding
from ici.domain.observation import Observation
from ici.domain.result import GateOutcome
from ici.execution.process import TaskOutcome, TaskSpec, run_task

__all__ = ["Verification", "verify"]

#: How a planned task is actually started. Injected so the flow can be tested
#: without a process, and so the one place that starts one stays visible.
Runner = Callable[[TaskSpec], TaskOutcome]

#: What ici does itself for a check with no tool.
Analysis = Callable[[], Observation]


@dataclass(frozen=True)
class Verification:
    """What a run did, what it found, and what that means."""

    gate: GateOutcome
    observations: tuple[Observation, ...]
    findings: tuple[Finding, ...]

    @property
    def exit_code(self) -> int:
        return self.gate.exit_code


def verify(
    plan: Plan | Iterable[Plan],
    providers: Mapping[str, Provider],
    analyses: Mapping[str, Analysis],
    runner: Runner = run_task,
    environment: Mapping[str, str] | None = None,
) -> Verification:
    """Run everything the plans intend to run, then judge it once.

    A workspace run is several component plans judged as one gate: an
    INCOMPLETE anywhere holds the verdict, and findings pool across
    components. Plans stay per-component because check ids are unique only
    within one component's selection.
    """

    plans = (plan,) if isinstance(plan, Plan) else tuple(plan)

    observations: list[Observation] = []
    incomplete: list[str] = []

    for planned in (item for p in plans for item in p.checks):
        observation = _perform(planned, providers, analyses, runner, environment)
        observations.append(observation)
        reason = _incompleteness(planned, observation)
        if reason:
            incomplete.append(reason)

    findings = tuple(item for observation in observations for item in observation.findings)
    return Verification(
        gate=_judge(tuple(incomplete), findings),
        observations=tuple(observations),
        findings=findings,
    )


def _perform(
    planned: PlannedCheck,
    providers: Mapping[str, Provider],
    analyses: Mapping[str, Analysis],
    runner: Runner,
    environment: Mapping[str, str] | None,
) -> Observation:
    if planned.blocked:
        return unavailable(planned.check.tool or "ici", planned.task_id, planned.blocked)
    if planned.is_internal:
        analysis = analyses.get(planned.task_id)
        if analysis is None:
            return unavailable(
                "ici", planned.task_id, f"{planned.check.id} has no analysis registered"
            )
        return analysis()

    assert planned.task is not None
    provider = providers.get(planned.check.tool or "")
    if provider is None:
        return unavailable(
            planned.check.tool or "?",
            planned.check.id,
            f"{planned.check.tool} has no provider registered",
        )
    outcome = runner(_executable_spec(planned, environment))
    return observe(provider, planned.task, outcome)


def _executable_spec(planned: PlannedCheck, environment: Mapping[str, str] | None) -> TaskSpec:
    """Turn the planned task into the one the executor takes.

    Two models, on purpose and not by accident: the planned one is what a run
    *declares*, and is what plan, cache keys and the DAG are written against;
    this one is what a process *needs*. Collapsing them would make the declared
    plan depend on the shape of the runner.
    """

    assert planned.task is not None
    task = planned.task.task
    overlay = dict(environment if environment is not None else os.environ)
    overlay.update(dict(task.env_overlay))
    return TaskSpec(
        argv=task.argv,
        name=task.id,
        cwd=Path(task.cwd),
        environment=overlay,
        timeout=task.timeout_seconds or 300.0,
    )


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


def _judge(incomplete: tuple[str, ...], findings: tuple[Finding, ...]) -> GateOutcome:
    has_violations = bool(findings)
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
            reasons=(f"{len(findings)} violation(s) found",),
        )
    return GateOutcome(selected=GateVerdict.PASS)
