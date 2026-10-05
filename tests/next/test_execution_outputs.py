"""Where a task may write, and the escape that looks like a safe path.

#205's fourth acceptance criterion is *"output path 이탈·symlink·부분 artifact를
거부한다"*. The three are listed together because they are the same mistake at
three depths, and the middle one is the reason the list is not just "check for
``..``": a name with no ``..`` in it, entirely inside the root, can still land
outside the root. So the symlink cases here are built for real — an actual link
placed in an actual output tree — rather than asserted about a string.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ici.execution.outputs import STAGING_DIRECTORY, OutputRoot, PathRefused


@pytest.fixture
def root(tmp_path: Path) -> OutputRoot:
    (tmp_path / "run").mkdir()
    return OutputRoot(tmp_path / "run")


# --- what is allowed ------------------------------------------------------


def test_a_plain_name_lands_in_the_root(root: OutputRoot) -> None:
    assert root.resolve("report.json") == root.path / "report.json"


def test_a_nested_name_lands_under_the_root(root: OutputRoot) -> None:
    assert root.resolve("reports/lint/out.json") == root.path / "reports/lint/out.json"


def test_preparing_a_nested_name_makes_its_directory(root: OutputRoot) -> None:
    target = root.prepare("reports/lint/out.json")

    assert target.parent.is_dir()
    assert not target.exists(), "preparing a path must not create the file"


def test_a_root_reached_through_a_link_is_still_a_root(tmp_path: Path) -> None:
    # /tmp is a symlink on macOS. Comparing against the unresolved path would
    # refuse every write on such a machine.
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    assert OutputRoot(linked).resolve("out.json") == linked / "out.json"


# --- leaving the root -----------------------------------------------------


def test_an_absolute_path_is_refused(root: OutputRoot) -> None:
    with pytest.raises(PathRefused, match="absolute"):
        root.resolve("/etc/passwd")


@pytest.mark.parametrize(
    "escape", ["../outside.json", "reports/../../outside.json", "a/b/../../../outside.json"]
)
def test_climbing_out_of_the_root_is_refused(root: OutputRoot, escape: str) -> None:
    with pytest.raises(PathRefused, match="climbs out"):
        root.resolve(escape)


def test_an_empty_name_is_refused(root: OutputRoot) -> None:
    with pytest.raises(PathRefused, match="needs a name"):
        root.resolve("")


# --- the escape that has no ".." in it ------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_a_link_in_the_output_tree_cannot_be_written_through(
    root: OutputRoot, tmp_path: Path
) -> None:
    # The case the criterion names. "reports/out.json" has no ".." in it and is
    # entirely inside the root; it writes to /elsewhere all the same.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root.path / "reports").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathRefused, match="symlink"):
        root.resolve("reports/out.json")


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_a_link_at_the_end_of_the_path_cannot_be_written_through(
    root: OutputRoot, tmp_path: Path
) -> None:
    target = tmp_path / "somebody-elses.json"
    target.write_text("theirs")
    (root.path / "out.json").symlink_to(target)

    with pytest.raises(PathRefused, match="symlink"):
        root.resolve("out.json")
    assert target.read_text() == "theirs"


@pytest.mark.skipif(os.name != "posix", reason="symlinks need privileges on Windows")
def test_a_link_that_stays_inside_the_root_is_refused_too(root: OutputRoot) -> None:
    # It resolves somewhere harmless today. What it points at tomorrow is not
    # this code's decision, and a link is how the tool between now and then
    # changes the answer.
    (root.path / "inner").mkdir()
    (root.path / "reports").symlink_to(root.path / "inner", target_is_directory=True)

    with pytest.raises(PathRefused, match="symlink"):
        root.resolve("reports/out.json")


# --- the root itself ------------------------------------------------------


def test_a_relative_root_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PathRefused, match="absolute"):
        OutputRoot(Path("run"))


def test_holds_says_whether_a_path_belongs_to_this_root(root: OutputRoot, tmp_path: Path) -> None:
    assert root.holds(root.path / "report.json")
    assert not root.holds(tmp_path / "elsewhere.json")


# --- private staging ------------------------------------------------------


def test_staging_is_inside_the_root_so_a_rename_stays_on_one_filesystem(
    root: OutputRoot,
) -> None:
    with root.staging() as staged:
        assert root.holds(staged)
        assert staged.parent.name == STAGING_DIRECTORY


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_staging_is_private_to_the_task_that_made_it(root: OutputRoot) -> None:
    with root.staging() as staged:
        assert staged.stat().st_mode & 0o077 == 0, "another user can read the staging area"


def test_two_tasks_staging_at_once_do_not_share_a_directory(root: OutputRoot) -> None:
    with root.staging() as first, root.staging() as second:
        assert first != second
        (first / "x").write_text("first")
        assert not (second / "x").exists()


def test_staged_work_is_removed_even_when_the_task_goes_wrong(root: OutputRoot) -> None:
    # A cancelled task leaves its half-written files here rather than under a
    # name a reader would trust.
    seen: list[Path] = []
    with pytest.raises(RuntimeError), root.staging() as staged:
        seen.append(staged)
        (staged / "half-written.json").write_text('{"incomp')
        raise RuntimeError("cancelled")

    assert not seen[0].exists()
