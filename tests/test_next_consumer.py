"""The idk-independent consumer against a real run — #224.

``idkconsumer`` reads only the two published artifacts — the result document
and the event stream — with no ici imports. These tests run ``ici next
verify`` end to end, then ask the consumer what it saw. The question each
test answers is whether the artifacts alone carry what a UI needs: the
verdict, the scope, the tasks that could not finish, and where each finding
lives — with the result, never the stream, as the final word.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.config.scaffold import propose, write
from idkconsumer import ContractError, consume_events, consume_result

runner = CliRunner()
RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")
    write(propose(root), root / "ici.toml")
    with (root / "ici.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            '[checks."python.test"]\nenabled = false\n[checks."python.coverage"]\nenabled = false\n'
        )
    monkeypatch.chdir(root)
    return root


def _verify(project: Path, tmp_path: Path, *extra: str) -> tuple[Path, Path]:
    result = tmp_path / "result.json"
    events = tmp_path / "events.jsonl"
    completed = runner.invoke(
        app,
        ["next", "verify", "--result", str(result), "--events", str(events), *extra],
    )
    assert completed.exit_code in (0, 1), completed.output
    return result, events


@needs_ruff
def test_the_consumer_reads_the_same_run_the_cli_ran(project: Path, tmp_path) -> None:
    """#224 acceptance: standalone verify and the consumer see one run with
    one meaning — same verdict, same scope, same findings."""
    result_path, events_path = _verify(project, tmp_path)

    document = json.loads(result_path.read_text(encoding="utf-8"))
    run = consume_result(result_path)

    assert run.selected_verdict == document["gate"]["selected"] == "FAIL"
    assert not run.cancelled
    assert run.findings
    finding = next(item for item in run.findings if item.rule_id == "ruff.F401")
    assert finding.location.resolve(project).is_file()
    assert finding.location.line == 1
    assert finding.component_id
    assert not finding.suppressed

    progress = consume_events(events_path)
    assert progress.run_completed
    assert progress.problems == []
    # Every task the stream watched start also finished; the result agrees
    # on the ones that could not — none here.
    assert progress.started_tasks <= set(progress.finished_tasks)
    assert run.incomplete_tasks == ()


@needs_ruff
def test_a_second_runs_consumer_sees_reuse_not_rework(project: Path, tmp_path) -> None:
    _verify(project, tmp_path)
    result_path = tmp_path / "second.json"
    events_path = tmp_path / "second-events.jsonl"
    runner.invoke(
        app, ["next", "verify", "--result", str(result_path), "--events", str(events_path)]
    )

    run = consume_result(result_path)
    progress = consume_events(events_path)

    document = json.loads(result_path.read_text(encoding="utf-8"))
    reused = set(document["execution"]["reused_task_ids"])
    assert reused
    # Reused tasks were reported complete without claiming a fresh start,
    # and the reused evidence still carries the finding — reuse is a lookup,
    # never a verdict (#209).
    assert not progress.started_tasks.intersection(reused)
    assert run.selected_verdict == "FAIL"
    assert any(item.rule_id == "ruff.F401" for item in run.findings)


@needs_ruff
def test_a_truncated_stream_changes_nothing_the_result_decided(project: Path, tmp_path) -> None:
    """A stream that ends mid-write is how a crash looks; the consumer reads
    what arrived, notes the loss, and still trusts the result."""
    result_path, events_path = _verify(project, tmp_path)
    text = events_path.read_text(encoding="utf-8")
    events_path.write_text(text[: text.rfind("\n")], encoding="utf-8")
    tail = events_path.read_text(encoding="utf-8")
    events_path.write_text(tail[:-7], encoding="utf-8")

    run = consume_result(result_path)
    progress = consume_events(events_path)

    assert not progress.run_completed
    assert progress.problems
    assert run.selected_verdict == "FAIL"


def test_a_document_the_consumer_does_not_know_is_refused(tmp_path) -> None:
    document = tmp_path / "result.json"
    document.write_text(json.dumps({"schema_id": "ici.result/v3", "results": []}), encoding="utf-8")

    with pytest.raises(ContractError):
        consume_result(document)


def test_a_newer_schema_version_is_refused_not_misread(tmp_path) -> None:
    """Version is negotiated: a producer that bumped it changed something
    breaking — reading on would misread the run rather than report it."""
    document = tmp_path / "result.json"
    document.write_text(
        json.dumps({"schema_id": "ici.next.run", "schema_version": 2}),
        encoding="utf-8",
    )

    with pytest.raises(ContractError):
        consume_result(document)


def test_a_finding_cannot_point_outside_the_workspace(project: Path, tmp_path) -> None:
    from idkconsumer import Location

    location = Location(path="../elsewhere.py", line=1)
    with pytest.raises(ContractError):
        location.resolve(project)


def test_a_cancelled_run_consumes_as_cancelled_not_as_passed(
    project: Path, monkeypatch, tmp_path
) -> None:
    """#224: cancellation reaches the consumer as cancellation — a partial
    result, an incomplete execution, and a stream that says the run stopped."""

    @contextlib.contextmanager
    def cancelled(cancellation):
        cancellation.cancel("received SIGINT")
        yield cancellation

    monkeypatch.setattr("ici.cli.next_path.signal_cancels", cancelled)
    result_path = tmp_path / "result.json"
    events_path = tmp_path / "events.jsonl"

    completed = runner.invoke(
        app, ["next", "verify", "--result", str(result_path), "--events", str(events_path)]
    )

    assert completed.exit_code == 130
    run = consume_result(result_path)
    assert run.cancelled
    assert run.selected_verdict == "INCOMPLETE"
    assert not run.required_complete

    progress = consume_events(events_path)
    assert progress.run_completed  # the run ended; incompletely, but ended
