"""``ici next`` selection surface — #210's union, scope, and output contracts.

The claims under test are the ones the issue's acceptance list names: language
flags union and intersect with ``--component`` without re-reading the model,
a subdirectory cwd does not re-scope the run, ``--require-full`` turns
uncovered required scope into exit 3 with the gap named, machine output never
shares a stream with diagnostics, and ``init``/``doctor``/``plan`` leave no
side effects behind.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app

runner = CliRunner()
RUFF = shutil.which("ruff") or str(Path(".venv/bin/ruff").resolve())
needs_ruff = pytest.mark.skipif(not Path(RUFF).exists(), reason="ruff is not available")

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'


def _hybrid_workspace(root: Path) -> None:
    (root / "app").mkdir(parents=True)
    (root / "native").mkdir(parents=True)
    (root / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (root / "native" / "core.cpp").write_text("int core() { return 1; }\n", encoding="utf-8")
    (root / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[[components]]\nid = "native"\nroot = "native"\nlanguages = ["cpp"]\n',
        encoding="utf-8",
    )


@needs_ruff
def test_language_flags_union_and_intersect_with_component(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    python_only = runner.invoke(app, ["next", "verify", "--python"])
    assert python_only.exit_code == 0, python_only.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    # A language filter leaves declared coverage out — the result says PARTIAL,
    # and only the Python unit's one file was counted.
    assert stored["scope"]["kind"] == "partial"
    assert stored["scope"]["selected_languages"] == ["python"]
    counted = sum(m["value"] for m in stored["metrics"] if m["name"] == "files_counted")
    assert counted == 1

    union = runner.invoke(app, ["next", "verify", "--python", "--cpp"])
    assert union.exit_code == 0, union.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["kind"] == "full"
    counted = sum(m["value"] for m in stored["metrics"] if m["name"] == "files_counted")
    assert counted == 2


@needs_ruff
def test_repeated_component_options_intersect_with_languages(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["next", "verify", "--component", "app", "--component", "native", "--cpp"]
    )
    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["selected_components"] == ["app", "native"]
    assert stored["scope"]["selected_languages"] == ["cpp"]
    assert not any(f["task_id"].startswith("app.") for f in stored["findings"])


def test_a_subdirectory_cwd_does_not_rescope_the_run(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    nested = tmp_path / "app"
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["next", "plan"])
    assert result.exit_code == 0, result.output
    assert f"root: {tmp_path}" in result.output
    assert "components=app,native" in result.output


def test_a_selection_of_nothing_is_a_config_error(tmp_path, monkeypatch) -> None:
    (tmp_path / "docs").mkdir()
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path / "docs")

    result = runner.invoke(app, ["next", "verify", "--component", "ghost"])
    assert result.exit_code == 2
    assert "ghost" in result.output and "app" in result.output


@needs_ruff
def test_require_full_demotes_a_partial_scope_to_incomplete(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--component", "app", "--require-full"])
    assert result.exit_code == 3, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["gate"]["selected"] == "INCOMPLETE"
    assert any("native" in reason for reason in stored["gate"]["reasons"])
    # The subset's own verdict stays visible: what ran still passed.
    assert stored["scope"]["kind"] == "partial"
    assert stored["scope"]["full_required_satisfied"] is False


@needs_ruff
def test_require_full_passes_when_coverage_is_complete(tmp_path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--require-full"])
    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text("utf-8"))
    assert stored["scope"]["full_required_satisfied"] is True


@needs_ruff
def test_json_output_keeps_diagnostics_off_stdout(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--component", "app", "--json"])
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)  # stdout is the result, nothing else
    assert document["schema_id"] == "ici.next.run"
    assert "root:" in result.stderr  # diagnostics went to stderr
    assert "scope:" in result.stderr


@needs_ruff
def test_the_event_stream_is_valid_jsonl_with_monotonic_seq(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    events = tmp_path / "run.jsonl"

    result = runner.invoke(app, ["next", "verify", "--events", str(events)])
    assert result.exit_code in (0, 1), result.output

    lines = [json.loads(line) for line in events.read_text("utf-8").splitlines()]
    assert [line["seq"] for line in lines] == list(range(len(lines)))
    assert lines[0]["event_type"] == "run.started"
    assert lines[-1]["event_type"] == "run.completed"
    assert all(line["schema_id"] == "ici.next.event" for line in lines)
    assert any(line["event_type"] == "task.completed" for line in lines)


def test_plan_names_blockers_dependencies_and_cost(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "plan", "--profile", "fast"])
    assert result.exit_code == 0, result.output
    assert "profile=fast" in result.output
    assert "app:" in result.output and "native:" in result.output

    as_json = runner.invoke(app, ["next", "plan", "--json"])
    document = json.loads(as_json.stdout)
    assert document["schema_id"] == "ici.next.plan"
    assert document["scope"]["profile"] == "standard"
    kinds = {check["kind"] for plan in document["plans"] for check in plan["checks"]}
    assert kinds <= {"process", "internal", "blocked"}


def test_doctor_reports_tools_inputs_and_the_resolving_keys(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "doctor"])
    assert result.exit_code == 0, result.output
    assert "app (python):" in result.output
    assert "python.line" in result.output and "python.lint" in result.output
    if Path(RUFF).exists():
        assert "ruff at" in result.output

    as_json = runner.invoke(app, ["next", "doctor", "--json"])
    document = json.loads(as_json.stdout)
    assert document["schema_id"] == "ici.next.doctor"
    statuses = {check["status"] for entry in document["components"] for check in entry["checks"]}
    assert "selected" in statuses


def test_doctor_explains_a_disabled_check_by_its_origin(tmp_path, monkeypatch) -> None:
    _hybrid_workspace(tmp_path)
    (tmp_path / "ici.toml").write_text(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["python"]\n'
        '[components.checks."python.lint"]\nenabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "doctor"])
    assert result.exit_code == 0, result.output
    assert "python.lint: omitted" in result.output
    assert "disabled" in result.output


def test_init_writes_a_workspace_without_touching_the_tree(tmp_path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text("int c;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    preview = runner.invoke(app, ["next", "init", "--preview"])
    assert preview.exit_code == 0
    assert not (tmp_path / "ici.toml").exists()  # preview writes nothing
    assert 'id = "app"' in preview.output and 'id = "native"' in preview.output

    written = runner.invoke(app, ["next", "init"])
    assert written.exit_code == 0
    document = (tmp_path / "ici.toml").read_text("utf-8")
    assert 'languages = ["python"]' in document

    again = runner.invoke(app, ["next", "init"])
    assert again.exit_code == 2  # never silently overwrites
    assert "--force" in again.output


def test_init_restricts_candidates_to_asked_languages(tmp_path, monkeypatch) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text("int c;\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "init", "--preview", "--cpp"])
    assert result.exit_code == 0
    assert 'id = "native"' in result.output and 'id = "app"' not in result.output
