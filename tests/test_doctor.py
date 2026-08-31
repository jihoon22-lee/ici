"""Tests for doctor diagnostics and its required_tools config wiring."""

import io
import json
from pathlib import Path

from rich.console import Console

from ici.config import load_config
from ici.core.capabilities import CapabilityInventory, ToolRequirement
from ici.core.toolchain import ToolCapability
from ici.doctor import collect_diagnostics, render_doctor_brief, render_doctor_table


def test_required_tools_defaults_to_empty(tmp_path: Path):
    data = collect_diagnostics(tmp_path, config={"engines": {}})
    assert data["required_tools"] == []
    assert all(not tool["required"] for tool in data["tools"].values())


def test_required_tools_flags_configured_tool():
    data = collect_diagnostics(Path.cwd(), config={"doctor": {"required_tools": ["git"]}})
    assert data["required_tools"] == ["git"]
    assert data["tools"]["git"]["required"] is True
    assert "doctor.config" in data["tools"]["git"]["required_by"]
    assert "git" in data["effective_required_tools"]
    assert set(data["effective_required_tools"]) == {
        name for name, tool in data["tools"].items() if tool["required"]
    }


def test_tool_policy_merges_engine_requirements_and_ignores_inactive_rows():
    import ici.doctor as doctor

    required_by, optional_by = doctor._tool_policy(
        {
            "entries": [
                {
                    "engine_name": "test",
                    "language": "python",
                    "applicable": True,
                    "enabled": True,
                    "required_tools": ["python3"],
                    "optional_tools": ["pytest"],
                },
                {
                    "engine_name": "lint",
                    "language": "cpp",
                    "applicable": False,
                    "enabled": True,
                    "required_tools": ["g++"],
                    "optional_tools": ["pkg-config"],
                },
                {
                    "engine_name": "type",
                    "language": "python",
                    "applicable": True,
                    "enabled": False,
                    "required_tools": ["mypy"],
                    "optional_tools": ["typing-extensions"],
                },
            ]
        },
        {"doctor-tool"},
    )

    assert required_by == {
        "doctor-tool": {"doctor.config"},
        "python3": {"test:python"},
    }
    assert optional_by == {"pytest": {"test:python"}}


def test_collect_diagnostics_shares_full_inventory_and_legacy_tool_rows(
    tmp_path: Path, monkeypatch
):
    import ici.doctor as doctor

    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")

    inventory = CapabilityInventory(
        capabilities={
            "sentinel": ToolCapability(
                name="sentinel",
                path="/fake/sentinel",
                available=True,
                version="sentinel 1.2.3",
                version_tuple=(1, 2, 3),
            )
        },
        requirements={
            "sentinel": ToolRequirement(
                name="sentinel", required_by=("doctor.config",), optional_by=()
            )
        },
    )
    serialized = {
        "schema_version": "ici.capabilities/v1",
        "status": "PASS",
        "healthy": True,
        "counts": {"total": 1, "ready": 1, "incomplete": 0, "unavailable": 0},
        "missing_required": [],
        "incomplete_required": [],
        "tools": [
            {
                "name": "sentinel",
                "available": True,
                "complete": True,
                "required": True,
                "optional": False,
                "required_by": ["doctor.config"],
                "optional_by": [],
            }
        ],
    }
    captured = {}

    def fake_collect(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return inventory

    def fake_serialize(value):
        captured["serialized_inventory"] = value
        return serialized

    monkeypatch.setattr(doctor, "collect_capability_inventory", fake_collect)
    monkeypatch.setattr(doctor, "serialize_capability_inventory", fake_serialize)
    monkeypatch.setattr(doctor, "get_system_info", lambda: {})
    monkeypatch.setattr(doctor, "find_python_candidates", lambda: [])
    monkeypatch.setattr(doctor, "get_nas_shared_dir", lambda: tmp_path / "nas")
    monkeypatch.setattr(doctor, "get_nas_cpp_lib_dir", lambda: tmp_path / "cpp")
    monkeypatch.setattr(doctor, "find_infra_root", lambda: tmp_path)

    data = doctor.collect_diagnostics(
        tmp_path,
        config={
            "project": {"source_dirs": ["src"]},
            "doctor": {"required_tools": ["sentinel"]},
        },
    )

    assert captured["cwd"] == tmp_path.resolve()
    assert captured["probes"] is doctor.DEFAULT_TOOL_PROBES
    assert captured["serialized_inventory"] is inventory
    assert captured["required_by"]["sentinel"] == {"doctor.config"}
    assert "test:python" in captured["required_by"]["python3"]
    assert "lint:python" in captured["optional_by"]["ruff"]
    assert captured["required_by"]["pytest"] == {"sanitize:python", "test:python"}
    assert "pytest" not in captured["optional_by"]
    assert data["capability_inventory"] is serialized
    assert data["tools"]["sentinel"] == serialized["tools"][0]


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


def test_doctor_reports_effective_analysis_profile(tmp_path: Path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    data = collect_diagnostics(
        tmp_path,
        config={
            "ici": {"profile": "deep"},
            "project": {"source_dirs": ["src"]},
            "engines": {},
        },
    )
    output = io.StringIO()
    monkeypatch.setattr("sys.stdout", output)

    render_doctor_brief(data)

    assert data["analysis_profile"] == "deep"
    assert "profile=deep" in output.getvalue()
