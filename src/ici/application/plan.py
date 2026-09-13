"""Deciding what a run will do, before any of it happens.

This is the 1-layer plan #206 item 3 asks for: every task is independent, so
"the plan" is a list rather than a graph. The later DAG is an expansion of this,
not a replacement -- the tasks are already
:class:`~ici.domain.tasks.TaskSpec`, so growing dependencies means filling in a
field that is already there instead of changing what a task is.

A plan is also the answer to *"what would this run do?"* without doing it, which
is why a check whose tool is missing is **in** the plan, marked blocked, rather
than dropped from it. A plan that silently omits what it cannot do describes a
smaller run than the one that was asked for, and the difference is invisible.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.adapters.providers.base import ProviderPlan
from ici.languages.python.checks import CheckDefinition

__all__ = ["NothingSelected", "Plan", "PlannedCheck"]


class NothingSelected(ValueError):
    """No check was selected, which SPEC-04 calls a configuration error.

    Not an empty result. A run that checks nothing and reports PASS has told
    the user their code is fine on the strength of never having looked.
    """


@dataclass(frozen=True)
class PlannedCheck:
    """One selected check and what will happen to it."""

    check: CheckDefinition
    #: The process to run, or None when ici does the work itself.
    task: ProviderPlan | None = None
    #: Why nothing will run. Empty when the check is going to happen.
    blocked: str = ""

    def __post_init__(self) -> None:
        if self.task is not None and self.blocked:
            raise ValueError(f"{self.check.id} cannot be both planned and blocked")

    @property
    def will_run(self) -> bool:
        return not self.blocked

    @property
    def is_internal(self) -> bool:
        """Whether ici performs this itself rather than by running a tool."""

        return self.will_run and self.task is None


@dataclass(frozen=True)
class Plan:
    """Everything a run intends to do, including what it cannot."""

    checks: tuple[PlannedCheck, ...]

    def __post_init__(self) -> None:
        if not self.checks:
            raise NothingSelected("no check was selected")
        seen = set()
        for planned in self.checks:
            if planned.check.id in seen:
                raise ValueError(f"{planned.check.id} was selected twice")
            seen.add(planned.check.id)
            if planned.task is not None and planned.task.task.depends_on:
                # The field exists for the DAG that comes later; a plan that
                # quietly carried dependencies nothing orders would run them in
                # whatever order the list happened to be in.
                raise ValueError(
                    f"{planned.check.id} declares dependencies, which this plan cannot order"
                )

    @property
    def blocked(self) -> tuple[PlannedCheck, ...]:
        return tuple(item for item in self.checks if not item.will_run)

    @property
    def required_blocked(self) -> tuple[PlannedCheck, ...]:
        """Blocked checks the gate is not allowed to pass without."""

        return tuple(item for item in self.blocked if item.check.required)

    def __str__(self) -> str:
        lines = []
        for planned in self.checks:
            if planned.blocked:
                lines.append(f"  {planned.check.id}: blocked — {planned.blocked}")
            elif planned.is_internal:
                lines.append(f"  {planned.check.id}: {planned.check.title} (ici)")
            else:
                assert planned.task is not None
                lines.append(f"  {planned.check.id}: {' '.join(planned.task.task.argv)}")
        return "\n".join(lines)
