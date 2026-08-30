"""Tests for doctor diagnostics and its required_tools config wiring."""

import io
import json
from pathlib import Path

from rich.console import Console

from ici.config import load_config
from ici.doctor import collect_diagnostics, render_doctor_brief, render_doctor_table


def test_required_tools_defaults_to_empty(tmp_path: Path):
    data = collect_diagnostics(tmp_path, config={"engines": {}})
    assert data["required_tools"] == []
    assert all(not tool["required"] for tool in data["tools"].values())


def test_required_tools_flags_configured_tool():
    data = collect_diagnostics(Path.cwd(), config={"doctor": {"required_tools": ["git"]}})
    assert data["required_tools"] == ["git"]
    assert data["tools"]["git"]["required"] is True
    other_tools = {name: t for name, t in data["tools"].items() if name != "git"}
    assert all(not t["required"] for t in other_tools.values())


def test_required_tools_loads_from_ici_toml(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        '[doctor]\nrequired_tools = ["g++", "cmake"]\n', encoding="utf-8"
    )
    config = load_config(tmp_path)
    data = collect_diagnostics(tmp_path, config=config)
    assert set(data["required_tools"]) == {"g++", "cmake"}
    assert data["tools"]["g++"]["required"] is True
    assert data["tools"]["cmake"]["required"] is True


def test_diagnostics_include_json_ready_support_matrix_without_running_engines(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {"cognitive": {"enabled": False}},
    }

    data = collect_diagnostics(tmp_path, config=config)
    matrix = data["support_matrix"]

    assert set(matrix) == {"project_languages", "project_frameworks", "entries"}
    assert matrix["project_languages"] == ["python"]
    assert json.loads(json.dumps(matrix)) == matrix

    applicable_enabled = [
        entry for entry in matrix["entries"] if entry["applicable"] and entry["enabled"]
    ]
    assert applicable_enabled
    assert all(entry["evidence"] == "NOT_RUN" for entry in applicable_enabled)
    assert all(entry["active_mode"] is None for entry in applicable_enabled)

    cognitive = next(
        entry
        for entry in matrix["entries"]
        if entry["engine_name"] == "cognitive" and entry["language"] == "python"
    )
    assert cognitive["enabled"] is False
    assert cognitive["evidence"] == "NOT_RUN"
    assert "disabled" in cognitive["reason"]


def test_render_doctor_table_displays_capability_matrix(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    data = collect_diagnostics(
        tmp_path,
        config={
            "project": {"source_dirs": ["src"]},
            "engines": {"cognitive": {"enabled": False}},
        },
    )
    output = io.StringIO()
    monkeypatch.setattr(
        "ici.doctor.console",
        Console(file=output, width=180, color_system=None),
    )

    render_doctor_table(data)

    rendered = output.getvalue()
    assert "Engine Capability Matrix" in rendered
    assert "Declared / Active" in rendered
    assert "Evidence / Confidence" in rendered
    assert "State" in rendered
    assert "line" in rendered
    assert "python" in rendered
    assert "cpp (qt)" in rendered
    assert "not-applicable" in rendered
    assert "disabled" in rendered
    assert "exact / -" in rendered
    assert "NOT_RUN / low" in rendered
    assert "heuristic" in rendered
    assert "applicable engine has not been" in rendered
    assert "run | Counts physical source" in rendered


def test_render_doctor_brief_keeps_support_summary_compact(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    data = collect_diagnostics(
        tmp_path,
        config={"project": {"source_dirs": ["src"]}, "engines": {}},
    )
    output = io.StringIO()
    monkeypatch.setattr("sys.stdout", output)

    render_doctor_brief(data)

    rendered = output.getvalue()
    assert "scope   languages=python  frameworks=none" in rendered
    assert "Engine Capability Matrix" not in rendered
    assert data["tools"]["gcc"]["required"] is False
