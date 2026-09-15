"""WP09 PR B: source inventory, roles, and before/after manifests (#207).

The behaviour under test is *counting honestly*: which files the composed
globs actually name, what role each plays, and that nothing was executed,
configured or built in order to find out.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from ici.domain.workspace import (
    AnalysisUnit,
    BuildUnit,
    Component,
    Workspace,
)
from ici.workspace.inventory import SourceRole, diff, take
from ici.workspace.vcs import status


def _write(root: Path, relative: str, text: str = "x") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _workspace(**overrides) -> Workspace:
    components = overrides.pop(
        "components",
        (
            Component(
                id="tool",
                root="tool",
                languages=("python",),
                sources=("tool/src/**/*.py",),
            ),
        ),
    )
    builds = overrides.pop("builds", ())
    units = overrides.pop(
        "units",
        tuple(
            AnalysisUnit(
                id=f"{component.id}.{language}",
                component_id=component.id,
                language=language,
            )
            for component in components
            for language in component.languages
        ),
    )
    return Workspace(
        id="ws",
        name="ws",
        components=components,
        builds=builds,
        analysis_units=units,
        **overrides,
    )


def test_inventory_hashes_each_file_once_and_lists_its_units(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/a.py", "a")
    _write(tmp_path, "tool/src/b.py", "b")
    workspace = _workspace()

    inventory = take(workspace, root=tmp_path)

    assert [item.path for item in inventory.files] == [
        "tool/src/a.py",
        "tool/src/b.py",
    ]
    expected = hashlib.sha256(b"a").hexdigest()
    assert inventory.files[0].digest == f"sha256:{expected}"
    assert inventory.files[0].units == ("tool.python",)
    assert inventory.digest.startswith("sha256:")


def test_two_components_sharing_a_file_read_it_once(tmp_path: Path) -> None:
    _write(tmp_path, "libs/shared/common.py", "shared")
    workspace = _workspace(
        components=(
            Component(
                id="a",
                root="libs/shared",
                languages=("python",),
                sources=("libs/shared/**/*.py",),
            ),
            Component(
                id="b",
                root="libs/shared",
                languages=("python",),
                sources=("libs/shared/**/*.py",),
            ),
        )
    )

    inventory = take(workspace, root=tmp_path)

    (entry,) = inventory.files
    assert entry.path == "libs/shared/common.py"
    assert entry.units == ("a.python", "b.python")


def test_generated_vendor_and_source_roles_are_distinct(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/app.py")
    _write(tmp_path, "tool/third_party/lib.py")
    _write(tmp_path, "build/ici/moc_main.cpp")
    workspace = _workspace(
        components=(
            Component(
                id="tool",
                root="tool",
                languages=("python",),
                sources=("tool/**/*.py",),
                vendor=("tool/third_party/**",),
            ),
            Component(
                id="gui",
                root=".",
                languages=("cpp",),
                sources=("build/**/*.cpp",),
            ),
        ),
        builds=(BuildUnit(id="native", system="qmake", variant="default", directory="build/ici"),),
    )

    inventory = take(workspace, root=tmp_path)
    roles = {item.path: item.role for item in inventory.files}

    assert roles["tool/src/app.py"] is SourceRole.SOURCE
    assert roles["tool/third_party/lib.py"] is SourceRole.VENDOR
    assert roles["build/ici/moc_main.cpp"] is SourceRole.GENERATED
    assert inventory.generated_roots == ("build/ici",)


def test_include_adds_and_exclude_removes(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/keep.py")
    _write(tmp_path, "tool/src/drop.py")
    _write(tmp_path, "tool/extra/added.py")
    workspace = _workspace(
        components=(
            Component(
                id="tool",
                root="tool",
                languages=("python",),
                sources=("tool/src/**/*.py",),
                include=("tool/extra/**/*.py",),
                exclude=("tool/src/drop.py",),
            ),
        )
    )

    inventory = take(workspace, root=tmp_path)

    assert [item.path for item in inventory.files] == [
        "tool/extra/added.py",
        "tool/src/keep.py",
    ]


def test_external_inputs_are_reported_not_merged(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/app.py")
    outside = tmp_path.parent / "outside_sysroot"
    _write(outside, "include/h.h")
    _write(tmp_path, "libs/public/api.h")
    workspace = _workspace(
        components=(
            Component(
                id="tool",
                root="tool",
                languages=("cpp",),
                sources=("tool/src/**/*.py",),
                external=(str(outside), "libs/public/api.h", "missing/dir"),
            ),
        )
    )

    inventory = take(workspace, root=tmp_path)

    externals = {item.path: item for item in inventory.externals}
    # An external directory outside the workspace stays external; only its
    # members inside the tree join the file inventory.
    assert externals[str(outside)].present
    assert externals["libs/public/api.h"].present
    assert not externals["missing/dir"].present
    assert "libs/public/api.h" in {item.path for item in inventory.files}


def test_inventory_never_executes_what_it_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AC: taking an inventory triggers no configure, build or source run."""

    _write(tmp_path, "tool/src/app.py", "import os; os.remove('/sentinel')")
    _write(tmp_path, "tool/qmake.pro", "SOURCES = app.cpp")
    workspace = _workspace()

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("inventory must not spawn processes")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    inventory = take(workspace, root=tmp_path)

    assert [item.path for item in inventory.files] == ["tool/src/app.py"]


