"""The flat command surface — bare ``ici verify`` runs the next path.

The stable dispatch era is over: the top-level commands are the next path's
own handlers, ``ici next …`` remains as an alias, and a directory without a
``[workspace]`` config is a config error, not a silent fallback. Both halves
are asserted here, because a dispatch that fires on the wrong side of the
line is a migration bug twice over.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.config.discovery import find_workspace_root
from ici.config.scaffold import propose, write

runner = CliRunner()

RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")


@pytest.fixture
def next_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    write(propose(root), root / "ici.toml")
    with (root / "ici.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            '[checks."python.test"]\nenabled = false\n'
            '[checks."python.coverage"]\nenabled = false\n'
            '[checks."python.type"]\nenabled = false\n'
            '[checks."python.compat-runtime"]\nenabled = false\n'
        )
    monkeypatch.chdir(root)
    return root


# --- find_workspace_root ---------------------------------------------------


def test_find_workspace_root_finds_a_declaring_file(next_project: Path) -> None:
    assert find_workspace_root(next_project) == next_project


def test_find_workspace_root_stops_at_the_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    (checkout / "ici.toml").write_text('[engine]\nenabled = ["lint"]\n', encoding="utf-8")
    assert find_workspace_root(checkout) is None


def test_find_workspace_root_returns_none_without_any_file(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    assert find_workspace_root(checkout) is None


# --- flat dispatch ---------------------------------------------------------


@needs_ruff
def test_bare_verify_runs_the_next_path(next_project: Path) -> None:
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0, result.output
    stored = json.loads((next_project / ".ici/next/result.json").read_text(encoding="utf-8"))
    assert stored["schema_id"] == "ici.next.run"


@needs_ruff
def test_bare_verify_matches_the_next_spelling(next_project: Path) -> None:
    bare = runner.invoke(app, ["verify", "--result", "bare.json"])
    assert bare.exit_code == 0, bare.output
    stored = json.loads((next_project / "bare.json").read_text(encoding="utf-8"))
    assert stored["schema_id"] == "ici.next.run"


def test_unknown_flags_are_named_not_dropped(next_project: Path) -> None:
    result = runner.invoke(app, ["verify", "--verbose"])
    assert result.exit_code == 2
    assert "--verbose" in result.output


def test_next_alias_reaches_the_same_command(next_project: Path) -> None:
    result = runner.invoke(app, ["next", "verify", "--result", "aliased.json"])
    assert result.exit_code == 0, result.output
    assert (next_project / "aliased.json").is_file()


@needs_ruff
def test_bare_verify_from_a_subdirectory_anchors_at_the_workspace(
    next_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(next_project / "src")
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0, result.output
    # Run artifacts live under the workspace root, not the directory typed in.
    assert (next_project / ".ici/next/result.json").is_file()


@needs_ruff
def test_report_from_a_subdirectory_reads_the_workspace_result(
    next_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runner.invoke(app, ["verify"]).exit_code == 0
    monkeypatch.chdir(next_project / "src")
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0, result.output
    assert (next_project / ".ici/next/result.html").is_file()


def test_config_error_composes_nothing(next_project: Path) -> None:
    # Declares [workspace] but is semantically broken.
    (next_project / "ici.toml").write_text("schema_version = 1\n[workspace]\n", encoding="utf-8")
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 2
    assert not (next_project / ".ici/next/result.json").exists()


def test_directory_without_a_workspace_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "ici.toml").write_text(
        '[engine]\nenabled = ["lint"]\n[lint]\nprovider = "ruff"\n', encoding="utf-8"
    )
    monkeypatch.chdir(root)
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 2
    assert not (root / ".ici/next/result.json").exists()
