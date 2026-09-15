"""The task graph a plan becomes — ordering, dedup, and honest blocking.

#208 items 3 and 4. The flat :class:`~ici.application.plan.Plan` says *which*
checks were asked for; this module turns the work behind them into the DAG that
decides *when* each may run. Three properties are the whole point:

**Edges come from inputs, not from check names.** A check declares the logical
inputs it consumes (``needs``), another declares what it produces
(``provides``), and the graph wires consumer→producer through them. A check
that named its prerequisite directly would silently rewire itself the day a
different provider started supplying the input.

**One command runs once.** Two checks — or two components — that plan the same
task get one unit: same provider, kind, argv, cwd, environment and inputs is
one execution with several consumers, which is what keeps a shared Ruff run or
a shared qmake prepare from being paid for twice. ``share_key`` is the equality
test because everything that could change the answer is inside it.

**A prerequisite that did not run blocks what needed it.** If the producing
check was never selected or ended up blocked, the consumer does not run either —
and the reason names the prerequisite, not just "blocked". A plan that dropped
the consumer would describe a run smaller than the one that was asked for.

Nothing in this module executes. It only arranges.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.adapters.providers.base import ProviderPlan
from ici.application.plan import PlannedCheck
from ici.domain.tasks import TaskSpec
from ici.languages.checks import CheckDefinition

__all__ = ["Blocked", "GraphError", "TaskGraph", "WorkUnit", "build_graph"]


class GraphError(ValueError):
    """The declared work does not form a runnable graph.

    A configuration error, not a verification failure: the run was never
    started, so nothing here may read as a verdict about the code.
    """


@dataclass(frozen=True)
class WorkUnit:
    """One schedulable unit — a shared process execution or an internal check.

    ``source`` is the planned check that defined the work; ``plan`` — its
    provider plan — is what runs, or ``None`` when ici performs the check
    itself. ``consumers`` are the planned checks' task ids, the identities
    their observations will carry. Several consumers on one unit is the
    sharing #208 asks for: the work ran once, and each consumer's result
    records that fact rather than recounting the execution.
    """

    id: str
    consumers: tuple[str, ...]
    #: The planned check whose plan this unit executes.
    source: PlannedCheck
    #: Unit ids this unit needs finished first. Derived from the check-level
    #: input edges, never invented inside the graph.
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a work unit must have an id")
        if not self.consumers:
            raise ValueError(f"work unit {self.id} serves no check")
        if self.id in self.depends_on:
            raise ValueError(f"work unit {self.id} cannot depend on itself")

    @property
    def plan(self) -> ProviderPlan | None:
        return self.source.task

    @property
    def is_internal(self) -> bool:
        return self.source.is_internal

    @property
    def task(self) -> TaskSpec:
        """The process spec. Only callable on a process unit."""

        assert self.plan is not None
        return self.plan.task

    @property
    def mutating(self) -> bool:
        """Whether running this unit writes outside ici's own run directory."""

        return self.plan is not None and self.plan.task.mutating


@dataclass(frozen=True)
class Blocked:
    """A check that will not run, and the reason it gave."""

    check: PlannedCheck
    reason: str


@dataclass(frozen=True)
class TaskGraph:
    """The ordered, deduplicated plan the scheduler executes."""

    units: tuple[WorkUnit, ...]
    blocked: tuple[Blocked, ...]

    def unit_of(self, check_task_id: str) -> WorkUnit | None:
        """The unit serving a planned check's identity, if it has one."""

        for unit in self.units:
            if check_task_id in unit.consumers:
                return unit
        return None

    def layers(self) -> tuple[tuple[WorkUnit, ...], ...]:
        """Ready-now groups, earliest first.

        A layer is a set of units whose dependencies all landed in earlier
        layers, so units inside one layer are independent of each other.
        """

        remaining = {unit.id: set(unit.depends_on) for unit in self.units}
        done: set[str] = set()
        layers: list[tuple[WorkUnit, ...]] = []
        while remaining:
            ready = tuple(
                unit for unit in self.units if unit.id in remaining and remaining[unit.id] <= done
            )
            if not ready:  # build() already proved acyclic; this is unreachable
                raise GraphError("the task graph contains a cycle")  # pragma: no cover
            layers.append(ready)
            done.update(unit.id for unit in ready)
            for unit in ready:
                del remaining[unit.id]
        return tuple(layers)


def build_graph(
    checks: tuple[PlannedCheck, ...],
    catalog: tuple[CheckDefinition, ...] | None = None,
) -> TaskGraph:
    """Wire the planned checks into a graph of shared executions.

    ``checks`` is everything the run selected, blocked entries included.
    ``catalog`` is every check the registry *could* have selected — it is what
    lets "the prerequisite was never asked for" be told apart from "nothing
    anywhere produces that input", and when it is omitted the two collapse into
    the same blocked reason.
    """

    producers = _producers(checks)
    available_producers = _catalog_producers(catalog)

    blocked: list[Blocked] = []
    runnable: list[PlannedCheck] = []
    edges: dict[str, list[str]] = {}

    for planned in checks:
        if planned.blocked:
            blocked.append(Blocked(check=planned, reason=planned.blocked))
            continue
        if _wire(planned, producers, available_producers, edges, blocked):
            continue
        runnable.append(planned)

    # A blocked producer blocks its consumers; repeat until nothing new is
    # blocked, because a chain a→b→c blocks c through b.
    moved = True
    while moved:
        moved = False
        blocked_ids = {item.check.task_id for item in blocked}
        still: list[PlannedCheck] = []
        for planned in runnable:
            prerequisite = next(
                (dep for dep in edges.get(planned.task_id, []) if dep in blocked_ids),
                None,
            )
            if prerequisite is None:
                still.append(planned)
                continue
            blocked.append(
                Blocked(check=planned, reason=f"prerequisite {prerequisite} did not run")
            )
            moved = True
        runnable = still

    units = _units(runnable, edges)
    return TaskGraph(units=units, blocked=tuple(blocked))