def test_snapshot_carries_generated_and_external_inputs(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/app.py")
    _write(tmp_path, "build/ici/moc.cpp")
    _write(tmp_path, "libs/public/api.h")
    workspace = _workspace(
        components=(
            Component(
                id="tool",
                root="tool",
                languages=("python",),
                sources=("tool/src/**/*.py",),
            ),
            Component(
                id="gui",
                root=".",
                languages=("cpp",),
                sources=("build/**/*.cpp",),
                external=("libs/public/api.h",),
            ),
        ),
        builds=(BuildUnit(id="native", system="qmake", variant="default", directory="build/ici"),),
    )

    snapshot = take(workspace, root=tmp_path).snapshot(commit="abc123", dirty=True)

    assert snapshot.generated == ("build/ici/moc.cpp",)
    assert snapshot.external_inputs == ("libs/public/api.h",)
    assert snapshot.commit == "abc123"
    assert snapshot.dirty
    assert set(snapshot.files) == {"tool/src/app.py", "build/ici/moc.cpp", "libs/public/api.h"}


def test_diff_reports_changes_between_run_start_and_end(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/app.py", "one")
    _write(tmp_path, "tool/src/gone.py")
    workspace = _workspace()
    before = take(workspace, root=tmp_path)

    _write(tmp_path, "tool/src/app.py", "two")
    _write(tmp_path, "tool/src/new.py")
    (tmp_path / "tool/src/gone.py").unlink()
    after = take(workspace, root=tmp_path)

    delta = diff(before, after)

    assert delta.added == ("tool/src/new.py",)
    assert delta.removed == ("tool/src/gone.py",)
    assert delta.changed == ("tool/src/app.py",)
    assert not delta.stable
    assert before.digest != after.digest


def test_an_unchanged_tree_diffs_clean(tmp_path: Path) -> None:
    _write(tmp_path, "tool/src/app.py")
    workspace = _workspace()
    assert diff(take(workspace, root=tmp_path), take(workspace, root=tmp_path)).stable


class TestVcs:
    def test_no_repository_reports_no_commit(self, tmp_path: Path) -> None:
        assert status(tmp_path) is None

    def test_commit_dirty_and_untracked_are_separate(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=t@t",
                "-c",
                "user.name=t",
                "commit",
                "-qm",
                "init",
                "--allow-empty",
            ],
            cwd=tmp_path,
            check=True,
        )
        _write(tmp_path, "tracked.py")
        subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "add"],
            cwd=tmp_path,
            check=True,
        )
        _write(tmp_path, "tracked.py", "changed")
        _write(tmp_path, "loose.py")

        state = status(tmp_path)

        assert state is not None
        assert state.commit is not None
        assert state.dirty == ("tracked.py",)
        assert state.untracked == ("loose.py",)
        assert not state.clean

    def test_clean_tree(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _write(tmp_path, "a.py")
        subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
            cwd=tmp_path,
            check=True,
        )

        state = status(tmp_path)

        assert state is not None and state.clean
        assert state.commit is not None
