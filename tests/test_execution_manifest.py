"""Publishing what a task produced, so that seeing it means it is all there.

The third refusal in #205's criterion is *"부분 artifact를 거부한다"*, and unlike
the other two it is not a path problem: the path is right, the file is there,
and a reader that checks whether it exists gets a yes. Presence read as
completeness is the same mistake as an exit code read as an answer, one layer
out — so a manifest carries a size and a digest, and publishing is atomic so
that a name never exists before its contents do.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from ici.execution.manifest import (
    MANIFEST_SCHEMA,
    MalformedManifest,
    Manifest,
    NotPromotable,
    describe,
    publish,
    read,
    verify,
)
from ici.execution.outputs import OutputRoot
from ici.execution.process import ExitContract, Outcome, TaskOutcome, TaskSpec

RUFF_LIKE = ExitContract(success=(0,), findings=(1,))


@pytest.fixture
def root(tmp_path: Path) -> OutputRoot:
    (tmp_path / "run").mkdir()
    return OutputRoot(tmp_path / "run")


def _outcome(outcome: Outcome = Outcome.FINISHED, exit_code: int = 0) -> TaskOutcome:
    return TaskOutcome(
        spec=TaskSpec(argv=("ruff", "check"), name="lint"),
        outcome=outcome,
        exit_code=exit_code,
    )


def _produce(root: OutputRoot, name: str, text: str) -> Path:
    target = root.prepare(name)
    target.write_text(text, encoding="utf-8")
    return target


# --- what is promoted, and what is not ------------------------------------


def test_a_clean_run_is_promoted(root: OutputRoot) -> None:
    _produce(root, "reports/lint.json", '{"violations": []}')

    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)

    assert manifest.interpretation == "succeeded"
    assert [a.path for a in manifest.artifacts] == ["reports/lint.json"]


def test_a_tool_that_found_violations_is_promoted(root: OutputRoot) -> None:
    # The distinction #205 item 6 exists for, now deciding what gets cached. A
    # linter that exits 1 because it found violations answered the question it
    # was asked, and refusing to cache that throws away a real result.
    _produce(root, "reports/lint.json", '{"violations": [1, 2]}')

    manifest = describe(root, _outcome(exit_code=1), ("reports/lint.json",), RUFF_LIKE)

    assert manifest.interpretation == "found-findings"
    assert manifest.exit_code == 1


def test_the_same_exit_one_is_not_promoted_under_a_contract_that_calls_it_a_failure(
    root: OutputRoot,
) -> None:
    _produce(root, "reports/lint.json", "{}")

    with pytest.raises(NotPromotable):
        describe(root, _outcome(exit_code=1), ("reports/lint.json",), ExitContract())


@pytest.mark.parametrize(
    "outcome",
    [
        Outcome.TIMED_OUT,
        Outcome.SIGNALLED,
        Outcome.OUTPUT_TRUNCATED,
        Outcome.START_FAILED,
        Outcome.CANCELLED,
    ],
)
def test_a_run_that_did_not_finish_is_never_promoted(root: OutputRoot, outcome: Outcome) -> None:
    # Covered over the whole enum rather than a list beside it, so a reason
    # added later cannot arrive already cacheable.
    _produce(root, "reports/lint.json", '{"half":')

    with pytest.raises(NotPromotable, match=outcome.value):
        describe(root, _outcome(outcome), ("reports/lint.json",), RUFF_LIKE)


def test_a_failed_run_is_not_promoted(root: OutputRoot) -> None:
    _produce(root, "reports/lint.json", "{}")

    with pytest.raises(NotPromotable):
        describe(root, _outcome(exit_code=2), ("reports/lint.json",), RUFF_LIKE)


def test_an_artifact_that_was_never_written_is_not_promoted(root: OutputRoot) -> None:
    with pytest.raises(NotPromotable, match="is not there"):
        describe(root, _outcome(), ("reports/never-written.json",), RUFF_LIKE)


def test_an_artifact_outside_the_root_is_refused(root: OutputRoot) -> None:
    with pytest.raises(ValueError, match="climbs out"):
        describe(root, _outcome(), ("../escaped.json",), RUFF_LIKE)


# --- partial artifacts ----------------------------------------------------


def test_a_manifest_verifies_against_what_is_actually_on_disk(root: OutputRoot) -> None:
    _produce(root, "reports/lint.json", "a" * 4096)
    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)

    assert verify(manifest, root).is_complete


def test_a_truncated_artifact_is_rejected_though_it_is_still_there(root: OutputRoot) -> None:
    # The case the criterion names. The path is right and the file exists, so
    # anything that checks presence says yes.
    target = _produce(root, "reports/lint.json", "a" * 4096)
    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)

    with target.open("r+") as handle:
        handle.truncate(1024)

    assert target.is_file(), "the file is still present, which is the point"
    result = verify(manifest, root)
    assert not result.is_complete
    assert result.altered == ("reports/lint.json",)


def test_an_artifact_rewritten_to_the_same_length_is_rejected(root: OutputRoot) -> None:
    # Same size, different bytes: a size check alone would pass this.
    target = _produce(root, "reports/lint.json", "a" * 4096)
    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)
    target.write_text("b" * 4096, encoding="utf-8")

    assert verify(manifest, root).altered == ("reports/lint.json",)


def test_a_deleted_artifact_is_reported_as_missing(root: OutputRoot) -> None:
    _produce(root, "reports/lint.json", "{}")
    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)
    root.resolve("reports/lint.json").unlink()

    result = verify(manifest, root)
    assert result.missing == ("reports/lint.json",)
    assert "missing" in str(result)


# --- publishing is atomic -------------------------------------------------


def test_a_published_manifest_reads_back(root: OutputRoot) -> None:
    _produce(root, "reports/lint.json", "{}")
    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)

    destination = publish(manifest, root.resolve("manifest.json"))

    assert read(destination) == manifest
    assert json.loads(destination.read_text())["schema"] == MANIFEST_SCHEMA


def test_publishing_leaves_no_staged_file_behind(root: OutputRoot) -> None:
    _produce(root, "reports/lint.json", "{}")
    manifest = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)
    destination = root.resolve("manifest.json")

    publish(manifest, destination)

    assert [p.name for p in destination.parent.iterdir() if p.name.startswith(".manifest-")] == []


def test_a_reader_never_sees_half_a_manifest(root: OutputRoot) -> None:
    # Written in place, a reader catching the moment between open and write
    # gets an empty or truncated file and a JSON error. Published by rename, it
    # gets the old manifest or the new one.
    destination = root.resolve("manifest.json")
    _produce(root, "reports/lint.json", "{}")
    first = describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE)
    publish(first, destination)

    big = Manifest(
        task="lint",
        outcome="finished",
        exit_code=0,
        interpretation="succeeded",
        artifacts=first.artifacts * 500,
    )
    seen: list[int] = []
    stop = threading.Event()

    def keep_reading() -> None:
        while not stop.is_set():
            try:
                seen.append(len(read(destination).artifacts))
            except (OSError, ValueError) as error:  # pragma: no cover - the bug being excluded
                seen.append(-1)
                raise AssertionError(f"a reader saw a partial manifest: {error}") from error

    reader = threading.Thread(target=keep_reading)
    reader.start()
    try:
        for _ in range(40):
            publish(big, destination)
            publish(first, destination)
    finally:
        stop.set()
        reader.join(timeout=30)

    assert -1 not in seen, "a reader saw a partial manifest"
    assert set(seen) <= {1, 500}, f"a reader saw a manifest that was never published: {set(seen)}"
    assert 500 in seen, "the reader never caught the larger manifest; the race was not exercised"


def test_publishing_over_an_existing_manifest_replaces_it(root: OutputRoot) -> None:
    destination = root.resolve("manifest.json")
    _produce(root, "reports/lint.json", "{}")
    publish(describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE), destination)

    publish(describe(root, _outcome(exit_code=1), ("reports/lint.json",), RUFF_LIKE), destination)

    assert read(destination).interpretation == "found-findings"


# --- reading a manifest that is not one -----------------------------------


def test_a_manifest_from_another_schema_is_refused(root: OutputRoot) -> None:
    destination = root.prepare("manifest.json")
    destination.write_text(json.dumps({"schema": "something.else/v9"}), encoding="utf-8")

    with pytest.raises(MalformedManifest, match=MANIFEST_SCHEMA):
        read(destination)


@pytest.mark.parametrize(
    "damage",
    [
        {"task": 7},
        {"exit_code": "zero"},
        {"exit_code": True},
        {"artifacts": "reports/lint.json"},
        {"artifacts": ["reports/lint.json"]},
    ],
)
def test_a_damaged_manifest_says_what_is_wrong_instead_of_crashing(
    root: OutputRoot, damage: dict[str, object]
) -> None:
    # A manifest is a file, and a file can be truncated or hand-edited.
    # Reaching into it and letting a TypeError out would report a damaged
    # manifest as a crash in whoever opened it.
    body = {
        "schema": MANIFEST_SCHEMA,
        "task": "lint",
        "outcome": "finished",
        "exit_code": 0,
        "interpretation": "succeeded",
        "artifacts": [],
        **damage,
    }
    destination = root.prepare("manifest.json")
    destination.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(MalformedManifest):
        read(destination)


# --- the whole thing together ---------------------------------------------


def test_a_cancelled_task_publishes_nothing_and_leaves_nothing_named(root: OutputRoot) -> None:
    # Staged, not published: the half-written file exists only under a name
    # nobody reads, and goes when the staging area does.
    staged_paths: list[Path] = []
    with root.staging() as staged:
        staged_paths.append(staged)
        (staged / "lint.json").write_text('{"violations": [', encoding="utf-8")
        cancelled = _outcome(Outcome.CANCELLED, exit_code=-15)
        with pytest.raises(NotPromotable, match="cancelled"):
            describe(root, cancelled, (), RUFF_LIKE)

    assert not staged_paths[0].exists()
    assert not (root.path / "manifest.json").exists()
    assert sorted(p.name for p in root.path.iterdir()) == [".ici-staging"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_a_published_manifest_is_no_more_private_than_the_artifacts_it_describes(
    root: OutputRoot,
) -> None:
    # mkstemp creates a 0600 file and os.replace keeps the mode, so the first
    # version of publish handed back a manifest only the user who ran the task
    # could read -- private, inside the shared build root that exists so other
    # people can read it. Compared against an ordinary file rather than a fixed
    # number, since what "ordinary" means here is the umask's business.
    artifact = _produce(root, "reports/lint.json", "{}")
    destination = publish(
        describe(root, _outcome(), ("reports/lint.json",), RUFF_LIKE),
        root.resolve("manifest.json"),
    )

    published = destination.stat().st_mode & 0o777
    ordinary = artifact.stat().st_mode & 0o777
    assert published == ordinary, (
        f"the manifest is {published:#o} where a file the task wrote is {ordinary:#o}"
    )
