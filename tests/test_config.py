"""Tests for config auto-generation and global default policy creation."""

from pathlib import Path

from ici.config import DEFAULT_CONFIG, get_global_config_path, load_config


def test_load_config_auto_creates_global_default(tmp_path: Path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ICI_CONFIG", raising=False)

    config = load_config(tmp_path / "empty_project")
    global_path = xdg / "ici" / "ici.toml"
    assert global_path.exists()
    assert "engines" in config
    assert "engines" in global_path.read_text(encoding="utf-8")


def test_load_config_does_not_create_when_project_config_exists(tmp_path: Path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ICI_CONFIG", raising=False)

    (tmp_path / "ici.toml").write_text("[engines.line]\nwarn_limit = 300\n", encoding="utf-8")
    config = load_config(tmp_path)
    assert config["engines"]["line"]["warn_limit"] == 300
    assert not (xdg / "ici" / "ici.toml").exists()


def test_load_config_respects_ici_config_env(tmp_path: Path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[engines.line]\nwarn_limit = 42\n", encoding="utf-8")
    monkeypatch.setenv("ICI_CONFIG", str(explicit))

    config = load_config(tmp_path)
    assert config["engines"]["line"]["warn_limit"] == 42
    assert not (xdg / "ici" / "ici.toml").exists()


def test_default_config_has_layout_and_line_gate_keys():
    assert DEFAULT_CONFIG["project"]["source_dirs"] == ["src", "lib", "app", "packages", "python"]
    assert DEFAULT_CONFIG["engines"]["line"]["gate_dirs"] == ["src", "include", "lib", "app"]
    assert DEFAULT_CONFIG["engines"]["line"]["include_dirs"] == []
    assert DEFAULT_CONFIG["engines"]["line"]["exclude_dirs"] == []


def test_get_global_config_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert get_global_config_path() == (tmp_path / "xdg" / "ici" / "ici.toml")
