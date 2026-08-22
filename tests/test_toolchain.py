"""Tests for toolchain engine."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.core.toolchain import ToolCapability
from ici.engines.toolchain import ToolchainEngine


def _fake_collect(available_names):
    def _collect(name, probe, cwd=None, timeout=15.0):
        if name in available_names:
            return (
                ToolCapability(name=name, path=f"/usr/bin/{name}", available=True, version="1.2.3"),
                None,
            )
        return ToolCapability(name=name, path="", available=False, error="not found"), None

    return _collect


def test_all_tools_available_passes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ici.engines.toolchain.collect_tool_capability", _fake_collect({"g++"}))
    cfg = {
        "engines": {"toolchain": {"mode": "pass_warn_fail", "required": False, "tools": ["g++"]}}
    }
    result = ToolchainEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.PASS
    assert "Toolchain OK" in result.summary
    assert result.extra["capabilities"][0]["version"] == "1.2.3"


def test_optional_missing_tool_warns(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ici.engines.toolchain.collect_tool_capability", _fake_collect(set()))
    cfg = {
        "engines": {
            "toolchain": {
                "mode": "pass_warn_fail",
                "required": False,
                "tools": ["cmake"],
                "required_tools": [],
            }
        }
    }
    result = ToolchainEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN
    assert any(t.status == EngineStatus.WARN for t in result.targets)


def test_required_missing_tool_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("ici.engines.toolchain.collect_tool_capability", _fake_collect(set()))
    cfg = {
        "engines": {
            "toolchain": {
                "mode": "pass_warn_fail",
                "required": True,
                "tools": ["g++", "cmake"],
                "required_tools": ["g++"],
            }
        }
    }
    result = ToolchainEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.ERROR
    missing = [t for t in result.targets if t.status == EngineStatus.ERROR]
    assert len(missing) == 1 and "g++" in missing[0].target_name
