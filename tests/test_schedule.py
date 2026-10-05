"""The scheduler — #208 items 5 through 7.

What is tested here is the difference between *ran*, *shared*, and *refused*:
identical work executes once for all its consumers, a failed prerequisite
blocks what needed it at run time, and a cancelled run says so rather than
leaving unstarted tasks to guess.
"""

from __future__ import annotations

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.application.graph import build_graph
from ici.application.plan import PlannedCheck
from ici.application.schedule import run_graph
from ici.domain.enums import TaskKind, TaskState
from ici.domain.observation import Observation
from ici.domain.tasks import TaskSpec
from ici.execution.cancellation import Cancellation
from ici.execution.process import Outcome, TaskOutcome


class _StubProvider:
    name = "stub"

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        return ParsedOutput()


def _check(check_id: str, *, needs=(), provides=(), tool="stub"):
    from ici.languages.checks import CheckDefinition

    return CheckDefinition(
        id=check_id,
        title=check_id,
        language="python",
        tool=tool,
        needs=needs,
        provides=provides,
    )


def _planned(check_id: str, *, argv=None, needs=(), provides=(), mutating=False):
    kind = TaskKind.PREPARE if mutating else TaskKind.ANALYZE
    spec = TaskSpec(
        id=check_id,
        kind=kind,
        provider="stub",
        argv=argv or (check_id,),
        cwd="/project",
        resource_keys=("build-dir",) if mutating else (),
        cacheable=not mutating,
    )
    return PlannedCheck(
        check=_check(check_id, needs=needs, provides=provides),
        task=ProviderPlan(task=spec),
        task_id=check_id,
    )


def _finished(spec) -> TaskOutcome:
    return TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=0, duration=0.01)


def test_shared_work_runs_once_for_all_consumers() -> None:
    a = _planned("a.lint", argv=("ruff", "check", "."))
    b = _planned("b.lint", argv=("ruff", "check", "."))
    graph = build_graph((a, b))
    ran: list[str] = []

    scheduled = run_graph(
        graph,
        providers={"stub": _StubProvider()},
        analyses={},
        runner=lambda spec: ran.append(spec.name) or _finished(spec),
    )

    assert len(scheduled.executions) == 1
    assert scheduled.executions[0].shared
    assert {obs.task_id for obs in scheduled.observations} == {"a.lint", "b.lint"}
    assert all(obs.state is TaskState.SUCCEEDED for obs in scheduled.observations)


def test_a_failed_prerequisite_blocks_its_consumer_at_runtime() -> None:
    build = _planned("app.build", provides=("build:x",))
    test = _planned("app.test", needs=("build:x",))
    graph = build_graph((build, test))

    def fail(spec):
        return TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=3, duration=0.01)

    scheduled = run_graph(
        graph,
        providers={"stub": _StubProvider()},
        analyses={},
        runner=fail,
    )

    states = {obs.task_id: obs.state for obs in scheduled.observations}
    assert states["app.build"] is TaskState.FAILED
    assert states["app.test"] is TaskState.BLOCKED
    skipped = next(e for e in scheduled.executions if e.unit == "app.test")
    assert "app.build" in skipped.detail


def test_cancellation_stops_unstarted_units() -> None:
    a = _planned("a.lint", argv=("tool", "a"))
    b = _planned("b.lint", argv=("tool", "b"))
    # b depends on a so they land in different layers; cancel during a.
    b = PlannedCheck(
        check=_check("b.lint", needs=("out:a",)),
        task=ProviderPlan(
            task=TaskSpec(
                id="b.lint",
                kind=TaskKind.ANALYZE,
                provider="stub",
                argv=("tool", "b"),
                cwd="/project",
            )
        ),
        task_id="b.lint",
    )
    a = PlannedCheck(
        check=_check("a.lint", provides=("out:a",)),
        task=ProviderPlan(task=a.task.task),
        task_id="a.lint",
    )
    cancellation = Cancellation()

    def run_and_cancel(spec):
        cancellation.cancel("stop requested")
        return _finished(spec)

    scheduled = run_graph(
        build_graph((a, b)),
        providers={"stub": _StubProvider()},
        analyses={},
        runner=run_and_cancel,
        cancellation=cancellation,
    )

    states = {obs.task_id: obs.state for obs in scheduled.observations}
    assert states["b.lint"] is TaskState.CANCELLED


