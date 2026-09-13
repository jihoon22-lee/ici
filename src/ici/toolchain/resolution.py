"""What a resolution is, and what it has to be able to say.

#204 item 4 lists what a resolved tool records: version, capability, digest,
the origin of the selection, the candidates it beat, and the limits of its
support. That list is long because of what the current path does not record.

Two distinctions here are load-bearing.

**Launch path is not identity.** ``.venv/bin/python`` and the interpreter it
symlinks to are the same file and not the same tool: launching the realpath
skips the virtual environment, so the packages the project declared are gone
and the run silently uses whatever the system has. The launch path is what gets
executed; the identity path is for digests and for telling two interpreters
apart.

**Not found, too old, and broken are three answers.** The current path collapses
them into a falsy value, so "mypy is missing" and "mypy cannot start" reach the
user as the same sentence and lead to different fixes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Availability",
    "ProbeResult",
    "ResolvedTool",
    "Role",
    "SelectionOrigin",
    "Unresolved",
]


class Role(str, Enum):
    """What a tool is being chosen *for*.

    The role decides the rule (#204 item 2), and it is also what makes a lazy
    probe possible: a Python-only workspace never asks for a compiler, so no
    compiler is ever probed for it.
    """

    PROJECT_PYTHON = "project-python"
    ANALYZER = "analyzer"
    COMPILER = "compiler"
    BUILD_SYSTEM = "build-system"


class Availability(str, Enum):
    """Three ways a tool can fail to be usable, kept apart.

    ``UNAVAILABLE`` — nothing at that name or path.
    ``UNSUPPORTED`` — it ran, and it is not new enough or lacks a needed option.
    ``BROKEN`` — it exists and could not be asked; a timeout, a non-zero exit, or
    output that is not a version.

    Three fixes: install it, upgrade it, find out what is wrong with it.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    BROKEN = "broken"


@dataclass(frozen=True)
class ProbeResult:
    """What running ``<tool> --version`` produced.

    A value rather than a subprocess call, because the resolver takes its probe
    as an argument (#204's "probe executor를 주입"): the rules can then be tested
    against a matrix of fake executables with no process involved, and the
    resolver never imports the runner.
    """

    exit_code: int
    output: str = ""
    timed_out: bool = False
    truncated: bool = False

    @property
    def usable(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.truncated


@dataclass(frozen=True)
class SelectionOrigin:
    """Why this one, and which key changes it.

    ``config_key`` is not decoration. #204's acceptance criteria ask that doctor
    can show "the reason and the config key to fix it"; a reason with no key
    leaves the user to search the schema for the setting that would change the
    answer.
    """

    reason: str
    source: str
    config_key: str | None = None

    def __str__(self) -> str:
        if self.config_key is None:
            return f"{self.reason} (from {self.source})"
        return f"{self.reason} (from {self.source}; set {self.config_key} to change it)"


@dataclass(frozen=True)
class ResolvedTool:
    """One tool, chosen, with everything needed to explain the choice."""

    role: Role
    name: str
    launch_path: str
    identity_path: str
    origin: SelectionOrigin
    availability: Availability = Availability.AVAILABLE
    version: str | None = None
    capabilities: tuple[str, ...] = ()
    passed_over: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.launch_path:
            raise ValueError("a resolved tool must have a launch path")
        if not self.identity_path:
            raise ValueError("a resolved tool must have an identity path")

    @property
    def usable(self) -> bool:
        return self.availability is Availability.AVAILABLE

    @property
    def launches_through_a_link(self) -> bool:
        """Whether launching and identity differ — the venv case.

        True means the launch path must be used as-is. Substituting the identity
        path would run the same file outside its environment, which is the one
        substitution that looks harmless and changes the answer.
        """

        return self.launch_path != self.identity_path


@dataclass(frozen=True)
class Unresolved:
    """No tool for this role, and why — never a silent substitution.

    #204 item 3 forbids falling back to another interpreter or provider when an
    explicit choice fails, and the first acceptance criterion forbids testing a
    project with ici's own interpreter. Both need a value that means "no", so
    that the caller has to decide rather than receiving something that runs.
    """

    role: Role
    name: str
    availability: Availability
    origin: SelectionOrigin
    detail: str
    considered: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.availability is Availability.AVAILABLE:
            raise ValueError("an unresolved tool cannot be available")

    @property
    def usable(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"{self.role.value} {self.name}: {self.detail} — {self.origin}"
