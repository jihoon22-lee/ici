"""Independent status axes for the ici-next result envelope.

SPEC-04 section 2 is built on one rule: these axes are independent. A tool
finishing is not the same as the code being clean; a check being unselected is
not the same as it being inapplicable; publishing failing does not change the
analysis verdict. Collapsing any pair of them is how "green" starts meaning
"nothing ran", so each axis gets its own enum and none of them is derived from
another.

``EvidenceLevel`` mirrors the four values of the existing
``ici.core.models.EvidenceState`` exactly, including the reason
``NOT_APPLICABLE`` exists apart from ``NOT_RUN``. That distinction is already
load-bearing in the current gate (``aggregate_suite_status`` uses it to keep a
project with no applicable language scope from being permanently red), so it is
carried over verbatim rather than redesigned. ``ici.domain.legacy`` maps the two
one-to-one.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CheckExecution",
    "EvidenceLevel",
    "GateVerdict",
    "PublicationState",
    "ScopeKind",
    "TaskKind",
    "TaskState",
]


class ScopeKind(str, Enum):
    """What the requested scope covered.

    ``PARTIAL`` exists so that a successful ``--python`` run can be reported as
    a pass over the selected scope without ever reading as a workspace pass
    (R05). ``STANDALONE`` marks a single component run from an explicit config
    file, which SPEC-01 section 2 forbids from standing in for its parent
    workspace.
    """

    FULL = "full"
    PARTIAL = "partial"
    STANDALONE = "standalone"


class GateVerdict(str, Enum):
    """The quality judgement, reported separately for selected and workspace.

    ``INCOMPLETE`` is the value the current implementation has no room for: a
    required check that did not finish is neither a pass nor a code violation,
    and exit code 3 exists for exactly this state.
    """

    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"
    NOT_EVALUATED = "NOT_EVALUATED"


class CheckExecution(str, Enum):
    """Whether a check ran, and if not, why not.

    ``NOT_SELECTED`` and ``NOT_APPLICABLE`` are different answers: the first
    means the run did not ask for it, the second means it could never apply to
    this scope. Reporting either as the other loses the question the user asked.
    """

    COMPLETED = "COMPLETED"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"
    NOT_SELECTED = "NOT_SELECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EvidenceLevel(str, Enum):
    """How a value was obtained. Mirrors ``core.models.EvidenceState``."""

    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TaskKind(str, Enum):
    """What a task does, from SPEC-02 section 4.

    ``PREPARE`` is the only kind allowed to mutate a build directory, which is
    what lets ``plan`` list mutating steps before anything runs and lets the
    fast profile refuse them. WP00 measured why the distinction needs to be
    explicit: eight engines declared read-only do spawn processes, because
    read-only means "does not mutate artifacts" and never meant "spawns
    nothing".
    """

    PROBE = "probe"
    PREPARE = "prepare"
    ANALYZE = "analyze"
    TEST = "test"
    COLLECT = "collect"


class TaskState(str, Enum):
    """Process lifecycle, from SPEC-02 section 5.

    ``FAILED`` describes the process, not the code: a linter that runs to
    completion and reports violations ``SUCCEEDED`` here. ``BLOCKED`` means a
    prerequisite never produced what this task needed.
    """

    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class PublicationState(str, Enum):
    """Result of publishing, which never alters the analysis verdict.

    A failed upload has to be retryable without re-running the analysis, so it
    lives on its own axis with its own exit code (SPEC-04 sections 3 and 7).
    """

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
