"""Capture frozen ici.next.event validation and reader notes (development-only oracle)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ici.domain.events import EventType, RunEvent
from ici.domain.eventstream import event_from_dict, events_from_jsonl

LINE = (
    '{"schema_id":"ici.next.event","schema_version":1,"run_id":"r",'
    '"seq":%d,"event_type":"diagnostic","timestamp":"t"}\n'
)


def accepted(**fields: object) -> bool:
    try:
        RunEvent("r", 1, EventType.TASK_STARTED, "t", **fields)  # type: ignore[arg-type]
    except ValueError:
        return False
    return True


def main() -> None:
    cases = {
        "task_id": ["", " a", "a ", "a\nb", "project.python.lint"],
        "component_id": ["", " a", "a\tb", "project"],
    }
    result: dict[str, object] = {
        "source": "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188",
        "ids": {
            name: {value: accepted(**{"task_id": "t", name: value}) for value in values}
            for name, values in cases.items()
        },
        "task_event_without_task": accepted(task_id=None),
        "notes": list(events_from_jsonl(LINE % 1 + LINE % 1 + LINE % 5 + LINE % 3 + "{")[1]),
    }
    try:
        event_from_dict(json.loads(LINE % 1)) and event_from_dict(
            {**json.loads(LINE % 1), "event_type": "task.started", "task_id": ""}
        )
        result["reader_refuses_empty_task"] = False
    except ValueError:
        result["reader_refuses_empty_task"] = True
    Path(sys.argv[1]).write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
