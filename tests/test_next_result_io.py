"""Writing and reading a saved result (#200 PR B, step 5).

The property under test is that a reader never sees a half-written result.
``report`` and ``publish`` are defined to work from a saved file, so a torn
write would let them report a smaller, cheerier verdict than the run actually
reached — which is the failure SPEC-04 section 5 is guarding against.
"""

from __future__ import annotations

import json
import os

import pytest

from ici.domain import (
    EventType,
    ExecutionSummary,
    GateOutcome,
    GateVerdict,
    Producer,
    RunEvent,
    RunIdentity,
    RunResult,
    ScopeKind,
    ScopeSelection,
    SourceSnapshot,
)
from ici.domain._codec import SchemaError
from ici.execution.results import (
    EVENTS_FILENAME,
    RESULT_FILENAME,
    append_events,
    read_events,
    read_run_result,
    run_directory,
    write_run_result,
)

DIGEST = "sha256:" + "ab" * 32


def make_result(run_id: str = "run-1") -> RunResult:
    return RunResult(
        run_id=run_id,
        producer=Producer(ici_version="0.11.0"),
        identity=RunIdentity(
            source=SourceSnapshot(digest=DIGEST),
            policy_digest=DIGEST,
            toolchain_digest=DIGEST,
        ),
        scope=ScopeSelection(kind=ScopeKind.FULL, full_required_satisfied=True),
        execution=ExecutionSummary(required_complete=True),
        gate=GateOutcome(selected=GateVerdict.PASS),
    )


class TestRunDirectory:
    def test_run_output_lives_under_the_workspace_not_the_install(self, tmp_path):
        """SPEC-02 section 1: the install directory may be read-only.

        WP01 verified a bundle runs from a genuine read-only mount, which only
        holds if run output goes somewhere else.
        """

        directory = run_directory(tmp_path, "run-1")
        assert directory == tmp_path / ".ici" / "runs" / "run-1"


class TestAtomicWrite:
    def test_result_round_trips_through_a_file(self, tmp_path):
        path = tmp_path / RESULT_FILENAME
        write_run_result(make_result(), path)
        assert read_run_result(path) == make_result()

    def test_parent_directories_are_created(self, tmp_path):
        path = run_directory(tmp_path, "run-1") / RESULT_FILENAME
        write_run_result(make_result(), path)
        assert path.is_file()

    def test_a_trailing_newline_makes_the_file_line_friendly(self, tmp_path):
        path = tmp_path / RESULT_FILENAME
        write_run_result(make_result(), path)
        assert path.read_text(encoding="utf-8").endswith("}\n")

    def test_no_partial_file_survives_a_successful_write(self, tmp_path):
        path = tmp_path / RESULT_FILENAME
        write_run_result(make_result(), path)
        leftovers = [item.name for item in tmp_path.iterdir() if item.name != RESULT_FILENAME]
        assert leftovers == []

    def test_a_failed_write_leaves_neither_a_partial_nor_a_damaged_result(
        self, tmp_path, monkeypatch
    ):
        """The point of writing through a temporary file.

        The previous result must still be readable after a write that dies
        halfway, and no ".partial" file may be left where a later glob could
        mistake it for a result.
        """

        path = tmp_path / RESULT_FILENAME
        write_run_result(make_result("run-original"), path)

        def explode(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", explode)
        with pytest.raises(OSError, match="disk full"):
            write_run_result(make_result("run-replacement"), path)

        assert read_run_result(path).run_id == "run-original"
        leftovers = [item.name for item in tmp_path.iterdir() if item.name != RESULT_FILENAME]
        assert leftovers == []

    def test_the_temporary_file_shares_the_destination_filesystem(self, tmp_path, monkeypatch):
        """os.replace is only atomic within one filesystem.

        Creating the temporary file in the system temp directory would silently
        downgrade the rename to a copy, so the directory passed to mkstemp is
        asserted rather than assumed.
        """

        seen: dict[str, object] = {}
        real_mkstemp = __import__("tempfile").mkstemp

        def record(*args: object, **kwargs: object):
            seen.update(kwargs)
            return real_mkstemp(*args, **kwargs)

        monkeypatch.setattr("ici.execution.results.tempfile.mkstemp", record)
        target = run_directory(tmp_path, "run-1") / RESULT_FILENAME
        write_run_result(make_result(), target)
        assert seen["dir"] == str(target.parent)


class TestReadDiagnostics:
    def test_a_missing_file_says_which_one(self, tmp_path):
        with pytest.raises(SchemaError, match="could not read result"):
            read_run_result(tmp_path / "absent.json")

    def test_malformed_json_names_the_file(self, tmp_path):
        path = tmp_path / RESULT_FILENAME
        path.write_text("{oh no", encoding="utf-8")
        with pytest.raises(SchemaError, match="not valid JSON"):
            read_run_result(path)

    def test_a_legacy_report_is_refused_rather_than_read_as_empty(self, tmp_path):
        """The failure SPEC-04 section 5 forbids, at the file boundary."""

        path = tmp_path / RESULT_FILENAME
        path.write_text(json.dumps({"version": 3, "suite_status": "PASS"}), encoding="utf-8")
        with pytest.raises(SchemaError, match=r"ici\.next\.run"):
            read_run_result(path)


class TestEventFile:
    def make_event(self, seq: int, **overrides: object) -> RunEvent:
        base: dict[str, object] = {
            "run_id": "run-1",
            "seq": seq,
            "event_type": EventType.RUN_STARTED,
            "timestamp": f"2026-09-13T00:00:{seq:02d}Z",
        }
        base.update(overrides)
        return RunEvent(**base)  # type: ignore[arg-type]

    def test_events_append_across_calls(self, tmp_path):
        """A consumer tailing the file sees a run in progress, not at the end."""

        path = tmp_path / EVENTS_FILENAME
        append_events((self.make_event(0),), path)
        append_events((self.make_event(1, event_type=EventType.RUN_COMPLETED),), path)
        events, skipped = read_events(path)
        assert [item.seq for item in events] == [0, 1]
        assert skipped == ()

    def test_nothing_but_events_goes_on_the_stream(self, tmp_path):
        """SPEC-04 section 6 keeps ordinary logs out, so every line parses."""

        path = tmp_path / EVENTS_FILENAME
        append_events((self.make_event(0), self.make_event(1)), path)
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["schema_id"] == "ici.next.event"

    def test_a_missing_stream_is_empty_with_a_note_not_an_error(self, tmp_path):
        events, skipped = read_events(tmp_path / EVENTS_FILENAME)
        assert events == ()
        assert len(skipped) == 1

    def test_a_crash_truncates_at_most_the_last_line(self, tmp_path):
        path = tmp_path / EVENTS_FILENAME
        append_events((self.make_event(0), self.make_event(1)), path)
        text = path.read_text(encoding="utf-8")
        path.write_text(text[:-8], encoding="utf-8")

        events, skipped = read_events(path)
        assert [item.seq for item in events] == [0]
        assert len(skipped) == 1
