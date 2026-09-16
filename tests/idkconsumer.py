"""An idk-independent consumer of the ``ici.next`` contracts — #224 item 5.

This module is deliberately stdlib-only and imports nothing from ``ici``. A
consumer proves the contract stands alone precisely by not sharing the
producer's parser: if reading the result and the event stream required ici
internals, the contract would be ici's internals, and idk would be a second
name for the same code.

What it consumes:

- the result document — ``ici.next.run`` — as the *authoritative* record:
  selected gate, scope, task dispositions, findings with their logical
  locations;
- the event stream — ``ici.next.event`` — as a *progress* record: useful
  while the run is live, never a replacement for the result, and tolerant of
  truncation because a cancelled run's stream ends mid-sequence by design.

Nothing here mutates what it reads, and nothing it derives outranks the
result: if the stream and the result disagree, the stream is what was lost.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RESULT_SCHEMA_ID = "ici.next.run"
EVENT_SCHEMA_ID = "ici.next.event"
SCHEMA_VERSION = 1


def _check_schema(document: dict[str, Any], schema_id: str, where: str) -> None:
    """Refuse a document whose envelope is not the one this consumer knows.

    Version is a negotiation, not a hint: a producer that bumped
    ``schema_version`` changed something breaking by contract — reading it
    anyway is how an old consumer quietly misreads a new run.
    """
    if document.get("schema_id") != schema_id:
        raise ContractError(f"{where}: not an {schema_id} document")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(
            f"{where}: schema_version {document.get('schema_version')!r} "
            f"is not the {SCHEMA_VERSION} this consumer reads"
        )


class ContractError(Exception):
    """The file is not what its schema_id claims — refuse, never guess."""


@dataclass(frozen=True)
class Location:
    """A logical source position: a workspace-relative path and a line."""

    path: str
    line: int

    def resolve(self, workspace: Path) -> Path:
        """Map the logical path into the workspace — and refuse to leave it.

        A path that escapes the root is not a source file this run could have
        examined; opening one anyway would follow a pointer outside the
        mapping the run was scoped to.
        """
        resolved = (workspace / self.path).resolve()
        if not resolved.is_relative_to(workspace.resolve()):
            raise ContractError(f"finding path escapes the workspace: {self.path}")
        return resolved


@dataclass(frozen=True)
class ConsumedFinding:
    rule_id: str
    severity: str
    location: Location
    component_id: str | None
    variant: str | None
    suppressed: bool


@dataclass
class ConsumedRun:
    """What a UI needs from one verification, in the result's own terms."""

    run_id: str
    selected_verdict: str
    workspace_verdict: str
    cancelled: bool
    required_complete: bool
    selected_components: tuple[str, ...]
    omitted_components: tuple[str, ...]
    incomplete_tasks: tuple[str, ...]
    findings: tuple[ConsumedFinding, ...]
    limitations: tuple[str, ...]


@dataclass
class ObservedProgress:
    """The run as the event stream told it — advisory, never authoritative."""

    events: int = 0
    started_tasks: set[str] = field(default_factory=set)
    finished_tasks: dict[str, str] = field(default_factory=dict)
    run_completed: bool = False
    problems: list[str] = field(default_factory=list)


def consume_result(path: Path) -> ConsumedRun:
    document = json.loads(path.read_text(encoding="utf-8"))
    _check_schema(document, RESULT_SCHEMA_ID, str(path))

    scope = document["scope"]
    execution = document["execution"]
    gate = document["gate"]
    findings = []
    for item in document.get("findings", []):
        span = item["primary_location"]
        findings.append(
            ConsumedFinding(
                rule_id=item["rule_id"],
                severity=item["severity"],
                location=Location(path=span["path"], line=span["start_line"]),
                component_id=item.get("component_id"),
                variant=item.get("variant"),
                suppressed=bool(item.get("suppression", {}).get("suppressed")),
            )
        )

    return ConsumedRun(
        run_id=document["run_id"],
        selected_verdict=gate["selected"],
        workspace_verdict=gate["workspace"],
        cancelled=execution["cancelled"],
        required_complete=execution["required_complete"],
        selected_components=tuple(scope["selected_components"]),
        omitted_components=tuple(scope["omitted_components"]),
        incomplete_tasks=tuple(execution["blocked_task_ids"] + execution["failed_task_ids"]),
        findings=tuple(findings),
        limitations=tuple(document.get("limitations", [])),
    )


def consume_events(path: Path) -> ObservedProgress:
    """Follow the event stream the way a live UI would.

    Sequence gaps and duplicates are reported, not fatal; an event type this
    consumer does not know is skipped, because additive optional fields are
    the documented extension path; a truncated final line — what a crash
    leaves — costs that line and nothing before it.
    """
    progress = ObservedProgress()
    if not path.is_file():
        progress.problems.append("no event stream")
        return progress

    expected_seq = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines):
        try:
            event: dict[str, Any] = json.loads(line)
        except json.JSONDecodeError:
            if number == len(lines) - 1:
                progress.problems.append(f"truncated final event on line {number + 1}")
            else:
                progress.problems.append(f"unparseable event on line {number + 1}")
            continue
        if event.get("schema_id") != EVENT_SCHEMA_ID:
            progress.problems.append(f"foreign document on line {number + 1}")
            continue
        if event.get("schema_version") != SCHEMA_VERSION:
            progress.problems.append(
                f"event schema_version {event.get('schema_version')!r} "
                f"is not the {SCHEMA_VERSION} this consumer reads"
            )
            continue

        seq = event.get("seq")
        if seq is not None:
            if seq < expected_seq:
                progress.problems.append(f"duplicate or reordered seq {seq}")
            elif seq > expected_seq:
                progress.problems.append(f"seq jumped {expected_seq}..{seq - 1}")
            expected_seq = seq + 1

        progress.events += 1
        task = event.get("task_id")
        kind = event.get("event_type")
        if kind == "task.started" and task:
            progress.started_tasks.add(task)
        elif kind == "task.completed" and task:
            progress.finished_tasks[task] = event.get("message", "")
        elif kind == "run.completed":
            progress.run_completed = True
        # Unknown event types are additive extensions — counted, not read.

    return progress
