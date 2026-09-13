"""Turning a declared component into a plan, keeping two absences apart.

The distinction this module exists for is SPEC-04's — *"미선택과 적용 불가를
혼동하지 않는다"*. A check the component turned off was never asked for; a check
whose tool is missing was asked for and could not be done. Both produce no
findings, and only one of them may pass.
"""

from __future__ import annotations

import pytest

from ici.adapters.providers.base import ProviderPlan
from ici.application.plan import NothingSelected
from ici.application.selection import select, selected_checks
from ici.config.documents import CheckSetting, ComponentBody
from ici.config.origin import Origin, Sourced
from ici.domain.enums import TaskKind
from ici.domain.tasks import TaskSpec
from ici.languages.python.checks import LINE_CHECK, LINT_CHECK, CheckDefinition

CHECKS = (LINE_CHECK, LINT_CHECK)
ORIGIN = Origin(file="ici.toml")


def _component(*settings: CheckSetting) -> ComponentBody:
    return ComponentBody(
        id=Sourced("app", ORIGIN),
        root=None,
        languages=None,
        build=None,
        sources=(),
        include=(),
        exclude=(),
        python=None,
        cpp=None,
        origin=ORIGIN,
        checks=settings,
    )


def _setting(check_id: str, *, enabled: bool | None = None, required: bool | None = None):
    return CheckSetting(
        id=check_id,
        enabled=Sourced(enabled, ORIGIN) if enabled is not None else None,
        required=Sourced(required, ORIGIN) if required is not None else None,
        origin=ORIGIN,
    )


def _work(check: CheckDefinition, executable: str) -> ProviderPlan:
    return ProviderPlan(
        task=TaskSpec(
            id=check.id,
            kind=TaskKind.ANALYZE,
            provider=check.tool or "ici",
            argv=(executable, "check"),
            cwd="/project",
        )
    )


def _everything(name: str) -> str | None:
    return f"/bundle/bin/{name}"


def _nothing(name: str) -> str | None:
    return None


# --- what gets selected ---------------------------------------------------


def test_a_component_that_says_nothing_runs_every_check() -> None:
    assert [c.id for c in selected_checks(_component(), CHECKS)] == ["python.line", "python.lint"]


def test_a_check_the_component_turned_off_is_not_selected() -> None:
    chosen = selected_checks(_component(_setting("python.lint", enabled=False)), CHECKS)

    assert [c.id for c in chosen] == ["python.line"]


def test_a_component_can_make_a_check_advisory() -> None:
    chosen = selected_checks(_component(_setting("python.lint", required=False)), CHECKS)

    lint = next(c for c in chosen if c.id == "python.lint")
    assert lint.required is False
    assert LINT_CHECK.required is True, "the shared definition was mutated"


# --- the two absences -----------------------------------------------------


def test_a_disabled_check_is_absent_from_the_plan_entirely() -> None:
    # Never asked for. Nothing to report, and nothing blocking the gate.
    plan = select(_component(_setting("python.lint", enabled=False)), _everything, _work, CHECKS)

    assert [item.check.id for item in plan.checks] == ["python.line"]
    assert plan.required_blocked == ()


def test_a_check_whose_tool_is_missing_stays_in_the_plan_as_blocked() -> None:
    # Asked for, and could not be done. The gate has to see this.
    plan = select(_component(), _nothing, _work, CHECKS)

    assert [item.check.id for item in plan.checks] == ["python.line", "python.lint"]
    assert [item.check.id for item in plan.required_blocked] == ["python.lint"]
    assert "ruff is not available" in plan.blocked[0].blocked


def test_the_two_absences_do_not_look_alike() -> None:
    disabled = select(_component(_setting("python.lint", enabled=False)), _nothing, _work, CHECKS)
    missing = select(_component(), _nothing, _work, CHECKS)

    assert disabled.required_blocked == ()
    assert missing.required_blocked != ()


# --- nothing selected -----------------------------------------------------


def test_selecting_no_check_is_refused_rather_than_reported_as_clean() -> None:
    component = _component(
        _setting("python.line", enabled=False), _setting("python.lint", enabled=False)
    )

    with pytest.raises(NothingSelected, match="cannot pass"):
        select(component, _everything, _work, CHECKS)


# --- the planner is told, not asking --------------------------------------


def test_planning_asks_for_a_tool_once_per_check_and_never_probes() -> None:
    # A planner that probed the machine would make `plan` -- the command whose
    # purpose is to say what *would* happen -- depend on what is installed
    # while it says it.
    asked: list[str] = []

    def locate(name: str) -> str | None:
        asked.append(name)
        return "/bundle/bin/ruff"

    select(_component(), locate, _work, CHECKS)

    assert asked == ["ruff"], "a check with no tool went looking for one"


def test_an_internal_check_is_planned_without_a_tool() -> None:
    plan = select(_component(), _everything, _work, CHECKS)

    line = next(item for item in plan.checks if item.check.id == "python.line")
    assert line.is_internal
    assert line.task is None
