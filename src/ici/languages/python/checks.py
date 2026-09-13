"""Which Python checks exist, and what each one is allowed to require.

A check is the unit a user selects and a gate judges. It is not a tool: a check
names the work it needs, and whether that work is ici's own algorithm or an
external program is the check's business, not the caller's.

``required`` is the field the gate reads. #206 asks that a missing required tool
come out as INCOMPLETE rather than as a pass, and the only way that can be
decided is if the check said in advance that it was required. A check that
decided its own importance after finding out whether its tool was there would
report whatever happened as what was wanted.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LINE_CHECK", "LINT_CHECK", "PYTHON_CHECKS", "CheckDefinition"]


@dataclass(frozen=True)
class CheckDefinition:
    """One selectable check."""

    id: str
    title: str
    language: str
    #: The tool this check needs, or None when ici performs the work itself.
    tool: str | None
    #: Whether the gate may pass without this check having completed.
    required: bool = True

    @property
    def needs_a_tool(self) -> bool:
        return self.tool is not None


LINE_CHECK = CheckDefinition(
    id="python.line",
    title="Line counts",
    language="python",
    tool=None,
)

LINT_CHECK = CheckDefinition(
    id="python.lint",
    title="Ruff lint",
    language="python",
    tool="ruff",
)

#: Declaration only. Importing this must not look at the machine.
PYTHON_CHECKS: tuple[CheckDefinition, ...] = (LINE_CHECK, LINT_CHECK)
