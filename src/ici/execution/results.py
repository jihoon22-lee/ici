"""Writing and reading a run result and its event stream.

Two rules from SPEC-04 section 5 drive the shape of this module.

**Atomic.** A result is written to a temporary file in the destination
directory and then renamed. A reader therefore sees either the previous result
or the complete new one, never a half-written file — which matters because
``report`` and ``publish`` are defined to work from a saved result, and a
cancelled run must not leave one that parses into a smaller, cheerier verdict
than the truth.

**Diagnostic, not silent.** Reading a file that is missing, unparseable or of
an unsupported version raises with the reason. SPEC-04 forbids a reader from
turning an unrecognised result into an empty PASS, and the easiest way to
violate that is a bare ``except`` around a parse.

Events are appended rather than rewritten. A stream interrupted mid-flush
leaves a partial final line, and ``ici.domain.eventstream`` treats that as an
expected skip rather than a parse failure, so the events that did arrive still
reach the consumer.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ici.domain._codec import SchemaError, dumps
from ici.domain.events import RunEvent
from ici.domain.eventstream import event_to_dict, events_from_jsonl
from ici.domain.result import RunResult
from ici.domain.serialization import run_result_from_dict, run_result_to_dict

__all__ = [
    "EVENTS_FILENAME",
    "RESULT_FILENAME",
    "append_events",
    "read_events",
    "read_run_result",
    "run_directory",
    "write_run_result",
]

RESULT_FILENAME = "result.json"
EVENTS_FILENAME = "events.jsonl"


def run_directory(workspace_root: Path, run_id: str) -> Path:
    """Return ``.ici/runs/<run_id>`` under the workspace.

    SPEC-02 section 1 separates run output from the install directory so that
    the latter can be read-only — WP01 verified a bundle runs from a genuine
    read-only mount, and that only holds if nothing writes back into it.
    """

    return workspace_root / ".ici" / "runs" / run_id


def write_run_result(result: RunResult, path: Path) -> Path:
    """Serialize a result and replace ``path`` atomically.

    The temporary file is created in the destination directory, not the system
    temp directory: ``os.replace`` is only atomic within a filesystem, and a
    ``/tmp`` on a different mount would silently downgrade this to a copy.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(run_result_to_dict(result))

    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".partial"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        # Includes KeyboardInterrupt: a cancelled write must not leave a
        # ".partial" file behind that a later glob could mistake for a result.
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_run_result(path: Path) -> RunResult:
    """Read a saved result, raising ``SchemaError`` with the reason on failure."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise SchemaError(f"could not read result {path}: {err}") from err
    try:
        payload = json.loads(text)
    except ValueError as err:
        raise SchemaError(f"{path} is not valid JSON: {err}") from err
    return run_result_from_dict(payload)


def append_events(events: tuple[RunEvent, ...] | list[RunEvent], path: Path) -> Path:
    """Append events to the JSONL stream, creating it if needed.

    Appending rather than rewriting is what makes the stream useful while a run
    is still going. Each line is flushed and fsynced so a consumer tailing the
    file sees complete lines, and a crash truncates at most the last one.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(dumps(event_to_dict(event)) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def read_events(path: Path) -> tuple[tuple[RunEvent, ...], tuple[str, ...]]:
    """Read the stream, returning the events and a note for everything skipped.

    A missing file is an empty stream with a note, not an error: asking for the
    events of a run that produced none is a reasonable thing for a consumer to
    do.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (), (f"{path} does not exist",)
    except OSError as err:
        raise SchemaError(f"could not read events {path}: {err}") from err
    return events_from_jsonl(text)
