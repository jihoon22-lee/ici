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


def test_static_plan_fast_blocks_cargo_without_running_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/lib.rs").write_text("pub fn answer() {}\n")
    result = CliRunner().invoke(app, ["analyze", "--plan", "--profile", "fast", "--json"])
    assert result.exit_code == 0, result.output
    assert "rust.clippy" in result.stdout
    assert "blocked" in result.stdout
    assert not (tmp_path / ".ici").exists()


def test_configless_excludes_dependencies_and_build_outputs(tmp_path):
    from ici.workspace import build, inventory

    (tmp_path / "main.js").write_text("let answer = 1;")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules/dependency.js").write_text("let ignore = 2;")
    root, config = automatic(tmp_path)
    stock = inventory.take(build(config), root=root)
    assert all("node_modules" not in path for path in stock.snapshot(dirty=True).files)


def test_static_selection_is_declarative_for_new_runtime_checks():
    from ici.languages.checks import CheckDefinition

    check = CheckDefinition("custom.runtime", "Custom runtime", "python", None, static=False)
    assert not wanted(RunRequest(static_only=True), check)
    assert wanted(RunRequest(), check)


def test_plan_does_not_probe_vcs_or_execute_tasks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hello.py").write_text("answer = 42\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("plan must not execute processes")

    monkeypatch.setattr("ici.cli.next_path.vcs_status", forbidden)
    monkeypatch.setattr("ici.cli.next_path.run_task", forbidden)
    result = CliRunner().invoke(app, ["analyze", "--plan", "--json"])
    assert result.exit_code == 0, result.output


def test_nested_ruff_configuration_is_attributed_and_changes_scope(tmp_path):
    from ici.application.check_scope import _configuration_digests
    from ici.application.planning import tool_configs
    from ici.domain.enums import TaskKind
    from ici.domain.tasks import TaskSpec

    nested = tmp_path / "src"
    nested.mkdir()
    config = nested / "ruff.toml"
    config.write_text('exclude = ["ignored.py"]\n')
    configs = tool_configs(tmp_path, tmp_path, ("src/app.py",))
    assert "src/ruff.toml" in configs
    task = TaskSpec(
        id="lint",
        kind=TaskKind.ANALYZE,
        provider="ruff",
        argv=("ruff", "check"),
        cwd=str(tmp_path),
        input_refs=configs,
    )
    old = _configuration_digests(task)
    config.write_text("exclude = []\n")
    assert _configuration_digests(task) != old
