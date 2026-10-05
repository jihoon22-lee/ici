"""The task graph — #208 items 3 and 4.

What is tested here is the shape of honesty the issue demands: shared work is
one node, a prerequisite that never ran blocks its consumer with a reason that
names it, and malformed wiring is a plan-time error rather than a runtime
surprise.
"""

from __future__ import annotations

import pytest

from ici.adapters.providers.base import ProviderPlan
from ici.application.graph import GraphError, build_graph
from ici.application.plan import PlannedCheck
from ici.domain.enums import TaskKind
from ici.domain.tasks import TaskSpec
from ici.languages.checks import CheckDefinition


def _check(
    check_id: str,
    *,
    needs: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    tool: str | None = "tool",
) -> CheckDefinition:
    return CheckDefinition(
        id=check_id,
        title=check_id,
        language="python",
        tool=tool,
        needs=needs,
        provides=provides,
    )


def _spec(task_id: str, *, argv: tuple[str, ...] = (), kind=TaskKind.ANALYZE):
    return TaskSpec(id=task_id, kind=kind, provider="tool", argv=argv, cwd="/project")


def _planned(check: CheckDefinition, task_id: str, **spec_kwargs) -> PlannedCheck:
    argv = spec_kwargs.pop("argv", (task_id,))
    spec = _spec(task_id, argv=argv, **spec_kwargs)
    return PlannedCheck(
        check=check,
        task=ProviderPlan(task=spec),
        task_id=task_id,
    )


def test_independent_tasks_land_in_one_layer() -> None:
    a = _planned(_check("a.line"), "a.line", argv=("tool", "a"))
    b = _planned(_check("b.line"), "b.line", argv=("tool", "b"))

    graph = build_graph((a, b))

    assert len(graph.layers()) == 1
    assert {unit.id for unit in graph.layers()[0]} == {"a.line", "b.line"}


def test_a_consumer_waits_for_its_producer() -> None:
    build = _planned(_check("app.build", provides=("build:release",)), "app.build")
    test = _planned(_check("app.test", needs=("build:release",)), "app.test")

    graph = build_graph((test, build))
    layers = graph.layers()

    assert [unit.id for unit in layers[0]] == ["app.build"]
    assert [unit.id for unit in layers[1]] == ["app.test"]
    assert graph.unit_of("app.test").depends_on == ("app.build",)


def test_identical_tasks_share_one_node() -> None:
    first = _planned(_check("a.lint"), "a.lint", argv=("ruff", "check", "."))
    second = _planned(_check("b.lint"), "b.lint", argv=("ruff", "check", "."))

    graph = build_graph((first, second))

    assert len(graph.units) == 1
    assert graph.units[0].task.id == "a.lint"
    assert sorted(graph.units[0].consumers) == ["a.lint", "b.lint"]


def test_different_argv_never_shares() -> None:
    first = _planned(_check("a.lint"), "a.lint", argv=("ruff", "check", "a"))
    second = _planned(_check("b.lint"), "b.lint", argv=("ruff", "check", "b"))

    graph = build_graph((first, second))

    assert len(graph.units) == 2


def test_a_missing_producer_blocks_the_consumer() -> None:
    test = _planned(_check("app.test", needs=("build:release",)), "app.test")

    graph = build_graph((test,))

    assert graph.units == ()
    assert graph.blocked[0].check.task_id == "app.test"
    assert "no selected check produces" in graph.blocked[0].reason


def test_an_unselected_producer_is_named_in_the_reason() -> None:
    test = _planned(_check("app.test", needs=("build:release",)), "app.test")
    catalog = (_check("app.build", provides=("build:release",)),)

    graph = build_graph((test,), catalog=catalog)

    assert "app.build was not selected" in graph.blocked[0].reason


def test_a_blocked_producer_blocks_its_consumer() -> None:
    build = PlannedCheck(
        check=_check("app.build", provides=("build:release",)),
        blocked="make is not available",
        task_id="app.build",
    )
    test = _planned(_check("app.test", needs=("build:release",)), "app.test")

    graph = build_graph((build, test))

    reasons = {item.check.task_id: item.reason for item in graph.blocked}
    assert reasons["app.test"] == "prerequisite app.build did not run"


