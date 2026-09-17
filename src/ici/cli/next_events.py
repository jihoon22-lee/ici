"""The ``ici.next.event`` sink for ``next verify`` — #224.

Separated from ``next_common`` not for length but for shape: everything else
there builds a plan; this one watches the run happen and writes it down.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from ici.domain._codec import dumps
from ici.domain.events import EventType, RunEvent
from ici.domain.eventstream import event_to_dict


class EventSink:
    """The ``ici.next.event`` stream for one run, on its own file handle.

    ``seq`` is monotonic per run and guarded because task completions arrive
    from pool threads. Events are appended and flushed as they happen —
    a consumer following the file sees progress live, and a cancelled run
    leaves a stream that ends mid-sequence rather than one that pretends to
    be whole. Nothing but events goes to the file — SPEC-04 section 6 keeps
    logs out of the stream so a consumer parses every line.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        self._path = path
        self._run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        self._file: TextIO | None = None

    def emit(
        self,
        event_type: EventType,
        *,
        task_id: str | None = None,
        component_id: str | None = None,
        message: str = "",
    ) -> None:
        with self._lock:
            if self._file is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._file = self._path.open("w", encoding="utf-8")
            event = RunEvent(
                run_id=self._run_id,
                seq=self._seq,
                event_type=event_type,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                task_id=task_id,
                component_id=component_id,
                message=message,
            )
            self._seq += 1
            self._file.write(dumps(event_to_dict(event)) + "\n")
            self._file.flush()

    def on_started(self, unit) -> None:
        """``task.started`` — emitted only when work actually begins.

        The scheduler calls this after cancellation, blocked-prerequisite,
        and cache-hit checks: a unit that never ran never claims a start.
        """
        self.emit(EventType.TASK_STARTED, task_id=unit.id)

    def on_execution(self, execution) -> None:
        """``task.completed`` — the unit's disposition is final."""
        detail = f" — {execution.detail}" if execution.detail else ""
        self.emit(
            EventType.TASK_COMPLETED,
            task_id=execution.unit,
            message=f"{execution.state.value}{detail}",
        )

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
