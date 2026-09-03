"""Tests for config auto-generation and global default policy creation."""

from copy import deepcopy
from pathlib import Path

import pytest
import tomli

from ici import __version__
from ici.config import DEFAULT_CONFIG, ConfigError, get_global_config_path, load_config
from ici.config_schema import validate_config, validate_config_paths
from ici.core.pipeline import apply_analysis_profile
from ici.engines.line import LineCountEngine

ENGINE_NAMES = (
    "line",
    "lint",
    "compile_db",
    "test",
    "type",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cycle",
    "cognitive",
    "security",
    "resource",
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
    # No hardcoded version literal here: the invariant is that the shipped
    # default policy tracks the package version, and pinning the string just
    # forces an extra edit every release. Cross-file sync with the repository
    # ici.toml is covered by test_repository_ici_version_matches_package_version.
    assert DEFAULT_CONFIG["ici"]["version"] == __version__
    assert DEFAULT_CONFIG["project"]["source_dirs"] == ["src", "lib", "app", "packages", "python"]
    assert DEFAULT_CONFIG["engines"]["line"]["gate_dirs"] == ["src", "include", "lib", "app"]
    assert DEFAULT_CONFIG["engines"]["line"]["include_dirs"] == []
    assert DEFAULT_CONFIG["engines"]["line"]["exclude_dirs"] == []
    assert DEFAULT_CONFIG["engines"]["lint"]["clang_tidy"] == "auto"
    assert DEFAULT_CONFIG["engines"]["lint"]["clazy"] == "auto"
    assert DEFAULT_CONFIG["engines"]["lint"]["clazy_profile"] == "level0"
    assert DEFAULT_CONFIG["engines"]["complexity"]["cpp_boundaries"] == "auto"
    assert DEFAULT_CONFIG["engines"]["dead"]["cpp_unused"] == "auto"
    assert DEFAULT_CONFIG["doctor"]["required_tools"] == []


def test_default_deep_only_engines_are_selected_only_by_deep_profile():
    standard, _ = apply_analysis_profile(DEFAULT_CONFIG, "standard")
    deep, _ = apply_analysis_profile(DEFAULT_CONFIG, "deep")

    assert DEFAULT_CONFIG["engines"]["cognitive"]["enabled"] is True
    assert DEFAULT_CONFIG["engines"]["thread_sanitize"]["enabled"] is True
    assert standard["engines"]["cognitive"]["enabled"] is False
    assert standard["engines"]["thread_sanitize"]["enabled"] is False
    assert deep["engines"]["cognitive"]["enabled"] is True
    assert deep["engines"]["thread_sanitize"]["enabled"] is True


@pytest.mark.parametrize("profile", ["fast", "standard", "deep"])
def test_config_schema_accepts_ici_profile(profile: str):
    config = deepcopy(DEFAULT_CONFIG)
    config["ici"]["profile"] = profile

    validate_config(config)


@pytest.mark.parametrize("profile", ["", "turbo", None, 1])
def test_config_schema_rejects_invalid_ici_profile(profile):
    config = deepcopy(DEFAULT_CONFIG)
    config["ici"]["profile"] = profile

    with pytest.raises(ConfigError, match=r"ici\.profile"):
        validate_config(config)


def test_load_config_accepts_doctor_required_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        '[doctor]\nrequired_tools = ["g++", "cmake"]\n', encoding="utf-8"
    )

    config = load_config(tmp_path)

    assert config["doctor"]["required_tools"] == ["g++", "cmake"]


def test_load_config_accepts_project_compile_database_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        '[project]\ncompile_database = "out/debug/compile_commands.json"\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config["project"]["compile_database"] == "out/debug/compile_commands.json"


@pytest.mark.parametrize(
    "value",
    [
        '"../outside.json"',
        '"/tmp/outside.json"',
        '"build\\\\compile_commands.json"',
        '"C:compile_commands.json"',
        "123",
    ],
)
def test_load_config_rejects_invalid_project_compile_database_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        f"[project]\ncompile_database = {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"project\.compile_database|outside project root"):
        load_config(tmp_path)


def test_load_config_rejects_non_list_doctor_required_tools(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text('[doctor]\nrequired_tools = "g++"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match=r"doctor\.required_tools"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_doctor_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text("[doctor]\nbogus = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"doctor\.bogus"):
        load_config(tmp_path)


def test_get_global_config_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert get_global_config_path() == (tmp_path / "xdg" / "ici" / "ici.toml")


def test_repository_test_policy_keeps_strict_calibrated_floors():
    """The repository policy stays strict while allowing measured baseline jitter."""
    policy_path = Path(__file__).resolve().parent.parent / "ici.toml"
    with policy_path.open("rb") as policy_file:
        test_policy = tomli.load(policy_file)["engines"]["test"]

    assert test_policy["mode"] == "pass_fail"
    assert test_policy["min_tem_score"] == 4.5
    assert test_policy["min_branch_cov"] == 70.0
    assert test_policy["min_func_cov"] == 90.0


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


def _lint_config(**values):
    lint = {
        "clang_tidy": "auto",
        "clang_tidy_checks": ["-*", "bugprone-*", "readability-identifier-naming"],
        "clang_tidy_config": "config/.clang-tidy",
    }
    lint.update(values)
    return {"engines": {"lint": lint}}


@pytest.mark.parametrize("mode", ["auto", "required", "off"])
def test_config_schema_accepts_clang_tidy_settings(mode: str):
    config = _lint_config(clang_tidy=mode)

    validate_config(config)


@pytest.mark.parametrize("mode", ["auto", "required", "off"])
def test_config_schema_accepts_clazy_settings(mode: str):
    validate_config(_lint_config(clazy=mode))


@pytest.mark.parametrize("profile", ["level0", "level1"])
def test_config_schema_accepts_clazy_profiles(profile: str):
    validate_config(_lint_config(clazy_profile=profile))


def test_config_schema_accepts_explicit_clazy_checks():
    validate_config(_lint_config(clazy_checks=["qdatetime-utc", "qstring-arg", "level0"]))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("clang_tidy", "always"),
        ("clang_tidy", 1),
        ("clang_tidy_checks", "bugprone-*"),
        ("clang_tidy_config", ["config/.clang-tidy"]),
    ],
)
def test_config_schema_rejects_invalid_clang_tidy_types_and_mode(key, value):
    with pytest.raises(ConfigError, match=rf"engines\.lint\.{key}"):
        validate_config(_lint_config(**{key: value}))