def test_propagation_walks_the_whole_chain() -> None:
    build = PlannedCheck(
        check=_check("app.build", provides=("build:release",)),
        blocked="make is not available",
        task_id="app.build",
    )
    package = _planned(
        _check("app.package", needs=("build:release",), provides=("package:app",)),
        "app.package",
    )
    publish = _planned(_check("app.publish", needs=("package:app",)), "app.publish")

    graph = build_graph((build, package, publish))

    reasons = {item.check.task_id: item.reason for item in graph.blocked}
    assert "app.package" in reasons["app.publish"]


def test_two_producers_of_one_input_is_a_plan_error() -> None:
    a = _planned(_check("a.build", provides=("build:x",)), "a.build")
    b = _planned(_check("b.build", provides=("build:x",)), "b.build")

    with pytest.raises(GraphError, match="produced by both"):
        build_graph((a, b))


def test_two_tasks_claiming_one_output_is_a_plan_error() -> None:
    spec_a = TaskSpec(
        id="a.run",
        kind=TaskKind.ANALYZE,
        provider="tool",
        argv=("tool", "a"),
        cwd="/project",
        output_specs=("report.json",),
    )
    spec_b = TaskSpec(
        id="b.run",
        kind=TaskKind.ANALYZE,
        provider="tool",
        argv=("tool", "b"),
        cwd="/project",
        output_specs=("report.json",),
    )
    a = PlannedCheck(check=_check("a"), task=ProviderPlan(task=spec_a), task_id="a.run")
    b = PlannedCheck(check=_check("b"), task=ProviderPlan(task=spec_b), task_id="b.run")

    with pytest.raises(GraphError, match="claimed by both"):
        build_graph((a, b))


def test_a_cycle_is_a_plan_error() -> None:
    a = _planned(_check("a.x", needs=("in:b",), provides=("in:a",)), "a.x")
    b = _planned(_check("b.x", needs=("in:a",), provides=("in:b",)), "b.x")

    with pytest.raises(GraphError, match="cycle"):
        build_graph((a, b))


def test_a_blocked_check_keeps_its_reason() -> None:
    lint = PlannedCheck(
        check=_check("app.lint"), blocked="ruff is not available", task_id="app.lint"
    )

    graph = build_graph((lint,))

    assert graph.units == ()
    assert graph.blocked[0].reason == "ruff is not available"


def test_an_internal_check_runs_without_a_process() -> None:
    line = PlannedCheck(check=_check("app.line", tool=None), task_id="app.line")

    graph = build_graph((line,))

    assert len(graph.units) == 1
    assert graph.units[0].is_internal
    assert graph.units[0].source.task_id == "app.line"
    assert graph.blocked == ()


def test_an_internal_producer_orders_its_consumer() -> None:
    line = PlannedCheck(
        check=_check("app.line", tool=None, provides=("lines:count",)),
        task_id="app.line",
    )
    report = _planned(
        _check("app.report", needs=("lines:count",)),
        "app.report",
        argv=("tool", "report"),
    )

    graph = build_graph((report, line))
    layers = graph.layers()

    assert [unit.id for unit in layers[0]] == ["app.line"]
    assert [unit.id for unit in layers[1]] == ["app.report"]


def test_a_shared_task_waits_for_every_consumers_prerequisites() -> None:
    build = _planned(_check("app.build", provides=("build:x",)), "app.build")
    # a.lint needs the build; b.lint needs nothing — but both plan the same
    # ruff invocation, so the single shared node waits on app.build for both.
    a = _planned(_check("a.lint", needs=("build:x",)), "a.lint", argv=("ruff", "check", "."))
    b = _planned(_check("b.lint"), "b.lint", argv=("ruff", "check", "."))

    graph = build_graph((build, a, b))

    assert len(graph.units) == 2
    shared = graph.unit_of("b.lint")
    assert shared.depends_on == ("app.build",)
