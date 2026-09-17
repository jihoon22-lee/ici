"""The default-path cutover — bare ``ici verify`` on a ``[workspace]`` root.

#227's transition is config-driven: a checkout whose ``ici.toml`` declares
``[workspace]`` runs the next engine path from the bare command, while a
checkout with only a legacy config keeps the stable engines. Both halves are
asserted here, because a dispatch that fires on the wrong side of the line is
a migration bug twice over.
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
            '[checks."python.test"]\nenabled = false\n[checks."python.coverage"]\nenabled = false\n'
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


# --- dispatch --------------------------------------------------------------


@needs_ruff
def test_bare_verify_runs_the_next_path(next_project: Path) -> None:
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0, result.output
    stored = json.loads((next_project / ".ici/next/result.json").read_text(encoding="utf-8"))
    assert stored["schema_id"] == "ici.next.run"
    # And it did not take the stable path on the way through.
    assert not (next_project / "verify_report.json").exists()


@needs_ruff
def test_bare_verify_report_maps_to_verify_report_json(next_project: Path) -> None:
    result = runner.invoke(app, ["verify", "--report"])
    assert result.exit_code == 0, result.output
    stored = json.loads((next_project / "verify_report.json").read_text(encoding="utf-8"))
    assert stored["schema_id"] == "ici.next.run"


@needs_ruff
def test_bare_verify_html_renders_the_stored_result(next_project: Path) -> None:
    result = runner.invoke(app, ["verify", "--html", "report.html"])
    assert result.exit_code == 0, result.output
    page = next_project / "report.html"
    assert page.is_file()
    assert "<script src" not in page.read_text(encoding="utf-8")


def test_stable_only_flags_are_named_not_dropped(next_project: Path) -> None:
    result = runner.invoke(app, ["verify", "--verbose"])
    assert result.exit_code == 2
    assert "--verbose" in result.output


def test_stable_only_flags_list_every_offender(next_project: Path) -> None:
    result = runner.invoke(
        app, ["verify", "--fail-on-new", "--github-summary", "--max-findings", "3"]
    )
    assert result.exit_code == 2
    for flag in ("--fail-on-new", "--github-summary", "--max-findings"):
        assert flag in result.output


def test_legacy_config_keeps_the_stable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "legacy"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "ici.toml").write_text(
        '[engine]\nenabled = ["lint"]\n[lint]\nprovider = "ruff"\n', encoding="utf-8"
    )
    monkeypatch.chdir(root)
    # The stable path may fail or not run — the point under test is that it
    # never dispatched: no next result exists afterwards.
    runner.invoke(app, ["verify"])
    assert not (root / ".ici/next/result.json").exists()