def test_an_internal_check_runs_its_analysis() -> None:
    line = PlannedCheck(check=_check("app.line", tool=None), task_id="app.line")
    obs = Observation(task_id="app.line", provider="ici", state=TaskState.SUCCEEDED)

    scheduled = run_graph(
        build_graph((line,)),
        providers={},
        analyses={"app.line": lambda: obs},
    )

    assert scheduled.observations == (obs,)
    assert scheduled.executions[0].state is TaskState.SUCCEEDED


def test_an_internal_check_without_analysis_is_blocked() -> None:
    line = PlannedCheck(check=_check("app.line", tool=None), task_id="app.line")

    scheduled = run_graph(build_graph((line,)), providers={}, analyses={})

    assert scheduled.observations[0].state is TaskState.BLOCKED
    assert "no analysis registered" in scheduled.observations[0].limitations[0]


def test_mutating_units_do_not_overlap_on_a_shared_resource(tmp_path) -> None:
    # Two mutating tasks declaring the same resource key must serialize; a
    # lock violation would let the second observe the first mid-write.
    seen: list[str] = []
    first = _planned("a.build", mutating=True, argv=("tool", "a"))
    second = _planned("b.build", mutating=True, argv=("tool", "b"))
    graph = build_graph((first, second))

    def slow(spec):
        import time

        seen.append(f"start:{spec.name}")
        time.sleep(0.01)
        seen.append(f"end:{spec.name}")
        return _finished(spec)

    scheduled = run_graph(
        graph,
        providers={"stub": _StubProvider()},
        analyses={},
        runner=slow,
        max_parallel=4,
    )

    assert len(scheduled.executions) == 2
    # Strict alternation: each start is followed by its own end before the
    # next start — overlap would interleave them.
    starts = [item for item in seen if item.startswith("start")]
    ends = [item for item in seen if item.startswith("end")]
    assert len(starts) == len(ends) == 2
    assert seen.index(starts[0]) < seen.index(ends[0]) < seen.index(starts[1])


# --- the started/completed boundary, #224 --------------------------------
#
# An event consumer pairs task-started with task-completed to know what is
# in flight. "Started" therefore means the work began — a unit that was
# cancelled, blocked, or answered from cache never started, and reporting it
# as started would show progress that never happened.


def _lifecycle(graph, **kwargs):
    started: list[str] = []
    completed: list[str] = []
    scheduled = run_graph(
        graph,
        providers={"stub": _StubProvider()},
        analyses={},
        runner=kwargs.pop("runner", _finished),
        on_started=lambda unit: started.append(unit.id),
        on_execution=lambda execution: completed.append(execution.unit),
        **kwargs,
    )
    return scheduled, started, completed


def test_a_unit_that_runs_reports_started_then_completed() -> None:
    _, started, completed = _lifecycle(build_graph((_planned("a.lint"),)))

    assert started == ["a.lint"]
    assert completed == ["a.lint"]


def test_a_blocked_unit_never_reports_started() -> None:
    build = _planned("app.build", provides=("build:x",))
    test = _planned("app.test", needs=("build:x",))

    def fail(spec):
        return TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=3, duration=0.01)

    _, started, completed = _lifecycle(build_graph((build, test)), runner=fail)

    # The build started and failed; the test's refusal is a completion —
    # "its disposition is final" — without a start that never happened.
    assert started == ["app.build"]
    assert "app.test" in completed


def test_a_cancelled_unit_never_reports_started() -> None:
    cancellation = Cancellation()
    cancellation.cancel("stop requested")

    _, started, completed = _lifecycle(
        build_graph((_planned("a.lint"),)), cancellation=cancellation
    )

    assert started == []
    assert completed == ["a.lint"]


