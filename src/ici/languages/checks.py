"""What a check is, shared by every language pack.

A check is the unit a user selects and a gate judges. It is not a tool: a check
names the work it needs, and whether that work is ici's own algorithm or an
external program is the check's business, not the caller's.

``required`` is the field the gate reads. #206 asks that a missing required tool
come out as INCOMPLETE rather than as a pass, and the only way that can be
decided is if the check said in advance that it was required. A check that
decided its own importance after finding out whether its tool was there would
report whatever happened as what was wanted.

``needs`` is how the DAG (#208) finds a check's prerequisites without asking the
check to name other checks. A check declares the *inputs* it consumes —
``"build:release"``, ``"compile-db"`` — and the graph matches them against what
the run's tasks produce. Naming a producer directly would couple the check to
whichever provider happens to supply the input today.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ici.domain.enums import Profile

__all__ = ["ALL_PROFILES", "CheckDefinition"]

#: Most checks are cheap source reads and belong to every profile. The ones
#: that do not — sanitizer runs, integration — declare a smaller set, because
#: a profile's job is to keep a run honest about cost, not to turn ``deep``
#: into "everything on".
ALL_PROFILES = frozenset(Profile)


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
    #: The profiles that may run it. A check outside the run's profile is
    #: omitted from selection — never asked, which is a different fact from
    #: blocked.
    profiles: frozenset[Profile] = field(default=ALL_PROFILES)
    #: Logical inputs the check's tasks consume, matched against the
    #: ``provides`` of the run's other checks. Empty for a pure source read.
    needs: tuple[str, ...] = ()
    #: Logical inputs this check's tasks produce for others — a compilation
    #: database, a prepared build. The graph wires consumers to these.
    provides: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a check must have an id")
        object.__setattr__(self, "profiles", frozenset(self.profiles))
        object.__setattr__(self, "needs", tuple(self.needs))
        object.__setattr__(self, "provides", tuple(self.provides))
        if not self.profiles:
            raise ValueError(f"check {self.id} must belong to at least one profile")
        if set(self.needs) & set(self.provides):
            raise ValueError(f"check {self.id} cannot produce an input it consumes")

    @property
    def needs_a_tool(self) -> bool:
        return self.tool is not None
