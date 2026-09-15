"""Executing a task graph — ordering, sharing, and bounded parallelism.

#208 items 5 through 7. The graph in :mod:`ici.application.graph` decides *what
may run when*; this module is the one place that actually runs it, and the
invariants it keeps are the same ones the graph promises:

**A unit runs once.** Work that several checks share executes a single time,
and each consumer's observation carries its own identity — the run count lives
in :class:`Execution`, so "the command ran once for three checks" is a fact of
the record rather than a retelling of three runs.

**Independence is the only parallelism.** Units inside one layer run
concurrently up to ``max_parallel``; units in later layers wait, because their
inputs are earlier layers' outputs. Mutating tasks additionally hold a lock on
every resource key they declare, so two units that write the same build
directory can never overlap no matter how the scheduler is tuned.

**A failed prerequisite blocks what needed it.** Selection-time blocking was
the graph's job; this is the runtime half. A unit whose dependency did not
succeed is not executed, and its consumers get an observation that says which
prerequisite failed — the same honest-blocked shape, now for the run itself.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from ici.adapters.providers.base import Provider, observe, unavailable
from ici.application.graph import TaskGraph, WorkUnit
from ici.domain.enums import TaskState
from ici.domain.observation import Observation
from ici.execution.cache import ObservationCache
from ici.execution.cancellation import Cancellation
from ici.execution.process import TaskOutcome, TaskSpec, run_task

__all__ = ["Execution", "Runner", "Scheduled", "run_graph"]

#: How a planned task is actually started. Injected so the flow can be tested
#: without a process, and so the one place that starts one stays visible.
#: Cancellation is checked between units here; interrupting a process already
#: running is the idk cancel contract's business (#224), not this runner's.
Runner = Callable[[TaskSpec], TaskOutcome]

#: What ici does itself for a check with no tool.
Analysis = Callable[[], Observation]

#: The cache identity of one unit — a key, or the reason reuse stays off.
#: Injected so the scheduler never resolves paths or measures files itself.
Identify = Callable[[WorkUnit], "tuple[str | None, str]"]


@dataclass(frozen=True)
class Execution:
    """One unit's record: how long, how it ended, or why it never started.

    This is the run-count half of #208 item 7 — executions and consumers are
    separate facts. A shared unit produces one ``Execution`` and several
    observations; a unit that never started produces one that says why.
    """

    unit: str
    consumers: tuple[str, ...]
    provider: str
    state: TaskState
    duration_seconds: float = 0.0
    #: Why the unit did not run — empty when it did.
    detail: str = ""

    @property
    def shared(self) -> bool:
        """Whether one execution served several checks."""

        return len(self.consumers) > 1


@dataclass(frozen=True)
class Scheduled:
    """What executing a graph produced.

    ``observations`` are per check identity — every consumer gets its own, so a
    check's record never depends on which other checks happened to share its
    work. ``executions`` are per unit — they are how many times anything
    actually ran.
    """

    observations: tuple[Observation, ...]
    executions: tuple[Execution, ...]


def run_graph(
    graph: TaskGraph,
    providers: Mapping[str, Provider],
    analyses: Mapping[str, Analysis],
    *,
    runner: Runner = run_task,
    environment: Mapping[str, str] | None = None,
    max_parallel: int = 4,
    cancellation: Cancellation | None = None,
    cache: ObservationCache | None = None,
    identify: Identify | None = None,
    run_id: str = "",
) -> Scheduled:
    """Execute a task graph, honouring its order and its sharing.

    Layer by layer: a unit starts only after everything it depends on has a
    successful observation. Within a layer, units run concurrently up to
    ``max_parallel``, with a lock held on each declared resource key so
    mutating work on one build directory cannot overlap.
    """

    observations: dict[str, Observation] = {}
    executions: list[Execution] = []
    finished: dict[str, Observation] = {}
    locks = _ResourceLocks()

    def attempt(unit: WorkUnit) -> tuple[Observation | None, Execution]:
        """One unit's run — returns its observation and record, or its refusal.

        Pure of ordering: dependencies were settled by earlier layers, so a
        unit either runs now or reports the reason it could not.
        """

        provider = _provider_name(unit)
        if cancellation is not None and cancellation.requested:
            reason = cancellation.reason or "cancelled before it started"
            return None, Execution(
                unit=unit.id,
                consumers=unit.consumers,
                provider=provider,
                state=TaskState.CANCELLED,
                detail=reason,
            )
        blocker = _failed_prerequisite(unit, finished)
        if blocker is not None:
            reason = f"prerequisite {blocker} did not run"
            return None, Execution(
                unit=unit.id,
                consumers=unit.consumers,
                provider=provider,
                state=TaskState.BLOCKED,
                detail=reason,
            )
        # #209: reuse is a lookup, never a verdict. A hit returns the stored
        # observation — the same evidence the run would have produced — and a
        # miss runs the task and keeps its answer for next time. A unit with
        # no key runs as usual with the reason on the record.
        key = reason = ""
        if cache is not None and identify is not None and not unit.is_internal:
            key, reason = identify(unit)
            if key is not None:
                read = cache.read(key)
                if read.hit and read.observation is not None:
                    detail = "cache hit"
                    if read.source_run:
                        detail += f" from run {read.source_run}"
                    return read.observation, Execution(
                        unit=unit.id,
                        consumers=unit.consumers,
                        provider=provider,
                        state=read.observation.state,
                        detail=detail,
                    )
                reason = read.reason

        observation = _perform(unit, providers, analyses, runner, environment, locks)
        if (
            cache is not None
            and key
            and observation.state is TaskState.SUCCEEDED
            and observation.evidence_is_complete
        ):
            cache.write(key, observation, run_id=run_id)
        return observation, Execution(
            unit=unit.id,
            consumers=unit.consumers,
            provider=provider,
            state=observation.state,
            duration_seconds=observation.duration_seconds,
            detail=reason,
        )

    for layer in graph.layers():
        if len(layer) == 1:
            results = [attempt(layer[0])]
        else:
            workers = max(1, min(max_parallel, len(layer)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(attempt, layer))
        for unit, (observation, execution) in zip(layer, results, strict=True):
            executions.append(execution)
            if observation is None:
                for consumer in unit.consumers:
                    observations[consumer] = unavailable(
                        _provider_name(unit),
                        consumer,
                        execution.detail,
                        state=execution.state,
                    )
                continue
            finished[unit.id] = observation
            for consumer in unit.consumers:
                observations[consumer] = replace(observation, task_id=consumer)

    for item in graph.blocked:
        observations[item.check.task_id] = unavailable(
            item.check.check.tool or "ici", item.check.task_id, item.reason
        )

    return Scheduled(
        observations=tuple(observations.values()),
        executions=tuple(executions),
    )


def _perform(
    unit: WorkUnit,
    providers: Mapping[str, Provider],
    analyses: Mapping[str, Analysis],
    runner: Runner,
    environment: Mapping[str, str] | None,
    locks: _ResourceLocks,
) -> Observation:
    if unit.is_internal:
        analysis = analyses.get(unit.id)
        if analysis is None:
            return unavailable("ici", unit.id, f"{unit.source.check.id} has no analysis registered")
        return analysis()

    assert unit.plan is not None
    provider = providers.get(unit.source.check.tool or "")
    if provider is None:
        return unavailable(
            unit.source.check.tool or "?",
            unit.id,
            f"{unit.source.check.tool} has no provider registered",
        )
    with locks.held(unit.task.resource_keys):
        outcome = runner(_executable_spec(unit.plan, environment))
    return observe(provider, unit.plan, outcome)


def _failed_prerequisite(unit: WorkUnit, finished: Mapping[str, Observation]) -> str | None:
    """The first dependency that did not complete, if any."""

    for dep in unit.depends_on:
        observation = finished.get(dep)
        if observation is None or observation.state is not TaskState.SUCCEEDED:
            return dep
    return None


def _provider_name(unit: WorkUnit) -> str:
    if unit.is_internal:
        return "ici"
    return unit.source.check.tool or "?"


def _executable_spec(plan, environment: Mapping[str, str] | None) -> TaskSpec:
    """Turn the planned task into the one the executor takes.

    Two models, on purpose and not by accident: the planned one is what a run
    *declares*, and is what plan, cache keys and the DAG are written against;
    this one is what a process *needs*. Collapsing them would make the declared
    plan depend on the shape of the runner.
    """

    task = plan.task
    overlay = dict(environment if environment is not None else os.environ)
    overlay.update(dict(task.env_overlay))
    return TaskSpec(
        argv=task.argv,
        name=task.id,
        cwd=Path(task.cwd),
        environment=overlay,
        timeout=task.timeout_seconds or 300.0,
    )


class _ResourceLocks:
    """One lock per declared resource key, held while a unit runs.

    ``resource_keys`` is how a task says "I write this directory" — a build
    unit and the instrumented rebuild that follows it must never overlap. The
    lock map is guarded because units acquire from pool threads.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def held(self, keys: tuple[str, ...]):
        """A context manager holding every key's lock, in sorted order.

        Sorted acquisition is what keeps two units that need the same pair of
        resources from deadlocking on each other's first lock.
        """

        return _HeldLocks([self._lock(key) for key in sorted(keys)])

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            found = self._locks.get(key)
            if found is None:
                found = self._locks[key] = threading.Lock()
            return found


class _HeldLocks:
    def __init__(self, locks: list[threading.Lock]) -> None:
        self._locks = locks

    def __enter__(self) -> None:
        for lock in self._locks:
            lock.acquire()

    def __exit__(self, *exc) -> None:
        for lock in reversed(self._locks):
            lock.release()
