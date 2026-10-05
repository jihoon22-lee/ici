"""Running a task and publishing its output, as one thing that cannot half-happen.

#205 item 4 in one piece. The rule these tests are all about is that nothing
exists under the name a reader trusts until all of it is there — so most of
them check what is *absent* at a moment when a naive implementation would have
left something behind.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from ici.execution.cancellation import Cancellation
from ici.execution.locks import exclusive
from ici.execution.manifest import read, verify
from ici.execution.outputs import STAGING_DIRECTORY, OutputRoot
from ici.execution.process import ExitContract, Outcome, TaskSpec
from ici.execution.production import LOCK_NAME, produce

RUFF_LIKE = ExitContract(success=(0,), findings=(1,))


@pytest.fixture
def root(tmp_path: Path) -> OutputRoot:
    (tmp_path / "run").mkdir()
    return OutputRoot(tmp_path / "run")


def _task(body: str, name: str = "lint", **kwargs: object) -> TaskSpec:
    return TaskSpec(argv=(sys.executable, "-c", body), name=name, **kwargs)  # type: ignore[arg-type]


WRITES = "open('lint.json', 'w').write('{\"violations\": []}')"

# Writes an opening brace and then never finishes the file.
HALF_WRITES_THEN_HANGS = (
    "import time\n"
    "handle = open('lint.json', 'w')\n"
    "handle.write('{\"violations\": [')\n"
    "handle.flush()\n"
    "time.sleep(60)\n"
)


def _published_names(root: OutputRoot) -> list[str]:
    return sorted(p.name for p in root.path.iterdir() if p.name != STAGING_DIRECTORY)


# --- the good path --------------------------------------------------------


def test_a_clean_run_publishes_its_artifact_and_a_manifest(root: OutputRoot) -> None:
    result = produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE)

    assert result.promoted, result.refusal
    assert _published_names(root) == ["lint.json", "manifest.json"]
    assert result.manifest is not None
    assert verify(result.manifest, root).is_complete
    assert read(root.resolve("manifest.json")) == result.manifest


def test_the_tool_runs_in_the_staging_area_not_in_the_root(root: OutputRoot) -> None:
    # While it is running there is no file under a real name for anyone to find.
    result = produce(_task("import os; print(os.getcwd())"), root, (), RUFF_LIKE)

    assert result.outcome is not None
    where = Path(result.outcome.parseable.strip())
    assert where.parent.name == STAGING_DIRECTORY
    assert not where.exists(), "the staging area outlived the run"


def test_a_tool_that_found_violations_is_still_published(root: OutputRoot) -> None:
    result = produce(_task(f"{WRITES}; raise SystemExit(1)"), root, ("lint.json",), RUFF_LIKE)

    assert result.promoted
    assert result.manifest is not None
    assert result.manifest.interpretation == "found-findings"


# --- what is not published ------------------------------------------------


def test_a_failing_tool_publishes_nothing_at_all(root: OutputRoot) -> None:
    result = produce(_task(f"{WRITES}; raise SystemExit(2)"), root, ("lint.json",), RUFF_LIKE)

    assert not result.promoted
    assert "not a cacheable result" in result.refusal
    assert _published_names(root) == [], "a failed run left output behind"


def test_a_timed_out_tool_publishes_nothing(root: OutputRoot) -> None:
    result = produce(
        _task(f"{WRITES}\nimport time; time.sleep(60)", timeout=1.0),
        root,
        ("lint.json",),
        RUFF_LIKE,
    )

    assert not result.promoted
    assert result.outcome is not None and result.outcome.outcome is Outcome.TIMED_OUT
    assert _published_names(root) == []


def test_a_cancelled_tool_publishes_nothing_and_leaves_nothing_half_written(
    root: OutputRoot,
) -> None:
    # The file it had already written is in the staging area, under a name
    # nobody reads, and goes when the staging area does.
    cancellation = Cancellation()
    threading.Timer(0.5, cancellation.cancel, args=("stop",)).start()
    result = produce(
        _task(HALF_WRITES_THEN_HANGS, timeout=60.0),
        root,
        ("lint.json",),
        RUFF_LIKE,
        cancellation=cancellation,
    )

    assert not result.promoted
    assert result.outcome is not None and result.outcome.outcome is Outcome.CANCELLED
    assert _published_names(root) == []
    assert list((root.path / STAGING_DIRECTORY).glob("task-*")) == []


def test_a_tool_that_did_not_write_what_it_promised_publishes_nothing(root: OutputRoot) -> None:
    result = produce(_task("pass"), root, ("lint.json",), RUFF_LIKE)

    assert not result.promoted
    assert "did not write it" in result.refusal
    assert _published_names(root) == []


def test_a_partial_run_does_not_replace_the_last_good_output(root: OutputRoot) -> None:
    # The property that makes this worth doing: a reader between the two runs
    # keeps the answer from the run that finished.
    assert produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE).promoted
    good = root.resolve("lint.json").read_text()

    produce(
        _task("open('lint.json','w').write('ruined'); raise SystemExit(2)"),
        root,
        ("lint.json",),
        RUFF_LIKE,
    )

    assert root.resolve("lint.json").read_text() == good
    assert read(root.resolve("manifest.json")).interpretation == "succeeded"


# --- output that tries to leave the root ----------------------------------


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_an_output_written_as_a_symlink_is_not_published(root: OutputRoot, tmp_path: Path) -> None:
    # A link renamed into the output tree only has to be followed once.
    outside = tmp_path / "theirs.json"
    outside.write_text("theirs")
    result = produce(
        _task(f"import os; os.symlink({str(outside)!r}, 'lint.json')"),
        root,
        ("lint.json",),
        RUFF_LIKE,
    )

    assert not result.promoted
    assert "symlink" in result.refusal
    assert _published_names(root) == []
    assert outside.read_text() == "theirs"


@pytest.mark.parametrize("escape", ["../escaped.json", "/tmp/escaped.json"])
def test_an_output_named_outside_the_working_directory_is_refused(
    root: OutputRoot, escape: str
) -> None:
    result = produce(_task(WRITES), root, (escape,), RUFF_LIKE)

    assert not result.promoted
    assert "not a name inside" in result.refusal


# --- the lock -------------------------------------------------------------


def test_two_runs_do_not_write_the_same_root_at_once(root: OutputRoot) -> None:
    holder = root.path / STAGING_DIRECTORY / LOCK_NAME
    holder.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(holder):
        result = produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE, lock_timeout=0.3)

    assert not result.promoted
    assert result.outcome is None, "the task ran without holding the lock"
    assert "held by another process" in result.refusal


def test_a_run_that_could_not_take_the_lock_is_not_reported_as_a_tool_failure(
    root: OutputRoot,
) -> None:
    # Nothing ran, so there is no outcome to interpret. Reporting this as a
    # failing tool would send somebody to debug a tool that was never started.
    holder = root.path / STAGING_DIRECTORY / LOCK_NAME
    holder.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(holder):
        result = produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE, lock_timeout=0.2)

    assert result.outcome is None
    assert result.manifest is None
    assert str(result) == result.refusal


def test_the_lock_is_released_for_the_next_run(root: OutputRoot) -> None:
    assert produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE).promoted

    started = time.monotonic()
    assert produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE, lock_timeout=5.0).promoted
    assert time.monotonic() - started < 5.0


def test_the_root_holds_published_output_and_the_staging_area_only(root: OutputRoot) -> None:
    produce(_task(WRITES), root, ("lint.json",), RUFF_LIKE)

    assert sorted(p.name for p in root.path.iterdir()) == [
        STAGING_DIRECTORY,
        "lint.json",
        "manifest.json",
    ]
