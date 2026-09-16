"""In-process analysis bodies behind the internal ``ici next`` checks.

Each ``*.<kind>`` check that runs without a tool lands here: the dispatcher
that maps a planned check to its callable, and the counters/measurements
themselves. Everything returns an :data:`Analysis` — a ``() -> Observation``
callable the scheduler runs — so none of this executes at plan time.
"""

from __future__ import annotations

from pathlib import Path

from ici.application.plan import PlannedCheck
from ici.application.schedule import Analysis
from ici.domain.enums import TaskState
from ici.domain.observation import Measurement, Observation
from ici.domain.workspace import BuildUnit, Component
from ici.languages.artifacts import ArtifactRequest, verify_artifacts
from ici.languages.cycles import CycleRequest, measure_cycles
from ici.languages.deadcode import DeadRequest, measure_dead
from ici.languages.duplicates import DuplicateRequest, measure_duplicates
from ici.languages.hygiene import HygieneRequest, measure_hygiene
from ici.languages.metrics import MetricRequest, measure
from ici.languages.python.lines import LineRequest
from ici.languages.python.lines import count as count_lines
from ici.workspace import compile_units

__all__ = ["internal_analysis"]


def internal_analysis(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
    builds: tuple[BuildUnit, ...],
    metric_cache: dict,
) -> Analysis:
    """The in-process work behind a planned internal check.

    ``cpp.compile`` reads the compile-database service — the check's answer is
    the coverage the database proves, not a pass it grants. The ``*.complexity``
    and ``*.cognitive`` checks measure functions through the shared metrics
    primitive — one scan feeds both when both are selected (#218). Everything
    else is the line counter, which every language's ``*.line`` check shares.
    """

    if planned.check.id == "cpp.compile":
        return _compile_coverage(root, component, files, builds, planned.task_id)
    kind = planned.check.id.rpartition(".")[2]
    if kind in {"complexity", "cognitive"}:
        return _metric_counter(planned, component, files, component_root, root, kind, metric_cache)
    if kind == "cycle":
        return _cycle_counter(planned, component, files, component_root, root)
    if kind == "dup":
        return _dup_counter(planned, component, files, component_root, root)
    if kind in {"security", "resource", "exception"}:
        return _hygiene_counter(planned, component, files, component_root, root, kind)
    if kind == "dead":
        return _dead_counter(planned, component, files, component_root, root)
    if kind == "artifact":
        return _artifact_checker(planned, component, root, builds)
    return _line_counter(component_root, root, files, planned.task_id)


def _metric_counter(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
    kind: str,
    metric_cache: dict,
) -> Analysis:
    resolved = tuple(root / item for item in files)
    request = MetricRequest(
        kind=kind,
        language=planned.check.language,
        project_root=component_root,
        files=resolved,
        task_id=planned.task_id,
        component_id=component.id,
        cache=metric_cache,
    )
    return lambda: measure(request)


def _cycle_counter(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
) -> Analysis:
    resolved = tuple(root / item for item in files)
    request = CycleRequest(
        language=planned.check.language,
        project_root=component_root,
        files=resolved,
        task_id=planned.task_id,
        component_id=component.id,
    )
    return lambda: measure_cycles(request)


def _dup_counter(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
) -> Analysis:
    resolved = tuple(root / item for item in files)
    request = DuplicateRequest(
        language=planned.check.language,
        project_root=component_root,
        files=resolved,
        task_id=planned.task_id,
        component_id=component.id,
    )
    return lambda: measure_duplicates(request)


def _hygiene_counter(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
    kind: str,
) -> Analysis:
    resolved = tuple(root / item for item in files)
    request = HygieneRequest(
        kind=kind,
        project_root=component_root,
        files=resolved,
        task_id=planned.task_id,
        component_id=component.id,
    )
    return lambda: measure_hygiene(request)


def _dead_counter(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
) -> Analysis:
    resolved = tuple(root / item for item in files)
    request = DeadRequest(
        project_root=component_root,
        source_dirs=(component_root,),
        files=resolved,
        task_id=planned.task_id,
        component_id=component.id,
    )
    return lambda: measure_dead(request)


def _artifact_checker(
    planned: PlannedCheck,
    component: Component,
    root: Path,
    builds: tuple[BuildUnit, ...],
) -> Analysis:
    """The ``cpp.artifact`` check: verify the linked builds' declared outputs.

    Only artifact-declaring builds reach the request — the gate already
    blocked the no-contract case, so a run that got this far has globs to
    answer for.
    """

    linked = tuple(build for build in builds if build.id in component.build_ids and build.artifacts)
    request = ArtifactRequest(
        project_root=root,
        builds=linked,
        task_id=planned.task_id,
        component_id=component.id,
    )
    return lambda: verify_artifacts(request)


def _line_counter(
    component_root: Path, root: Path, files: tuple[str, ...], task_id: str
) -> Analysis:
    resolved = tuple(root / item for item in files)
    return lambda: count_lines(
        LineRequest(project_root=component_root, files=resolved, task_id=task_id)
    )


def _compile_coverage(
    root: Path,
    component: Component,
    files: tuple[str, ...],
    builds: tuple[BuildUnit, ...],
    task_id: str,
) -> Analysis:
    """The ``cpp.compile`` check's own read: coverage as measured fact."""

    def run() -> Observation:
        inputs = compile_units.load_compile_inputs(root, component, files, builds)
        limitations: list[str] = []
        if inputs.missing:
            shown = ", ".join(inputs.missing[:8])
            more = f" (+{len(inputs.missing) - 8} more)" if len(inputs.missing) > 8 else ""
            limitations.append(
                f"no compile invocation for {len(inputs.missing)}/{len(inputs.expected)} "
                f"translation units: {shown}{more}"
            )
        limitations += [item.message for item in inputs.diagnostics]
        if inputs.targets:
            resolved = sum(1 for target in inputs.targets if target.project)
            limitations = (
                [
                    *limitations,
                    f"qmake targets resolved: {resolved}/{len(inputs.targets)}",
                ]
                if resolved < len(inputs.targets)
                else limitations
            )
        # A capture that names fewer TUs than the scope holds — or generated
        # inputs that are gone — is incomplete evidence, not a failed check
        # and not a passing one: FAILED state here reads as INCOMPLETE at the
        # gate, which is what "partial coverage is not a full C++ pass" means.
        incomplete_capture = inputs.missing or any(
            item.code == "generated-input-missing" for item in inputs.diagnostics
        )
        return Observation(
            task_id=task_id,
            provider="ici.compile",
            state=TaskState.FAILED if incomplete_capture else TaskState.SUCCEEDED,
            measurements=(
                Measurement(
                    name="units.covered",
                    value=len(inputs.covered),
                    unit="translation units",
                    numerator=len(inputs.covered),
                    denominator=len(inputs.expected),
                ),
                Measurement(
                    name="units.generated",
                    value=len(inputs.generated),
                    unit="translation units",
                ),
            ),
            limitations=tuple(limitations),
        )

    return run