def _producers(checks: tuple[PlannedCheck, ...] | None) -> dict[str, PlannedCheck]:
    """Which selected check produces each logical input.

    Two selected checks claiming the same input is a definition error, not a
    choice: whichever the graph picked, the other half of the run would be
    reading output nobody produced.
    """

    found: dict[str, PlannedCheck] = {}
    for planned in checks or ():
        for name in planned.check.provides:
            previous = found.get(name)
            if previous is not None:
                raise GraphError(
                    f"input {name!r} is produced by both {previous.check.id} and {planned.check.id}"
                )
            found[name] = planned
    return found


def _catalog_producers(
    catalog: tuple[CheckDefinition, ...] | None,
) -> dict[str, CheckDefinition]:
    """Every check that could produce an input, selected or not.

    This is the difference between "the prerequisite was disabled" and "the
    prerequisite does not exist": both leave the consumer blocked, but the
    reason tells the user whether the fix is a config change or a missing
    feature.
    """

    found: dict[str, CheckDefinition] = {}
    for check in catalog or ():
        for name in check.provides:
            found.setdefault(name, check)
    return found


def _wire(
    planned: PlannedCheck,
    producers: dict[str, PlannedCheck],
    available: dict[str, CheckDefinition],
    edges: dict[str, list[str]],
    blocked: list[Blocked],
) -> bool:
    """Record a check's prerequisite edges, or block it if it cannot be fed."""

    reasons: list[str] = []
    for need in planned.check.needs:
        producer = producers.get(need)
        if producer is None:
            supplier = available.get(need)
            if supplier is not None:
                reasons.append(f"needs {need}, which {supplier.id} was not selected to produce")
            else:
                reasons.append(f"needs {need}, which no selected check produces")
            continue
        if producer.task_id == planned.task_id:
            raise GraphError(f"check {planned.check.id} cannot consume its own {need}")
        edges.setdefault(planned.task_id, []).append(producer.task_id)
    if reasons:
        blocked.append(Blocked(check=planned, reason="; ".join(reasons)))
    return bool(reasons)


def _units(runnable: list[PlannedCheck], edges: dict[str, list[str]]) -> tuple[WorkUnit, ...]:
    """Fold runnable checks into units, sharing identical work.

    Two passes, because each depends on the other. First every check's task is
    folded by ``share_key`` — internal checks fold to themselves — so each
    consumer identity maps to the unit that will serve it; then each unit's
    dependencies are computed as the union of its consumers' edges, translated
    through that same fold. A shared execution must wait on anything any of its
    consumers needs.
    """

    groups: dict[object, list[PlannedCheck]] = {}
    for planned in runnable:
        key = ("internal", planned.task_id) if planned.is_internal else planned.task.task.share_key
        groups.setdefault(key, []).append(planned)

    group_of = {member.task_id: key for key, members in groups.items() for member in members}

    units: list[WorkUnit] = []
    for key, members in groups.items():
        first = members[0]
        deps = {
            groups[dep_group][0].task_id
            for member in members
            for producer in edges.get(member.task_id, ())
            if (dep_group := group_of[producer]) != key
        }
        units.append(
            WorkUnit(
                id=first.task_id,
                consumers=tuple(member.task_id for member in members),
                source=first,
                depends_on=tuple(sorted(deps)),
            )
        )

    _validate(units)
    return tuple(units)


def _validate(units: list[WorkUnit]) -> None:
    """Prove the graph can run: known deps, single owners, no cycles."""

    ids = {unit.id for unit in units}
    if len(ids) != len(units):
        raise GraphError("the task graph contains duplicate unit ids")
    for unit in units:
        unknown = [dep for dep in unit.depends_on if dep not in ids]
        if unknown:
            raise GraphError(
                f"task {unit.id} depends on {sorted(unknown)}, which are not in the plan"
            )
    owners: dict[str, str] = {}
    for unit in units:
        if unit.plan is None:
            continue
        for output in unit.task.output_specs:
            previous = owners.get(output)
            if previous is not None:
                raise GraphError(f"output {output!r} is claimed by both {previous} and {unit.id}")
            owners[output] = unit.id

    # Kahn's pass on the unit ids — a cycle means some layer can never start,
    # and the error needs to name who is stuck rather than hang.
    remaining = {unit.id: set(unit.depends_on) for unit in units}
    done: set[str] = set()
    while remaining:
        ready = [unit_id for unit_id, deps in remaining.items() if deps <= done]
        if not ready:
            stuck = ", ".join(sorted(remaining))
            raise GraphError(f"the task graph contains a cycle: {stuck}")
        done.update(ready)
        for unit_id in ready:
            del remaining[unit_id]
