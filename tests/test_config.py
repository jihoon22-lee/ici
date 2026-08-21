"""Tests for config auto-generation and global default policy creation."""

from pathlib import Path

import pytest
import tomli

from ici import __version__
from ici.config import DEFAULT_CONFIG, ConfigError, get_global_config_path, load_config
from ici.engines.line import LineCountEngine

ENGINE_NAMES = (
    "line",
    "lint",
    "test",
    "type",
    "complexity",
    "sanitize",
    "dead",
    "dup",
    "exception",
)


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
    assert DEFAULT_CONFIG["ici"]["version"] == __version__ == "0.4.2"
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


def test_repository_ici_version_matches_package_version():
    """Repository ici.toml version must stay in sync with the package version."""
    policy_path = Path(__file__).resolve().parent.parent / "ici.toml"
    with policy_path.open("rb") as policy_file:
        policy_version = tomli.load(policy_file).get("ici", {}).get("version")
    assert policy_version == __version__


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


def test_load_config_accepts_test_interpreter_and_coverage_policy(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        '[engines.test]\npython = "/opt/project/.venv/bin/python"\ncoverage_required = true\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config["engines"]["test"]["python"] == "/opt/project/.venv/bin/python"
    assert config["engines"]["test"]["coverage_required"] is True


def test_load_config_accepts_lint_and_type_tool_required_policies(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        "[engines.lint]\nruff_required = true\n[engines.type]\nmypy_required = true\n",
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config["engines"]["lint"]["ruff_required"] is True
    assert config["engines"]["type"]["mypy_required"] is True


@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_load_config_accepts_common_required_policy_for_every_engine(
    tmp_path: Path, monkeypatch, engine_name: str
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        f"[engines.{engine_name}]\nrequired = false\n", encoding="utf-8"
    )

    config = load_config(tmp_path)

    assert config["engines"][engine_name]["required"] is False


@pytest.mark.parametrize("engine_name", ENGINE_NAMES)
def test_load_config_rejects_non_boolean_common_required_policy(
    tmp_path: Path, monkeypatch, engine_name: str
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        f'[engines.{engine_name}]\nrequired = "yes"\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError, match=rf"engines\.{engine_name}\.required"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("table", "key"),
    [("lint", "ruff_required"), ("type", "mypy_required")],
)
def test_load_config_rejects_non_boolean_tool_required_policy(
    tmp_path: Path, monkeypatch, table: str, key: str
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        f'[engines.{table}]\n{key} = "yes"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=rf"engines\.{table}\.{key}"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("key", "value"),
    [("coverage_required", '"yes"'), ("python", "false")],
)
def test_load_config_rejects_invalid_test_execution_policy(
    tmp_path: Path, monkeypatch, key: str, value: str
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(f"[engines.test]\n{key} = {value}\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=rf"engines\.test\.{key}"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_top_level_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text("[not_a_setting]\nvalue = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="not_a_setting"):
        load_config(tmp_path)


def test_load_config_accepts_build_python_entrypoint(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        '[build.python]\nentrypoint = "pkg.cli:main"\n', encoding="utf-8"
    )

    config = load_config(tmp_path)

    assert config["build"]["python"]["entrypoint"] == "pkg.cli:main"


@pytest.mark.parametrize(
    "config_text",
    [
        "[build]\nunexpected = true\n",
        '[build]\npython = "pkg.cli:main"\n',
        '[build.python]\nunknown = "value"\n',
        "[build.python]\nentrypoint = 42\n",
        '[build.python]\nentrypoint = "  "\n',
    ],
)
def test_load_config_rejects_invalid_build_python_schema(
    tmp_path: Path, monkeypatch, config_text: str
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(config_text, encoding="utf-8")

    with pytest.raises(ConfigError, match=r"build"):
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


def test_load_config_rejects_absolute_line_include_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    outside = tmp_path.parent / "outside"
    (tmp_path / "ici.toml").write_text(
        f'[engines.line]\ninclude_dirs = ["{outside}"]\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="outside project root"):
        load_config(tmp_path)


def test_load_config_rejects_parent_source_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        '[project]\nsource_dirs = ["../outside"]\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="outside project root"):
        load_config(tmp_path)


def test_load_config_rejects_symlink_source_dir_escape(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    (tmp_path / "ici.toml").write_text('[project]\nsource_dirs = ["linked"]\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="outside project root"):
        load_config(tmp_path)


def test_load_config_rejects_symlink_loop_source_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second, target_is_directory=True)
    second.symlink_to(first, target_is_directory=True)
    (tmp_path / "ici.toml").write_text('[project]\nsource_dirs = ["first"]\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="resolve"):
        load_config(tmp_path)


def test_load_config_rejects_invalid_utf8(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_bytes(b"[engines.line]\nwarn_limit = \xff\n")

    with pytest.raises(ConfigError, match=r"ici\.toml"):
        load_config(tmp_path)


def test_load_config_rejects_oversized_numeric_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    huge_integer = "9" * 400
    (tmp_path / "ici.toml").write_text(
        f"[engines.test]\nmin_tem_score = {huge_integer}\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="min_tem_score"):
        load_config(tmp_path)


def test_load_config_rejects_parser_level_ten_thousand_digit_integer(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    huge_integer = "9" * 10_000
    (tmp_path / "ici.toml").write_text(
        f"[engines.test]\nmin_tem_score = {huge_integer}\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match=r"could not parse configuration"):
        load_config(tmp_path)


@pytest.mark.parametrize(
    "pathological_config",
    [
        pytest.param("value = " + "[" * 1000 + "1" + "]" * 1000 + "\n", id="nested-arrays"),
        pytest.param("a" + ".a" * 1000 + " = 1\n", id="dotted-key-parts"),
    ],
)
def test_load_config_rejects_pathological_toml_as_config_error(
    tmp_path: Path, monkeypatch, pathological_config: str
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(pathological_config, encoding="utf-8")

    with pytest.raises(ConfigError, match=r"could not parse configuration"):
        load_config(tmp_path)


def test_load_config_rejects_explicit_symlink_loop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    first = tmp_path / "explicit-first.toml"
    second = tmp_path / "explicit-second.toml"
    first.symlink_to(second)
    second.symlink_to(first)
    monkeypatch.setenv("ICI_CONFIG", str(first))

    with pytest.raises(ConfigError, match="resolve"):
        load_config(tmp_path)


def test_engine_rejects_line_path_outside_project(tmp_path: Path):
    outside = tmp_path.parent / "outside"

    with pytest.raises(ConfigError, match="outside project root"):
        LineCountEngine(
            tmp_path,
            config={"engines": {"line": {"include_dirs": [str(outside)]}}},
        )
