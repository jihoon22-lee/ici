"""The JSONL event codec.

Nothing but events goes on this stream. SPEC-04 section 6 keeps ordinary logs
out of it so that a consumer can parse every line rather than guessing which
ones are for it.
"""

from __future__ import annotations

import json
from typing import Any

from ici.domain._codec import SchemaError, check_envelope, dumps, require_mapping
from ici.domain.events import (
    EVENT_SCHEMA_ID,
    EVENT_SCHEMA_VERSION,
    EventType,
    RunEvent,
)

__all__ = ["event_from_dict", "event_to_dict", "events_from_jsonl", "events_to_jsonl"]


def event_to_dict(event: RunEvent) -> dict[str, Any]:
    """Render one event as the ``ici.next.event`` v1 object."""

    payload: dict[str, Any] = {
        "schema_id": event.schema_id,
        "schema_version": event.schema_version,
        "run_id": event.run_id,
        "seq": event.seq,
        "event_type": event.event_type.value,
        "timestamp": event.timestamp,
    }
    for name in ("task_id", "component_id"):
        value = getattr(event, name)
        if value is not None:
            payload[name] = value
    if event.message:
        payload["message"] = event.message
    return payload


def event_from_dict(payload: object) -> RunEvent | None:
    """Read one event, or return ``None`` for a kind this build does not know.

    SPEC-04 section 6 asks a consumer to tolerate unknown optional events while
    still refusing an unsupported major schema. Returning ``None`` for the first
    and raising for the second keeps those two answers distinguishable at the
    call site, where the caller can count what it skipped.
    """

    data = require_mapping(payload, "event")
    check_envelope(data, EVENT_SCHEMA_ID, EVENT_SCHEMA_VERSION)
    raw_type = data.get("event_type")
    try:
        event_type = EventType(raw_type)
    except ValueError:
        return None
    try:
        return RunEvent(
            run_id=data.get("run_id"),  # type: ignore[arg-type]
            seq=data.get("seq"),  # type: ignore[arg-type]
            event_type=event_type,
            timestamp=data.get("timestamp"),  # type: ignore[arg-type]
            task_id=data.get("task_id"),
            component_id=data.get("component_id"),
            message=data.get("message", ""),
        )
    except ValueError as err:
        raise SchemaError(f"event is not valid: {err}") from err


def events_to_jsonl(events: tuple[RunEvent, ...] | list[RunEvent]) -> str:
    """Render events as JSONL, one object per line.

    Nothing but events goes on this stream. SPEC-04 section 6 keeps ordinary
    logs out of it so that a consumer can parse every line rather than guessing
    which ones are for it.
    """

    return "".join(dumps(event_to_dict(event)) + "\n" for event in events)


def _seq_note(previous: int | None, current: int, line_number: int) -> str | None:
    """Describe a break in the sequence, or ``None`` when it continues.

    Split out from the reader so the parse loop stays shallow, and because
    "is this sequence intact" is a separate question from "did this line
    parse". Reported, never repaired: renumbering would hide the fact that a
    producer emitted a gap.
    """

    if previous is None or current == previous + 1:
        return None
    if current == previous:
        return f"line {line_number}: duplicate seq {current}"
    return f"line {line_number}: seq jumped from {previous} to {current}"


def events_from_jsonl(text: str) -> tuple[tuple[RunEvent, ...], tuple[str, ...]]:
    """Parse a JSONL stream, returning the events and what was skipped.

    A truncated final line is expected, not exceptional: a writer killed
    mid-flush leaves one. It is reported as a skip so the caller knows the
    stream is incomplete, rather than raising and discarding the events that
    did arrive.

    Monotonic ``seq`` is checked here because a reader is the only place that
    can see the whole sequence.
    """

    events: list[RunEvent] = []
    skipped: list[str] = []
    last_seq: int | None = None

    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            skipped.append(f"line {number}: not valid JSON (truncated stream?)")
            continue
        event = event_from_dict(payload)
        if event is None:
            skipped.append(f"line {number}: unknown event_type, ignored")
            continue
        note = _seq_note(last_seq, event.seq, number)
        if note is not None:
            skipped.append(note)
        last_seq = event.seq
        events.append(event)

    return tuple(events), tuple(skipped)
