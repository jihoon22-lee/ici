"""Strict configuration contracts for build, ABI, and integration evidence."""

from __future__ import annotations

from copy import deepcopy

import pytest

from ici.config import DEFAULT_CONFIG
from ici.config_schema import ConfigError, validate_config, validate_config_paths


def test_new_defaults_are_disabled_and_schema_valid() -> None:
    config = deepcopy(DEFAULT_CONFIG)

    validate_config(config)

    make = config["build"]["make"]
    assert make["enabled"] is False
    assert make["jobs"] == 1
    assert config["engines"]["build"]["enabled"] is False
    assert config["engines"]["binary_compat"]["enabled"] is False
    assert config["engines"]["integration"]["enabled"] is False


def test_make_contract_requires_build_argv_when_enabled() -> None:
    with pytest.raises(ConfigError, match=r"build\.make\.build_argv"):
        validate_config(
            {
                "build": {
                    "make": {
                        "enabled": True,
                        "workdir": ".",
                        "shadow_dir": "build/ici-make",
                    }
                },
                "engines": {},
            }
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jobs": 65}, "jobs"),
        ({"build_argv": ["sh", "-c", "make"]}, "command shell"),
        ({"build_argv": ["make", "$(touch nope)"]}, "shell metacharacters"),
        ({"shadow_dir": "."}, "project root"),
        ({"out_of_tree": "required", "workdir": "."}, "non-root"),
        ({"unknown": True}, "unknown configuration key"),
    ],
)
def test_make_contract_rejects_unsafe_values(overrides: dict[str, object], message: str) -> None:
    table: dict[str, object] = {
        "enabled": True,
        "workdir": "build",
        "shadow_dir": "build/ici-make",
        "out_of_tree": "allow",
        "build_argv": ["make", "all"],
    }
    table.update(overrides)
    with pytest.raises(ConfigError, match=message):
        validate_config({"build": {"make": table}, "engines": {}})


def test_make_paths_are_checked_after_symlink_resolution(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    config = {
        "build": {
            "make": {
                "enabled": False,
                "workdir": "linked",
                "shadow_dir": "build/ici-make",
            }
        },
        "engines": {},
    }

    validate_config(config)
    with pytest.raises(ConfigError, match="outside project root"):
        validate_config_paths(config, tmp_path)


def test_binary_compat_contract_accepts_policy_values() -> None:
    validate_config(
        {
            "engines": {
                "binary_compat": {
                    "enabled": True,
                    "mode": "pass_warn_fail",
                    "required": True,
                    "artifacts": ["executable:app"],
                    "expected_class": "ELF64",
                    "expected_machine": "Advanced Micro Devices X86-64",
                    "max_glibc": "2.17",
                    "max_glibcxx": "3.4.19",
                    "max_cxxabi": "1.3.9",
                    "forbid_absolute_rpath": True,
                    "forbidden_needed": ["libbad.so"],
                    "allowed_needed": ["libc.so.6"],
                    "forbid_build_paths": True,
                    "allow_non_elf": False,
                    "max_artifacts": 8,
                }
            }
        }
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_glibc", "latest"),
        ("max_artifacts", 0),
        ("forbid_build_paths", "yes"),
        ("artifacts", ["build/app", "build/app"]),
        ("unexpected", True),
    ],
)
def test_binary_compat_contract_rejects_invalid_values(key: str, value: object) -> None:
    config = {"engines": {"binary_compat": {key: value}}}
    with pytest.raises(ConfigError, match=r"engines\.binary_compat"):
        validate_config(config)


def test_integration_contract_accepts_typed_cases() -> None:
    validate_config(
        {
            "engines": {
                "integration": {
                    "enabled": True,
                    "required": True,
                    "max_cases": 4,
                    "max_output_bytes": 4096,
                    "python_targets": {"py310": ".venv/bin/python"},
                    "cases": [
                        {
                            "name": "python-to-cpp",
                            "argv": [
                                "{python:py310}",
                                "tests/smoke.py",
                                "{artifact:demo-server}",
                            ],
                            "expected_exit": 0,
                            "stdout_contains": ["integration-ok"],
                            "stderr_not_contains": ["traceback"],
                            "timeout_seconds": 30,
                            "inherit_env": ["PATH"],
                            "env": {"MODE": "test"},
                            "output_artifacts": [
                                {"path": "reports/result.json", "kind": "report", "min_size": 1}
                            ],
                        }
                    ],
                }
            }
        }
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("max_cases", 33, "max_cases"),
        ("max_output_bytes", 8 * 1024 * 1024 + 1, "max_output_bytes"),
        ("cases", [{"name": "bad", "argv": ["sh", "-c", "echo"]}], "command shell"),
        ("cases", [{"name": "bad", "argv": ["tool"]}], "typed Python or artifact"),
        (
            "cases",
            [{"name": "bad", "argv": ["{python:current}", "prefix-{artifact:x}"]}],
            "whole-token",
        ),
        (
            "cases",
            [
                {
                    "name": "bad",
                    "argv": ["{python:current}"],
                    "env": {"BAD-NAME": "x"},
                }
            ],
            "environment",
        ),
        (
            "cases",
            [
                {
                    "name": "bad",
                    "argv": ["{python:current}"],
                    "output_artifacts": [{"path": "../x"}],
                }
            ],
            "contained",
        ),
    ],
)
def test_integration_contract_rejects_invalid_nested_values(
    key: str, value: object, message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        validate_config({"engines": {"integration": {key: value}}})


def test_integration_contract_rejects_duplicate_case_names() -> None:
    cases = [
        {"name": "smoke", "argv": ["{python:current}"]},
        {"name": "smoke", "argv": ["{python:current}"]},
    ]
    with pytest.raises(ConfigError, match=r"cases\[1\]\.name"):
        validate_config({"engines": {"integration": {"cases": cases}}})
