"""``ici next`` end to end, with the exit codes #206 names.

The four rows in #206's acceptance list are exit codes, and each one is a
different kind of "did not pass". The one that is easy to get wrong is 2: a
configuration that cannot be read is not a failing verification, because
nothing ran, and reporting 1 there claims a verdict nobody reached.

Driven through the real CLI rather than the functions underneath it. The
functions were already covered in PR A; what is new here is the wiring, and the
wiring is what the first run of this found broken.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.config.scaffold import propose, write

runner = CliRunner()
RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    write(propose(root), root / "ici.toml")
    monkeypatch.chdir(root)
    return root


def _seed(root: Path) -> None:
    (root / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")


# --- the four exit codes --------------------------------------------------


@needs_ruff
def test_a_clean_project_exits_zero(project: Path) -> None:
    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


@needs_ruff
def test_a_seeded_defect_exits_one(project: Path) -> None:
    _seed(project)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output


def test_a_configuration_that_cannot_be_read_exits_two(project: Path) -> None:
    # Not 1. Nothing ran, so there is no verdict to report.
    (project / "ici.toml").write_text('schema_version = 1\n[workspace\nname = "x"\n', "utf-8")

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 2, result.output


def test_a_malformed_file_is_reported_as_malformed(project: Path) -> None:
    # The first version said "none declares a [workspace]" about a file whose
    # [workspace] was right there. The exit code was already right; the message
    # sent the reader to fix something that was not wrong.
    (project / "ici.toml").write_text('schema_version = 1\n[workspace\nname = "x"\n', "utf-8")

    result = runner.invoke(app, ["next", "verify"])

    assert "not valid TOML" in result.output
    assert "declares a [workspace]" not in result.output


def test_a_missing_required_tool_exits_three(project: Path, monkeypatch) -> None:
    _seed(project)
    # Patched at the one function that answers "where is this tool". The first
    # version patched Path.is_file, which also stopped config discovery finding
    # ici.toml, and the run failed for a reason the test was not about.
    monkeypatch.setattr("ici.cli.next_path._locate", lambda _: None)

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 3, result.output
    assert "INCOMPLETE" in result.output


# --- plan runs nothing ----------------------------------------------------


@needs_ruff
def test_plan_says_what_would_run_without_running_it(project: Path) -> None:
    before = {p for p in project.rglob("*")}

    result = runner.invoke(app, ["next", "plan"])

    assert result.exit_code == 0, result.output
    assert "python.lint" in result.output
    assert {p for p in project.rglob("*")} == before, "plan wrote something"


def test_plan_starts_no_process(project: Path, monkeypatch) -> None:
    # #206: init/plan은 build/source/install을 호출하지 않는다.
    def refuse(*args: object, **kwargs: object):
        raise AssertionError("plan started a process")

    monkeypatch.setattr("ici.execution.process.run_process", refuse)

    assert runner.invoke(app, ["next", "plan"]).exit_code == 0


# --- verify then report ---------------------------------------------------


@needs_ruff
def test_verify_saves_a_result_and_report_renders_it(project: Path) -> None:
    _seed(project)

    assert runner.invoke(app, ["next", "verify"]).exit_code == 1
    saved = project / ".ici" / "next" / "result.json"
    assert saved.is_file()

    assert runner.invoke(app, ["next", "report"]).exit_code == 0
    page = (project / ".ici" / "next" / "result.html").read_text(encoding="utf-8")

    stored = json.loads(saved.read_text(encoding="utf-8"))
    assert stored["gate"]["selected"] == "FAIL"
    assert "ruff.F401" in page


def test_report_without_a_result_says_so_rather_than_rendering_nothing(project: Path) -> None:
    result = runner.invoke(app, ["next", "report"])

    assert result.exit_code == 2
    assert "run `ici next verify` first" in result.output


@needs_ruff
def test_verifying_writes_only_under_dot_ici(project: Path) -> None:
    _seed(project)
    before = {p.relative_to(project) for p in project.rglob("*")}

    runner.invoke(app, ["next", "verify"])

    added = {p.relative_to(project) for p in project.rglob("*")} - before
    assert added and all(str(p).startswith(".ici") for p in added), sorted(str(p) for p in added)


# --- it replaces nothing --------------------------------------------------


def test_the_stable_commands_are_untouched() -> None:
    # #206: 완성 경로가 기존 stable 기능을 자동 교체하지 않는다. A sub-command
    # cannot be reached without typing it; a flag could be defaulted on.
    help_text = runner.invoke(app, ["--help"]).output

    assert "next" in help_text
    assert "verify" in help_text


def test_next_is_its_own_namespace_and_not_a_verify_flag() -> None:
    assert runner.invoke(app, ["verify", "--next"]).exit_code != 0
