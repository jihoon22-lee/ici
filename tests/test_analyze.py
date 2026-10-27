"""Static entrypoint must not execute project tests or create configuration."""

import json
from pathlib import Path

from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.request import RunRequest, wanted
from ici.config.automatic import automatic
from ici.languages.registry import builtin


def test_static_selection_excludes_execution_across_profiles():
    checks = builtin().checks_for(("python", "cpp"))
    static = {check.id for check in checks if wanted(RunRequest(static_only=True), check)}
    assert {"python.lint", "python.type", "cpp.diagnostics"} <= static
    assert not static & {"python.test", "python.coverage", "cpp.test", "cpp.sanitize", "cpp.tsan"}
    assert all(wanted(RunRequest(), check) for check in checks)


def test_configless_uses_checkout_from_child(tmp_path):
    (tmp_path / ".git").mkdir()
    child = tmp_path / "src"
    child.mkdir()
    (child / "hello.py").write_text("answer = 42\n")
    root, config = automatic(child)
    assert root == tmp_path
    assert config.components
    assert not (tmp_path / "ici.toml").exists()


def test_existing_config_is_never_ignored(tmp_path):
    (tmp_path / "ici.toml").write_text("broken [")
    assert automatic(tmp_path) is None


def test_analyze_writes_contract_without_running_project_tests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / "hello.py").write_text("answer = 42\n")
    (tmp_path / "test_trap.py").write_text(
        "from pathlib import Path\nPath('TEST_EXECUTED').touch()\n"
    )
    result = CliRunner().invoke(app, ["analyze", "--json", "--events", "events.jsonl"])
    assert result.exit_code in (0, 1, 3), result.output
    stored = json.loads(result.stdout)
    assert stored["schema_id"] == "ici.next.run"
    assert not (tmp_path / "TEST_EXECUTED").exists()
    assert not (tmp_path / "ici.toml").exists()
    assert Path(".ici/next/result.json").is_file()
    assert Path("events.jsonl").is_file()
    assert "static selection" in result.stdout
