"""WP18 — pytest and coverage evidence on the project's own interpreter.

The promises under test (#216):

- the run uses the *project's* interpreter — a declared ``executable`` or the
  component's ``.venv`` — and never silently ici's own
- per-node pytest verdicts become findings at the node that failed; zero
  collected tests is not a pass
- when ``python.coverage`` is selected the *same* run is wrapped in
  ``coverage run`` — one execution feeds both checks
- the coverage read verifies its evidence: absent or inconsistent JSON is a
  parse failure, never a zero-coverage verdict
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.cli.next_testing import python_interpreter

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'


def _python_workspace(root: Path, *, tests: str | None = "pass") -> None:
    """A component with its own .venv interpreter and a tiny suite.

    ``.venv/bin/python`` symlinks the running interpreter — the fixture is a
    stand-in for a real project venv, and the point under test is that this
    interpreter, not an ambient one, is what runs the suite.
    """

    (root / "app" / "tests").mkdir(parents=True)
    (root / "app" / "lib.py").write_text("def one() -> int:\n    return 1\n", encoding="utf-8")
    if tests == "pass":
        (root / "app" / "tests" / "test_lib.py").write_text(
            "from lib import one\n\n\ndef test_one():\n    assert one() == 1\n",
            encoding="utf-8",
        )
    elif tests == "fail":
        (root / "app" / "tests" / "test_lib.py").write_text(
            "def test_bad():\n    assert False\n", encoding="utf-8"
        )
    elif tests == "collection":
        (root / "app" / "tests" / "test_lib.py").write_text(
            "import missing_package_entirely\n", encoding="utf-8"
        )
    # An empty conftest at the component root puts it on sys.path — the suite
    # sees ``lib`` with no import surgery to lint.
    (root / "app" / "conftest.py").write_text("", encoding="utf-8")
    venv_bin = root / "app" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    (root / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        + "[components.python]\n"
        + f'executable = "{sys.executable}"\n'
        + 'test_paths = ["tests/**/*.py"]\n',
        encoding="utf-8",
    )


def test_the_component_venv_wins_over_ambient_interpreters(tmp_path) -> None:
    _python_workspace(tmp_path)
    chosen = python_interpreter(None, tmp_path / "app")
    assert chosen == str(tmp_path / "app" / ".venv" / "bin" / "python")


def test_a_declared_executable_wins_over_the_venv(tmp_path) -> None:
    _python_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        + '[components.python]\nexecutable = "/custom/python3"\n',
        encoding="utf-8",
    )
    from ici.config.discovery import load

    effective = load(tmp_path).component("app")
    assert effective is not None
    assert python_interpreter(effective, tmp_path / "app") == "/custom/python3"


def test_no_project_interpreter_blocks_the_test_checks(tmp_path, monkeypatch) -> None:
    """A suite that cannot run under its own runtime is blocked, not faked."""

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "lib.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan"])
    assert result.exit_code == 0, result.output
    assert "app.python.test: blocked — no project interpreter" in result.output
    assert "app.python.coverage: blocked" in result.output


def test_verify_runs_the_suite_and_reports_a_failure(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path, tests="fail")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])
    assert result.exit_code == 1, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    rules = {finding["rule_id"] for finding in document["findings"]}
    assert "pytest.failed" in rules


def test_a_green_suite_and_coverage_share_one_run(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])
    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    names = {measurement["name"] for measurement in document["metrics"]}
    assert "coverage.lines" in names
    # The coverage task never re-ran the suite: its argv is the json read,
    # and the data file is the one the wrapped test run wrote.
    assert (tmp_path / ".ici" / "cache" / "coverage" / "app.data").is_file()


def test_zero_collected_tests_is_not_a_pass(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path, tests=None)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])
    assert result.exit_code == 3, result.output
    assert "app.python.test" in result.output


def test_a_collection_error_is_a_finding_not_a_green_run(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path, tests="collection")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])
    assert result.exit_code in (1, 3), result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    rules = {finding["rule_id"] for finding in document["findings"]}
    assert rules & {"pytest.error", "pytest.collection-error"}


def test_coverage_opt_out_leaves_the_run_unwrapped(tmp_path, monkeypatch) -> None:
    _python_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        HEADER
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        + "[components.python]\n"
        + f'executable = "{sys.executable}"\n'
        + 'test_paths = ["tests/**/*.py"]\n'
        + '[checks."python.coverage"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".ici" / "cache" / "coverage" / "app.data").exists()


@pytest.mark.skipif(shutil.which("coverage") is None, reason="coverage missing")
def test_inconsistent_coverage_json_is_a_parse_failure(tmp_path) -> None:
    """A report that does not add up is refused, never read as coverage."""

    from ici.adapters.providers.coverage import CoverageProvider
    from ici.execution.process import Outcome, TaskOutcome, TaskSpec

    bad = tmp_path / "coverage.json"
    bad.write_text('{"totals": {"num_statements": 10}, "files": {}}')
    spec = TaskSpec(
        name="app.python.coverage",
        argv=("python", "-m", "coverage", "json", "-o", str(bad)),
        cwd=tmp_path,
    )
    outcome = TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=0)
    parsed = CoverageProvider().parse(outcome)
    assert parsed.failed_to_parse is not None