def test_a_cache_hit_never_reports_started(tmp_path) -> None:
    from ici.execution.cache import ObservationCache

    cache = ObservationCache(tmp_path / "cache")
    stored = Observation(task_id="a.lint", provider="stub", state=TaskState.SUCCEEDED)
    cache.write("k" * 64, stored, run_id="run-1")

    _, started, completed = _lifecycle(
        build_graph((_planned("a.lint"),)),
        cache=cache,
        identify=lambda unit: ("k" * 64, ""),
    )

    assert started == []
    assert completed == ["a.lint"]


# --- crash containment -----------------------------------------------------
#
# A bug inside a provider's parser, an internal analysis, or the runner is
# that unit's failure — never the run's. The scheduler converts the exception
# into a FAILED observation: the gate reads it as missing evidence, siblings
# keep what they produced, and dependents block on the usual rule.


def test_a_crashing_analysis_fails_its_unit_not_the_run() -> None:
    line = PlannedCheck(check=_check("app.line", tool=None), task_id="app.line")
    other = _planned("other.lint", argv=("tool", "b"))

    def boom() -> Observation:
        raise RuntimeError("parser ate itself")

    scheduled = run_graph(
        build_graph((line, other)),
        providers={"stub": _StubProvider()},
        analyses={"app.line": boom},
        runner=_finished,
    )

    states = {obs.task_id: obs.state for obs in scheduled.observations}
    assert states["app.line"] is TaskState.FAILED
    assert states["other.lint"] is TaskState.SUCCEEDED
    crash = next(obs for obs in scheduled.observations if obs.task_id == "app.line")
    assert "internal error" in crash.limitations[0]
    assert "RuntimeError" in crash.limitations[0]
    execution = next(e for e in scheduled.executions if e.unit == "app.line")
    assert execution.state is TaskState.FAILED


def test_a_crashing_parser_fails_its_unit_not_the_run() -> None:
    class ExplodingProvider:
        name = "exploding"

        def parse(self, outcome: TaskOutcome) -> ParsedOutput:
            raise ValueError("cannot read output")

    a = PlannedCheck(
        check=_check("a.lint", tool="exploding"),
        task=ProviderPlan(
            task=TaskSpec(
                id="a.lint",
                kind=TaskKind.ANALYZE,
                provider="exploding",
                argv=("tool", "a"),
                cwd="/project",
            )
        ),
        task_id="a.lint",
    )
    b = _planned("b.lint", argv=("tool", "b"))

    scheduled = run_graph(
        build_graph((a, b)),
        providers={"exploding": ExplodingProvider(), "stub": _StubProvider()},
        analyses={},
        runner=_finished,
    )

    states = {obs.task_id: obs.state for obs in scheduled.observations}
    assert states["a.lint"] is TaskState.FAILED
    assert states["b.lint"] is TaskState.SUCCEEDED


def test_a_crashed_prerequisite_blocks_its_consumer() -> None:
    build = PlannedCheck(
        check=_check("app.build", provides=("build:x",), tool=None), task_id="app.build"
    )
    test = _planned("app.test", needs=("build:x",))

    def boom() -> Observation:
        raise RuntimeError("nope")

    scheduled = run_graph(
        build_graph((build, test)),
        providers={"stub": _StubProvider()},
        analyses={"app.build": boom},
        runner=_finished,
    )

    states = {obs.task_id: obs.state for obs in scheduled.observations}
    assert states["app.build"] is TaskState.FAILED
    assert states["app.test"] is TaskState.BLOCKED


def test_a_crashing_runner_fails_its_unit_not_the_run() -> None:
    def boom(spec) -> TaskOutcome:
        raise OSError("spawn machinery broke")

    scheduled = run_graph(
        build_graph((_planned("a.lint", argv=("tool", "a")),)),
        providers={"stub": _StubProvider()},
        analyses={},
        runner=boom,
    )

    assert scheduled.observations[0].state is TaskState.FAILED
    assert "OSError" in scheduled.observations[0].limitations[0]
    assert scheduled.executions[0].state is TaskState.FAILED
