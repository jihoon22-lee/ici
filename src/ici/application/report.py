"""Turning a run into the result that gets saved, and nothing more.

#206 item 5 asks that analysis and rendering be separate calls. This module is
the first half: it assembles a :class:`~ici.domain.result.RunResult` from what
the run observed. It does not render, and the renderer does not run anything —
so a report is always a report *of a stored result*, and there is no path where
looking at a result quietly re-runs a tool and shows a different one.

The assembly is mostly copying, with one piece of real work:
:class:`~ici.domain.result.ExecutionSummary` has to say which tasks were blocked
and which failed, and the domain model then refuses the combinations that do not
make sense — a run with blocked work is not complete, and an incomplete run
cannot carry a passing gate. Those invariants are why this is assembled in one
place rather than filled in by whoever happens to be writing a report.
"""

from __future__ import annotations

import hashlib

from ici.application.tem import (
    FORMULA_VERSION,
    counts_from,
    inputs_from,
)
from ici.application.tem import (
    aggregate as tem_aggregate,
)
from ici.application.tem import (
    calculate as tem_calculate,
)
from ici.application.tem import (
    component_of as tem_component_of,
)
from ici.application.verify import Verification
from ici.domain.enums import GateVerdict, ScopeKind, TaskState
from ici.domain.result import (
    ExecutionSummary,
    Producer,
    RunIdentity,
    RunResult,
    ScopeSelection,
)
from ici.domain.workspace import SourceSnapshot

__all__ = ["assemble"]


def assemble(
    verification: Verification,
    run_id: str,
    ici_version: str,
    source: SourceSnapshot,
    policy_digest: str,
    toolchain_digest: str,
    component_ids: tuple[str, ...] = (),
    languages: tuple[str, ...] = (),
    scope: ScopeKind = ScopeKind.PARTIAL,
    bundle_digest: str | None = None,
    required_components: tuple[str, ...] | None = None,
    omitted_components: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    cancelled: bool = False,
) -> RunResult:
    """Build the storable result for one verification.

    ``required_components`` defaults to the selected ones because a single
    component run *is* its whole scope; a workspace run passes the workspace's
    required set instead, so a ``--component`` subset cannot read as having
    covered it. ``limitations`` carries what the run observed outside any one
    task — a scope nothing had a check for, inputs that moved mid-run.
    """

    tem = tem_metrics(verification.observations)
    blocked = tuple(
        observation.task_id
        for observation in verification.observations
        if observation.state in (TaskState.BLOCKED, TaskState.CANCELLED)
    )
    failed = tuple(
        observation.task_id
        for observation in verification.observations
        if observation.state is TaskState.FAILED
    )
    complete = verification.gate.selected is not GateVerdict.INCOMPLETE

    return RunResult(
        run_id=run_id,
        producer=Producer(ici_version=ici_version, bundle_digest=bundle_digest),
        identity=RunIdentity(
            source=source, policy_digest=policy_digest, toolchain_digest=toolchain_digest
        ),
        scope=ScopeSelection(
            kind=scope,
            selected_components=component_ids,
            selected_languages=languages,
            required_components=(
                component_ids if required_components is None else required_components
            ),
            omitted_components=omitted_components,
            # A partial run never claims the workspace was satisfied. R05: a
            # partial pass must not read as a workspace pass, and this is
            # stored rather than derived so a consumer cannot recompute it
            # from an incomplete list and reach the wrong conclusion.
            full_required_satisfied=scope is ScopeKind.FULL and complete,
        ),
        execution=ExecutionSummary(
            required_complete=complete,
            cancelled=cancelled,
            blocked_task_ids=blocked if not complete else (),
            failed_task_ids=failed,
            reused_task_ids=tuple(
                consumer
                for item in verification.executions
                if item.detail.startswith("cache hit")
                for consumer in item.consumers
            ),
        ),
        gate=verification.gate,
        findings=verification.findings,
        metrics=tuple(
            measurement
            for observation in verification.observations
            for measurement in observation.measurements
        )
        + tem[0],
        limitations=tuple(
            limitation
            for observation in verification.observations
            for limitation in observation.limitations
        )
        + tuple(limitations)
        + tem[1],
    )


def tem_metrics(observations) -> tuple[tuple, tuple[str, ...]]:
    """The run's TEM, computed from evidence the checks already produced.

    One score per component that measured tests and coverage, plus a
    workspace score merged from raw counts — never an average of component
    percentages (#219 items 2-3). A component whose evidence cannot produce
    a score is a limitation, not a 0 or a 100.
    """

    from ici.domain.observation import Measurement

    grouped: dict[str, list] = {}
    for observation in observations:
        grouped.setdefault(tem_component_of(observation.task_id), []).extend(
            observation.measurements
        )

    metrics: list = []
    limitations: list[str] = []
    components: list = []
    for name, measurements in sorted(grouped.items()):
        inputs = inputs_from(measurements)
        result = tem_calculate(inputs)
        label = name or "workspace"
        if result.score is None:
            limitations.append(f"{label}: TEM N/A — {result.reason}")
            continue
        metrics.append(
            Measurement(
                name=f"tem.{label}",
                value=result.score,
                unit=f"{FORMULA_VERSION} {result.coverage_label}{' est' if result.estimated else ''}",
            )
        )
        components.append(counts_from(label, measurements))

    if len(components) > 1:
        merged, excluded = tem_aggregate(tuple(components))
        workspace = tem_calculate(merged)
        if workspace.score is not None:
            metrics.append(
                Measurement(
                    name="tem.workspace",
                    value=workspace.score,
                    unit=FORMULA_VERSION,
                )
            )
        for name in excluded:
            limitations.append(f"{name}: excluded from workspace TEM — no raw coverage counts")
        if workspace.score is None:
            limitations.append(f"workspace: TEM N/A — {workspace.reason}")
    return tuple(metrics), tuple(limitations)


def digest_of(text: str) -> str:
    """A digest in the form the domain model requires."""

    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
