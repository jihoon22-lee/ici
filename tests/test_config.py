"""Tests for config auto-generation and global default policy creation."""

from pathlib import Path

import tomli

import pytest

from ici.config import DEFAULT_CONFIG, ConfigError, get_global_config_path, load_config


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


def test_repository_test_policy_keeps_strict_calibrated_floors():
    """The repository policy stays strict while allowing measured baseline jitter."""
    policy_path = Path(__file__).resolve().parent.parent / "ici.toml"
    with policy_path.open("rb") as policy_file:
        test_policy = tomli.load(policy_file)["engines"]["test"]

    assert test_policy["mode"] == "pass_fail"
    assert test_policy["min_tem_score"] == 2.0
    assert test_policy["min_branch_cov"] == 35.0
    assert test_policy["min_func_cov"] == 60.0


def test_load_config_merges_global_project_and_explicit(tmp_path: Path, monkeypatch):
    xdg = tmp_path / "xdg"
    global_file = xdg / "ici" / "ici.toml"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("[engines.line]\nwarn_limit = 400\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text("[engines.line]\nfail_limit = 900\n", encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[engines.line]\nwarn_limit = 300\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("ICI_CONFIG", str(explicit))

    config = load_config(tmp_path)

    assert config["engines"]["line"]["warn_limit"] == 300
    assert config["engines"]["line"]["fail_limit"] == 900


def test_load_config_applies_dev_after_project_before_explicit(tmp_path: Path, monkeypatch):
    (tmp_path / "ici.toml").write_text(
        "[engines.line]\nwarn_limit = 400\nfail_limit = 900\n", encoding="utf-8"
    )
    (tmp_path / "dev.toml").write_text("[engines.line]\nwarn_limit = 350\n", encoding="utf-8")
    explicit = tmp_path / "explicit.toml"
    explicit.write_text("[engines.line]\nwarn_limit = 300\n", encoding="utf-8")
    monkeypatch.setenv("ICI_CONFIG", str(explicit))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    config = load_config(tmp_path)

    assert config["engines"]["line"]["warn_limit"] == 300
    assert config["engines"]["line"]["fail_limit"] == 900


def test_load_config_rejects_invalid_threshold_order(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        "[engines.line]\nwarn_limit = 1000\nfail_limit = 500\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="warn_limit"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_engine_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text("[engines.line]\nunexpected_limit = 10\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"engines\.line\.unexpected_limit"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_top_level_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text("[not_a_setting]\nvalue = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="not_a_setting"):
        load_config(tmp_path)


def test_load_config_rejects_invalid_mode(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text("[engines.line]\nmode = 'unknown'\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="mode"):
        load_config(tmp_path)


def test_load_config_rejects_malformed_toml(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text("[engines.line\nwarn_limit = 300\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"ici\.toml"):
        load_config(tmp_path)


def test_load_config_rejects_missing_explicit_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("ICI_CONFIG", str(missing))

    with pytest.raises(ConfigError, match=r"missing\.toml"):
        load_config(tmp_path)