@pytest.mark.parametrize("mode", ["auto", "required", "off"])
def test_config_schema_accepts_cpp_function_boundary_modes(mode: str) -> None:
    validate_config({"engines": {"complexity": {"cpp_boundaries": mode}}})


@pytest.mark.parametrize("mode", ["always", "", 1, True, None])
def test_config_schema_rejects_invalid_cpp_function_boundary_modes(mode: object) -> None:
    with pytest.raises(ConfigError, match=r"engines\.complexity\.cpp_boundaries"):
        validate_config({"engines": {"complexity": {"cpp_boundaries": mode}}})


@pytest.mark.parametrize("mode", ["auto", "required", "off"])
def test_config_schema_accepts_cpp_unused_function_modes(mode: str) -> None:
    validate_config({"engines": {"dead": {"cpp_unused": mode}}})


@pytest.mark.parametrize("mode", ["always", "", 1, True, None])
def test_config_schema_rejects_invalid_cpp_unused_function_modes(mode: object) -> None:
    with pytest.raises(ConfigError, match=r"engines\.dead\.cpp_unused"):
        validate_config({"engines": {"dead": {"cpp_unused": mode}}})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("clazy", "always"),
        ("clazy", 1),
        ("clazy_profile", "level2"),
        ("clazy_profile", 1),
        ("clazy_checks", "qdatetime-utc"),
    ],
)
def test_config_schema_rejects_invalid_clazy_types_and_modes(key, value):
    with pytest.raises(ConfigError, match=rf"engines\.lint\.{key}"):
        validate_config(_lint_config(**{key: value}))


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [""],
        ["bugprone-*", "bugprone-*"],
        ["bugprone,foo"],
        ["bugprone foo"],
        ["bugprone\x00foo"],
        ["bugprone?foo"],
        ["a" * 129],
        [f"check_{index:03d}_" + "a" * 118 for index in range(65)],
        [f"check{index}" for index in range(129)],
    ],
)
def test_config_schema_rejects_invalid_clang_tidy_checks(checks):
    with pytest.raises(ConfigError, match=r"engines\.lint\.clang_tidy_checks"):
        validate_config(_lint_config(clang_tidy_checks=checks))


@pytest.mark.parametrize(
    "checks",
    [
        [],
        [""],
        ["qdatetime-utc", "qdatetime-utc"],
        ["qdatetime,utc"],
        ["qdatetime utc"],
        ["qdatetime\x00utc"],
        ["qdatetime?utc"],
        ["a" * 129],
        [f"check_{index:03d}_" + "a" * 118 for index in range(65)],
        [f"check{index}" for index in range(129)],
    ],
)
def test_config_schema_rejects_invalid_clazy_checks(checks):
    with pytest.raises(ConfigError, match=r"engines\.lint\.clazy_checks"):
        validate_config(_lint_config(clazy_checks=checks))


@pytest.mark.parametrize("config", ["", "   ", "a" * 4097])
def test_config_schema_rejects_invalid_clang_tidy_config(config: str):
    with pytest.raises(ConfigError, match=r"engines\.lint\.clang_tidy_config"):
        validate_config(_lint_config(clang_tidy_config=config))


def test_validate_config_paths_rejects_clang_tidy_config_outside_project(tmp_path: Path):
    with pytest.raises(ConfigError, match=r"engines\.lint\.clang_tidy_config|outside project root"):
        validate_config_paths(
            _lint_config(clang_tidy_config="../outside/.clang-tidy"),
            tmp_path,
        )


def test_validate_config_paths_accepts_clang_tidy_config_inside_project(tmp_path: Path):
    validate_config_paths(
        _lint_config(clang_tidy_config="config/.clang-tidy"),
        tmp_path,
    )


def test_load_config_accepts_compile_database_gate_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        "[engines.compile_db]\n"
        "database_required = true\n"
        'required_flags = ["-Wall", "-std=c++20"]\n'
        'forbidden_flags = ["-fpermissive"]\n',
        encoding="utf-8",
    )

    config = load_config(tmp_path)

    assert config["engines"]["compile_db"] == {
        "enabled": True,
        "mode": "pass_warn_fail",
        "database_required": True,
        "required_flags": ["-Wall", "-std=c++20"],
        "forbidden_flags": ["-fpermissive"],
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("database_required", '"yes"'),
        ("required_flags", '"-Wall"'),
        ("forbidden_flags", '[""]'),
    ],
)
def test_load_config_rejects_invalid_compile_database_gate_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: str,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "ici.toml").write_text(
        f"[engines.compile_db]\n{key} = {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=rf"engines\.compile_db\.{key}"):
        load_config(tmp_path)


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
