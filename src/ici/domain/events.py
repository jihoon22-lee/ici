"""The event stream: progress, reported separately from the result.

SPEC-04 section 6 makes one rule load-bearing: **the final result is
authoritative and a missing event never overrides it**. Events exist so a
consumer can show progress, not so it can reconstruct the verdict. That is why
``RunEvent`` carries no gate and no findings — a consumer that wanted to decide
pass or fail from the stream would have to go and read the result instead,
which is the intended outcome.

``seq`` is monotonic per run. A reader that sees a gap knows it lost something;
a reader that sees a repeat knows the producer is broken. Both are better than
silently renumbering.

Payloads are bounded here rather than at write time. An event carrying an
unbounded tool output would make the stream the place a large blob lands, and
SPEC-02 section 5 wants output limits applied where the data enters, not where
it is flushed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ici.domain._validation import (
    require_identifier,
    require_non_negative,
    require_text,
)

__all__ = ["EVENT_SCHEMA_ID", "EVENT_SCHEMA_VERSION", "EventType", "RunEvent"]

EVENT_SCHEMA_ID = "ici.next.event"
EVENT_SCHEMA_VERSION = 1

# A payload big enough to describe what happened and too small to be a place to
# dump a compiler log.
MAX_PAYLOAD_CHARS = 4096


class EventType(str, Enum):
    """The event kinds SPEC-04 section 6 defines.

    A consumer may ignore a kind it does not know — the enum is closed here so
    that a producer cannot invent one, while the reader deliberately tolerates
    unknown values (see ``ici.domain.serialization``). The asymmetry is the
    point: ici must not emit something undocumented, but a newer ici talking to
    an older consumer must not break it either.
    """

    RUN_STARTED = "run.started"
    PLAN_READY = "plan.ready"
    TASK_STARTED = "task.started"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETED = "task.completed"
    DIAGNOSTIC = "diagnostic"
    RUN_COMPLETED = "run.completed"


@dataclass(frozen=True)
class RunEvent:
    """One progress record. Never a verdict."""

    run_id: str
    seq: int
    event_type: EventType
    timestamp: str
    task_id: str | None = None
    component_id: str | None = None
    message: str = ""
    schema_id: str = EVENT_SCHEMA_ID
    schema_version: int = EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", require_text(self.run_id, "event run id"))
        require_non_negative(self.seq, "event seq")
        if not isinstance(self.event_type, EventType):
            raise ValueError("event type must be an EventType")
        object.__setattr__(self, "timestamp", require_text(self.timestamp, "event timestamp"))
        for name in ("task_id", "component_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_identifier(value, f"event {name}"))
        if not isinstance(self.message, str):
            raise ValueError("event message must be a string")
        if len(self.message) > MAX_PAYLOAD_CHARS:
            raise ValueError(
                f"event message exceeds {MAX_PAYLOAD_CHARS} characters; "
                "bound tool output where it is produced, not in the event stream"
            )
        if self.schema_id != EVENT_SCHEMA_ID:
            raise ValueError(f"event schema_id must be {EVENT_SCHEMA_ID!r}")
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ValueError(f"event schema_version must be {EVENT_SCHEMA_VERSION}")
        if self.event_type in _TASK_SCOPED and self.task_id is None:
            raise ValueError(f"{self.event_type.value} must name the task it is about")


# Kinds that make no sense without a task: a reader grouping by task would
# silently drop them, which looks like the task produced nothing.
_TASK_SCOPED = frozenset(
    {EventType.TASK_STARTED, EventType.TASK_PROGRESS, EventType.TASK_COMPLETED}
)
