"""Turning a declared component into the plan for one run.

#206 item 1: read a component's TOML, decide which checks it selects, and build
the plan the rest of the flow executes. This is the seam where the declaration
stops and the run begins, and three things about it are worth stating because
each one has an easy wrong version.

**A check a component disabled is not in the plan, and a check whose tool is
missing is.** The first was not asked for; the second was asked for and could
not be done, and those are different facts. SPEC-04 says so in as many words —
*"미선택과 적용 불가를 혼동하지 않는다"* — and the difference decides whether
the gate may pass. #208 adds the third member of that family: a check outside
the run's profile is *omitted* — never asked — and a check that needs a
mutating step under ``fast`` is *blocked*, because fast exists to promise no
hidden configure or build happens (SPEC-03 section 4).

**Selecting nothing is refused here**, not reported as a clean empty run.

**Whether a tool exists is asked once, by the caller**, and handed in. This
module is given availability; it does not go looking. A planner that probed the
machine would make ``plan`` — the command whose whole purpose is to say what
*would* happen — depend on what happens to be installed while it says it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ici.adapters.providers.base import ProviderPlan
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.config.composition import EffectiveComponent
from ici.config.documents import ComponentBody
from ici.domain.enums import Profile
from ici.languages.checks import CheckDefinition
from ici.languages.python.checks import PYTHON_CHECKS

__all__ = [
    "CheckSelection",
    "Omitted",
    "Planner",
    "ScopeProbe",
    "ToolLocator",
    "select",
    "select_effective",
    "selected_checks",
    "selected_effective_checks",
]

#: Where a named tool is, or None when it is not available. Asked once, by the
#: caller, and handed in.
ToolLocator = Callable[[str], str | None]

#: How to build the work for a check whose tool was found. The task id is
#: decided by the planner so a run task and a blocked placeholder carry the
#: same identity — the provider is told it, it does not choose it.
Planner = Callable[[CheckDefinition, str, str], ProviderPlan]

#: Whether the run's scope holds anything a check could read. A check whose
#: language inventories no files is *blocked*, not omitted — it was asked for
#: and could not be done, which is a different fact from never selected.
ScopeProbe = Callable[[CheckDefinition], bool]

DEFAULT_PROFILE = Profile.STANDARD


@dataclass(frozen=True)
class Omitted:
    """A check the run did not ask for, and why — distinct from blocked."""

    check: CheckDefinition
    reason: str


@dataclass(frozen=True)
class CheckSelection:
    """What a component asked for under a profile, and what it declined."""

    selected: tuple[CheckDefinition, ...]
    omitted: tuple[Omitted, ...]


def selected_checks(
    component: ComponentBody,
    available: Sequence[CheckDefinition] = PYTHON_CHECKS,
    profile: Profile = DEFAULT_PROFILE,
) -> CheckSelection:
    """Which checks this component runs, with its own settings applied.

    A check is on unless the component turned it off or the profile excludes
    it. ``required`` is likewise the check's default until the component says
    otherwise — and it matters before anything runs, because a check that
    decided its own importance after finding out whether its tool was there
    would report whatever happened as what was wanted.
    """

    settings = {setting.id: setting for setting in component.checks}
    chosen: list[CheckDefinition] = []
    omitted: list[Omitted] = []
    for check in available:
        if profile not in check.profiles:
            omitted.append(Omitted(check=check, reason=f"not in profile '{profile.value}'"))
            continue
        setting = settings.get(check.id)
        if setting is not None and setting.enabled is not None and not setting.enabled.value:
            omitted.append(Omitted(check=check, reason="disabled"))
            continue
        required = check.required
        if setting is not None and setting.required is not None:
            required = setting.required.value
        chosen.append(check if required == check.required else _with_required(check, required))
    return CheckSelection(selected=tuple(chosen), omitted=tuple(omitted))


def select(
    component: ComponentBody,
    locate: ToolLocator,
    plan_work: Planner,
    available: Sequence[CheckDefinition] = PYTHON_CHECKS,
    task_prefix: str = "",
    profile: Profile = DEFAULT_PROFILE,
    in_scope: ScopeProbe | None = None,
) -> Plan:
    """Build the plan for one component, keeping what it cannot do in it."""

    return _plan_for(
        selected_checks(component, available, profile).selected,
        locate,
        plan_work,
        "this component",
        task_prefix,
        profile,
        in_scope,
    )


def _plan_for(
    checks: Sequence[CheckDefinition],
    locate: ToolLocator,
    plan_work: Planner,
    who: str,
    task_prefix: str = "",
    profile: Profile = DEFAULT_PROFILE,
    in_scope: ScopeProbe | None = None,
) -> Plan:
    if not checks:
        raise NothingSelected(f"{who} selected no check; a run that checks nothing cannot pass")

    planned = []
    for check in checks:
        task_id = f"{task_prefix}.{check.id}" if task_prefix else check.id
        if task_prefix:
            check = _with_scope(check, task_prefix)
        if in_scope is not None and not in_scope(check):
            planned.append(
                PlannedCheck(
                    check=check,
                    blocked=f"no sources in scope for {check.language}",
                    task_id=task_id,
                )
            )
            continue
        if not check.needs_a_tool:
            planned.append(PlannedCheck(check=check, task_id=task_id))
            continue
        assert check.tool is not None
        executable = locate(check.tool)
        if executable is None:
            planned.append(
                PlannedCheck(check=check, blocked=f"{check.tool} is not available", task_id=task_id)
            )
            continue
        work = plan_work(check, executable, task_id)
        if profile is Profile.FAST and work.task.mutating:
            # SPEC-03 section 4: fast promises no hidden configure or build.
            # The task is shown as blocked, not dropped — dropped would read
            # as "never asked".
            planned.append(
                PlannedCheck(
                    check=check,
                    blocked="profile 'fast' does not run prepare tasks",
                    task_id=task_id,
                )
            )
            continue
        planned.append(PlannedCheck(check=check, task=work, task_id=task_id))
    return Plan(checks=tuple(planned))


def _with_scope(check: CheckDefinition, prefix: str) -> CheckDefinition:
    """Qualify a check's input names with the component it runs under.

    ``compile-inputs`` means *this component's* compile inputs — two
    components each producing it must be two capabilities, or the graph sees
    one input with two producers (#208's duplicate-producer error). Task ids
    already carry the prefix; the capability names follow them.
    """

    if not check.needs and not check.provides:
        return check
    return CheckDefinition(
        id=check.id,
        title=check.title,
        language=check.language,
        tool=check.tool,
        required=check.required,
        profiles=check.profiles,
        needs=tuple(f"{prefix}.{name}" for name in check.needs),
        provides=tuple(f"{prefix}.{name}" for name in check.provides),
    )


def _with_required(check: CheckDefinition, required: bool) -> CheckDefinition:
    return CheckDefinition(
        id=check.id,
        title=check.title,
        language=check.language,
        tool=check.tool,
        required=required,
        profiles=check.profiles,
        needs=check.needs,
        provides=check.provides,
    )


def selected_effective_checks(
    component: EffectiveComponent,
    available: Sequence[CheckDefinition] = PYTHON_CHECKS,
    profile: Profile = DEFAULT_PROFILE,
) -> CheckSelection:
    """The same decision, read off a composed component.

    The composed form is what a real run uses: every layer that had an opinion
    has already spoken, and the check carries where the winning value came
    from. Reading the raw document here instead would apply the component's own
    settings and silently drop the root's.
    """

    settings = {check.id: check for check in component.checks}
    chosen: list[CheckDefinition] = []
    omitted: list[Omitted] = []
    for check in available:
        if profile not in check.profiles:
            omitted.append(Omitted(check=check, reason=f"not in profile '{profile.value}'"))
            continue
        decided = settings.get(check.id)
        if decided is not None and not decided.enabled.value:
            omitted.append(Omitted(check=check, reason="disabled"))
            continue
        required = check.required if decided is None else decided.required.value
        chosen.append(check if required == check.required else _with_required(check, required))
    return CheckSelection(selected=tuple(chosen), omitted=tuple(omitted))


def select_effective(
    component: EffectiveComponent,
    locate: ToolLocator,
    plan_work: Planner,
    available: Sequence[CheckDefinition] = PYTHON_CHECKS,
    task_prefix: str = "",
    profile: Profile = DEFAULT_PROFILE,
    in_scope: ScopeProbe | None = None,
) -> Plan:
    """Build the plan for one composed component."""

    return _plan_for(
        selected_effective_checks(component, available, profile).selected,
        locate,
        plan_work,
        component.id,
        task_prefix,
        profile,
        in_scope,
    )
