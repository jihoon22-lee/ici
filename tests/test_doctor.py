"""Tests for doctor diagnostics and its required_tools config wiring."""

from pathlib import Path

from ici.config import load_config
from ici.doctor import collect_diagnostics


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
    assert data["tools"]["gcc"]["required"] is False
