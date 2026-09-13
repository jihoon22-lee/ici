"""Turning a declared component into the plan for one run.

#206 item 1: read a single component's TOML, decide which checks it selects,
and build the plan the rest of the flow executes. This is the seam where the
declaration stops and the run begins, and three things about it are worth
stating because each one has an easy wrong version.

**A check a component disabled is not in the plan, and a check whose tool is
missing is.** The first was not asked for; the second was asked for and could
not be done, and those are different facts. SPEC-04 says so in as many words —
*"미선택과 적용 불가를 혼동하지 않는다"* — and the difference decides whether
the gate may pass.

**Selecting nothing is refused here**, not reported as a clean empty run.

**Whether a tool exists is asked once, by the caller**, and handed in. This
module is given availability; it does not go looking. A planner that probed the
machine would make ``plan`` — the command whose whole purpose is to say what
*would* happen — depend on what happens to be installed while it says it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from ici.adapters.providers.base import ProviderPlan
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.config.documents import ComponentBody
from ici.languages.python.checks import PYTHON_CHECKS, CheckDefinition

__all__ = ["Planner", "ToolLocator", "select", "selected_checks"]

#: Where a named tool is, or None when it is not available. Asked once, by the
#: caller, and handed in.
ToolLocator = Callable[[str], str | None]

#: How to build the work for a check whose tool was found.
Planner = Callable[[CheckDefinition, str], ProviderPlan]


def selected_checks(
    component: ComponentBody, available: Sequence[CheckDefinition] = PYTHON_CHECKS
) -> tuple[CheckDefinition, ...]:
    """Which checks this component runs, with its own settings applied.

    A check is on unless the component turned it off. ``required`` is likewise
    the check's default until the component says otherwise — and it matters
    before anything runs, because a check that decided its own importance after
    finding out whether its tool was there would report whatever happened as
    what was wanted.
    """

    settings = {setting.id: setting for setting in component.checks}
    chosen = []
    for check in available:
        setting = settings.get(check.id)
        if setting is not None and setting.enabled is not None and not setting.enabled.value:
            continue
        required = check.required
        if setting is not None and setting.required is not None:
            required = setting.required.value
        chosen.append(check if required == check.required else _with_required(check, required))
    return tuple(chosen)


def select(
    component: ComponentBody,
    locate: ToolLocator,
    plan_work: Planner,
    available: Sequence[CheckDefinition] = PYTHON_CHECKS,
) -> Plan:
    """Build the plan for one component, keeping what it cannot do in it."""

    checks = selected_checks(component, available)
    if not checks:
        raise NothingSelected(
            "this component selected no check; a run that checks nothing cannot pass"
        )

    planned = []
    for check in checks:
        if not check.needs_a_tool:
            planned.append(PlannedCheck(check=check))
            continue
        assert check.tool is not None
        executable = locate(check.tool)
        if executable is None:
            planned.append(PlannedCheck(check=check, blocked=f"{check.tool} is not available"))
            continue
        planned.append(PlannedCheck(check=check, task=plan_work(check, executable)))
    return Plan(checks=tuple(planned))


def _with_required(check: CheckDefinition, required: bool) -> CheckDefinition:
    return CheckDefinition(
        id=check.id,
        title=check.title,
        language=check.language,
        tool=check.tool,
        required=required,
    )
