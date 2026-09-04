"""Validation for opt-in build and analysis-contract configuration."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ici._config_validation import (
    _error,
    _reject_unknown,
    _require_bool,
    _require_int,
    _require_number,
    _require_string,
    _require_string_list,
    _validate_common_engine,
    _validate_direct_argv,
    _validate_relative_setting,
    _validate_string_list_bounded,
)

_BUILD_MAKE_KEYS = frozenset(
    {
        "enabled",
        "workdir",
        "shadow_dir",
        "out_of_tree",
        "configure_argv",
        "build_argv",
        "test_argv",
        "clean_argv",
        "jobs",
        "coverage_build_argv",
        "coverage_test_argv",
        "sanitize_build_argv",
        "sanitize_test_argv",
        "thread_sanitize_build_argv",
        "thread_sanitize_test_argv",
    }
)
_MAX_INTEGRATION_CASES = 32
_MAX_INTEGRATION_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_INTEGRATION_ASSERTIONS = 32
_MAX_INTEGRATION_ENV = 32
_MAX_BINARY_ARTIFACTS = 64
_MAX_BINARY_DEPENDENCIES = 256
_MAX_BINARY_VERSION_LENGTH = 32
_SAFE_INTEGRATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_ABI_VERSION = re.compile(r"\d+(?:\.\d+)+\Z")
_INTEGRATION_PLACEHOLDER = re.compile(r"\{(?:python|artifact):[^{}]+\}\Z")


def _validate_build_make(table: Any, path: str) -> None:
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _BUILD_MAKE_KEYS, path)
    enabled = table.get("enabled", False)
    _require_bool(enabled, f"{path}.enabled")
    for key, allow_root in (("workdir", True), ("shadow_dir", False)):
        if key in table:
            _validate_relative_setting(table[key], f"{path}.{key}", allow_root=allow_root)
    out_of_tree = table.get("out_of_tree", "allow")
    _require_string(out_of_tree, f"{path}.out_of_tree", non_empty=True)
    if out_of_tree not in {"allow", "required"}:
        raise _error(f"{path}.out_of_tree", "must be one of: allow, required")
    if out_of_tree == "required" and PurePosixPath(table.get("workdir", ".")) == PurePosixPath("."):
        raise _error(f"{path}.out_of_tree", "required needs a non-root workdir")
    if "jobs" in table:
        _require_int(table["jobs"], f"{path}.jobs", minimum=1)
        if table["jobs"] > 64:
            raise _error(f"{path}.jobs", "must be less than or equal to 64")
    argv_keys = (
        "configure_argv",
        "build_argv",
        "test_argv",
        "clean_argv",
        "coverage_build_argv",
        "coverage_test_argv",
        "sanitize_build_argv",
        "sanitize_test_argv",
        "thread_sanitize_build_argv",
        "thread_sanitize_test_argv",
    )
    for key in argv_keys:
        if key in table:
            _validate_direct_argv(
                table[key], f"{path}.{key}", allow_empty=key != "build_argv" or not enabled
            )
    if enabled and not table.get("build_argv"):
        raise _error(f"{path}.build_argv", "must contain a direct command argv when enabled")


def _validate_binary_compat(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "artifacts" in table:
        _validate_string_list_bounded(
            table["artifacts"], f"{path}.artifacts", maximum=_MAX_BINARY_ARTIFACTS
        )
    for key in ("expected_class", "expected_machine"):
        if key in table:
            value = table[key]
            _require_string(value, f"{path}.{key}")
            if len(value) > 128 or any(ord(character) < 32 for character in value):
                raise _error(f"{path}.{key}", "must be a safe string of at most 128 characters")
    for key in ("max_glibc", "max_glibcxx", "max_cxxabi"):
        if key not in table:
            continue
        value = table[key]
        _require_string(value, f"{path}.{key}")
        if value and (
            len(value) > _MAX_BINARY_VERSION_LENGTH or _ABI_VERSION.fullmatch(value) is None
        ):
            raise _error(f"{path}.{key}", "must be empty or an ABI version such as 2.17")
    for key in ("forbid_absolute_rpath", "forbid_build_paths", "allow_non_elf"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")
    for key in ("forbidden_needed", "allowed_needed"):
        if key in table:
            _validate_string_list_bounded(
                table[key], f"{path}.{key}", maximum=_MAX_BINARY_DEPENDENCIES
            )
    if "max_artifacts" in table:
        _require_int(table["max_artifacts"], f"{path}.max_artifacts", minimum=1)
        if table["max_artifacts"] > _MAX_BINARY_ARTIFACTS:
            raise _error(f"{path}.max_artifacts", "must be less than or equal to 64")


def _validate_integration_id(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _SAFE_INTEGRATION_ID.fullmatch(value):
        raise _error(path, "must be an identifier containing only letters, digits, _, ., or -")


def _validate_bounded_text(value: Any, path: str, *, maximum: int) -> None:
    _require_string(value, path, non_empty=True)
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise _error(path, f"must be a safe string of at most {maximum} characters")


def _validate_integration_text_list(value: Any, path: str) -> None:
    _validate_string_list_bounded(
        value,
        path,
        maximum=_MAX_INTEGRATION_ASSERTIONS,
        item_length=1024,
    )


def _validate_integration_case(case: Any, path: str) -> None:
    if not isinstance(case, dict):
        raise _error(path, "must be a table")
    allowed = frozenset(
        {
            "name",
            "argv",
            "expected_exit",
            "stdout_contains",
            "stderr_contains",
            "stdout_not_contains",
            "stderr_not_contains",
            "timeout_seconds",
            "inherit_env",
            "env",
            "output_artifacts",
            "required",
        }
    )
    _reject_unknown(case, allowed, path)
    _validate_bounded_text(case.get("name"), f"{path}.name", maximum=128)
    argv = case.get("argv")
    _validate_direct_argv(argv, f"{path}.argv", allow_empty=False, allow_placeholders=True)
    _validate_integration_argv_placeholders(argv, f"{path}.argv")
    expected_exit = case.get("expected_exit", 0)
    if type(expected_exit) is not int or not -(2**31) <= expected_exit < 2**31:
        raise _error(f"{path}.expected_exit", "must be a signed 32-bit integer")
    _validate_integration_assertions(case, path)
    if "timeout_seconds" in case:
        _require_number(
            case["timeout_seconds"], f"{path}.timeout_seconds", minimum=0.1, maximum=300
        )
    _validate_integration_environment(case, path)
    _validate_integration_outputs(case, path)
    if "required" in case:
        _require_bool(case["required"], f"{path}.required")


def _validate_integration_argv_placeholders(argv: Any, path: str) -> None:
    if argv and _INTEGRATION_PLACEHOLDER.fullmatch(argv[0]) is None:
        raise _error(f"{path}[0]", "must be a typed Python or artifact placeholder")
    if not argv:
        return
    for index, token in enumerate(argv):
        if ("{" in token or "}" in token) and _INTEGRATION_PLACEHOLDER.fullmatch(token) is None:
            raise _error(f"{path}[{index}]", "must use a known whole-token placeholder")


def _validate_integration_assertions(case: dict[str, Any], path: str) -> None:
    for key in (
        "stdout_contains",
        "stderr_contains",
        "stdout_not_contains",
        "stderr_not_contains",
    ):
        if key in case:
            _validate_integration_text_list(case[key], f"{path}.{key}")


def _validate_integration_environment(case: dict[str, Any], path: str) -> None:
    if "inherit_env" in case:
        _validate_string_list_bounded(
            case["inherit_env"], f"{path}.inherit_env", maximum=_MAX_INTEGRATION_ENV
        )
        for index, name in enumerate(case["inherit_env"]):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z", name):
                raise _error(f"{path}.inherit_env[{index}]", "must be a valid environment name")
    if "env" not in case:
        return
    env = case["env"]
    if not isinstance(env, dict) or len(env) > _MAX_INTEGRATION_ENV:
        raise _error(f"{path}.env", f"must be a table with at most {_MAX_INTEGRATION_ENV} values")
    for name, value in env.items():
        name_path = f"{path}.env.{name}"
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z", name):
            raise _error(name_path, "must be a valid environment name")
        if (
            not isinstance(value, str)
            or len(value) > 4096
            or any(ord(character) < 32 for character in value)
        ):
            raise _error(name_path, "must be a safe string of at most 4096 characters")


def _validate_integration_outputs(case: dict[str, Any], path: str) -> None:
    if "output_artifacts" not in case:
        return
    outputs = case["output_artifacts"]
    if not isinstance(outputs, list) or len(outputs) > _MAX_INTEGRATION_ASSERTIONS:
        raise _error(
            f"{path}.output_artifacts",
            f"must contain at most {_MAX_INTEGRATION_ASSERTIONS} tables",
        )
    seen_paths: set[str] = set()
    for index, output in enumerate(outputs):
        output_path = f"{path}.output_artifacts[{index}]"
        if not isinstance(output, dict):
            raise _error(output_path, "must be a table")
        _reject_unknown(output, frozenset({"path", "kind", "min_size"}), output_path)
        _validate_relative_setting(output.get("path"), f"{output_path}.path", allow_root=False)
        output_name = output["path"]
        if output_name in seen_paths:
            raise _error(f"{output_path}.path", "must be unique")
        seen_paths.add(output_name)
        _validate_integration_output_metadata(output, output_path)


def _validate_integration_output_metadata(output: dict[str, Any], path: str) -> None:
    if "kind" in output:
        _require_string(output["kind"], f"{path}.kind", non_empty=True)
        if len(output["kind"]) > 128 or any(ord(character) < 32 for character in output["kind"]):
            raise _error(f"{path}.kind", "must be a safe string of at most 128 characters")
    if "min_size" in output:
        _require_int(output["min_size"], f"{path}.min_size", minimum=0)
        if output["min_size"] > 64 * 1024 * 1024:
            raise _error(f"{path}.min_size", "must be less than or equal to 67108864")


def _validate_integration(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    max_cases = table.get("max_cases", _MAX_INTEGRATION_CASES)
    _require_int(max_cases, f"{path}.max_cases", minimum=1)
    if max_cases > _MAX_INTEGRATION_CASES:
        raise _error(f"{path}.max_cases", "must be less than or equal to 32")
    max_output = table.get("max_output_bytes", 64 * 1024)
    _require_int(max_output, f"{path}.max_output_bytes", minimum=1024)
    if max_output > _MAX_INTEGRATION_OUTPUT_BYTES:
        raise _error(
            f"{path}.max_output_bytes",
            f"must be less than or equal to {_MAX_INTEGRATION_OUTPUT_BYTES}",
        )
    _validate_python_targets(table.get("python_targets", {}), f"{path}.python_targets")
    cases = table.get("cases", [])
    if not isinstance(cases, list) or len(cases) > max_cases:
        raise _error(f"{path}.cases", f"must contain at most {max_cases} tables")
    names: set[str] = set()
    for index, case in enumerate(cases):
        case_path = f"{path}.cases[{index}]"
        _validate_integration_case(case, case_path)
        name = case["name"]
        if name in names:
            raise _error(f"{case_path}.name", "must be unique")
        names.add(name)


def _validate_python_targets(targets: Any, path: str) -> None:
    if not isinstance(targets, dict) or len(targets) > _MAX_INTEGRATION_ENV:
        raise _error(path, f"must be a table with at most {_MAX_INTEGRATION_ENV} values")
    for name, value in targets.items():
        _validate_integration_id(name, f"{path}.{name}")
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise _error(
                f"{path}.{name}",
                "must be a non-empty executable path of at most 1024 characters",
            )
        if any(ord(character) < 32 for character in value):
            raise _error(f"{path}.{name}", "must not contain control characters")


def _validate_python_compat(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key, limit in (("interpreters", 32), ("required_interpreters", 32), ("imports", 64)):
        _validate_python_compat_list(table, path, key, limit)
    interpreters = table.get("interpreters", [])
    required = table.get("required_interpreters", [])
    if any(value not in interpreters for value in required):
        raise _error(f"{path}.required_interpreters", "must be a subset of interpreters")
    _validate_python_target_version(table, path)
    for key in ("wheel_required", "check_entrypoints", "check_package_files"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")
    _validate_wheel_policy(table, path)
    _validate_wheel_globs(table, path)
    for key, maximum in (
        ("max_wheels", 32),
        ("max_wheel_members", 8192),
        ("max_wheel_uncompressed_bytes", 64 * 1024 * 1024),
    ):
        if key in table:
            _require_int(table[key], f"{path}.{key}", minimum=1)
            if table[key] > maximum:
                raise _error(f"{path}.{key}", f"must be less than or equal to {maximum}")


def _validate_python_compat_list(table: dict[str, Any], path: str, key: str, limit: int) -> None:
    if key not in table:
        return
    item_path = f"{path}.{key}"
    _require_string_list(table[key], item_path)
    values = table[key]
    if len(values) > limit:
        raise _error(item_path, f"must contain at most {limit} values")
    if len(values) != len(set(values)):
        raise _error(item_path, "must not contain duplicate values")
    for index, value in enumerate(values):
        if len(value) > 1024 or any(ord(character) < 32 for character in value):
            raise _error(
                f"{item_path}[{index}]", "must be a safe string of at most 1024 characters"
            )
        if key == "imports" and not all(part.isidentifier() for part in value.split(".")):
            raise _error(f"{item_path}[{index}]", "must be a dotted Python module name")


def _validate_python_target_version(table: dict[str, Any], path: str) -> None:
    if "target_version" not in table:
        return
    value = table["target_version"]
    _require_string(value, f"{path}.target_version")
    if value and not re.fullmatch(r"3\.(?:[7-9]|[1-9][0-9])", value):
        raise _error(f"{path}.target_version", "must be empty or a Python 3 minor such as 3.10")


def _validate_wheel_policy(table: dict[str, Any], path: str) -> None:
    if "wheel_policy" not in table:
        return
    value = table["wheel_policy"]
    _require_string(value, f"{path}.wheel_policy", non_empty=True)
    if value not in {"allow-native", "pure"}:
        raise _error(f"{path}.wheel_policy", "must be one of: allow-native, pure")


def _validate_wheel_globs(table: dict[str, Any], path: str) -> None:
    if "wheel_globs" not in table:
        return
    item_path = f"{path}.wheel_globs"
    _require_string_list(table["wheel_globs"], item_path)
    values = table["wheel_globs"]
    if len(values) > 32:
        raise _error(item_path, "must contain at most 32 values")
    if len(values) != len(set(values)):
        raise _error(item_path, "must not contain duplicate values")
    for index, value in enumerate(values):
        pure = PureWindowsPath(value)
        if (
            len(value) > 256
            or not value
            or "\\" in value
            or value.startswith("/")
            or pure.drive
            or ".." in Path(value).parts
            or any(ord(character) < 32 for character in value)
        ):
            raise _error(
                f"{item_path}[{index}]",
                "must be a contained POSIX glob of at most 256 characters",
            )
