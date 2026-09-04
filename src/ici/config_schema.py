"""Validation for the layered ici configuration.

The configuration is intentionally validated without a third-party schema
library.  Keeping the schema in Python makes the zipapp self-contained and
lets us report the exact dotted key that caused a configuration error.
"""

import math
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


class ConfigError(ValueError):
    """Raised when an ici configuration is malformed or violates policy."""


MODES = frozenset({"pass_warn_fail", "pass_fail", "pass_warn"})
PROJECT_TYPES = frozenset({"python", "cpp", "hybrid"})
ANALYSIS_PROFILES = frozenset({"fast", "standard", "deep"})
CLANG_TIDY_MODES = frozenset({"auto", "required", "off"})
CLAZY_MODES = frozenset({"auto", "required", "off"})
CLAZY_PROFILES = frozenset({"level0", "level1"})
MYPY_PROFILES = frozenset({"project", "ici"})
_TOOL_CHECK_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.*-")
_MAX_TOOL_CHECKS = 128
_MAX_TOOL_CHECK_LENGTH = 128
_MAX_TOOL_CHECKS_JOINED_LENGTH = 8192
_MAX_CLANG_TIDY_CONFIG_LENGTH = 4096
_MAX_MAKE_ARGV = 64
_MAX_MAKE_ARG_LENGTH = 1024
_MAX_MAKE_ARGV_LENGTH = 32 * 1024
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

_TOP_LEVEL_KEYS = frozenset(
    {"ici", "project", "engines", "build", "doctor", "name", "type", "version"}
)
_ICI_KEYS = frozenset({"version", "policy_name", "profile"})
_PROJECT_KEYS = frozenset(
    {
        "source_dirs",
        "name",
        "type",
        "version",
        # Packages whose pkg-config --cflags are appended to C++ compile flags,
        # so toolkit-backed sources (Qt widgets and the like) can be checked.
        "cpp_pkg_config",
        # C++ that ici analyses but does not compile itself: moc-dependent or
        # build-system-driven sources that a bare g++ call cannot produce.
        "cpp_external_build_dirs",
        # Optional deterministic compilation database selection. When absent,
        # ici checks only the root and conventional build location.
        "compile_database",
    }
)
_BUILD_KEYS = frozenset({"python", "make"})
_BUILD_PYTHON_KEYS = frozenset({"entrypoint"})
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
_DOCTOR_KEYS = frozenset({"required_tools"})
_TEST_QUALITY_KEYS = frozenset(
    {
        "enabled",
        "mode",
        "repeat_runs",
        "repeat",
        "timeout",
        "slow_test_threshold",
        "slow_threshold",
        "max_slow_tests",
        "mutation",
    }
)
_MUTATION_KEYS = frozenset({"enabled", "tool", "command"})
_MUTATION_TOOLS = frozenset({"auto", "mutmut", "cosmic-ray", "mutpy"})
_COMMON_ENGINE_KEYS = frozenset({"enabled", "mode", "required"})
_ENGINE_KEYS = {
    "line": _COMMON_ENGINE_KEYS
    | frozenset(
        {
            "warn_limit",
            "fail_limit",
            "gate_dirs",
            "include_dirs",
            "exclude_dirs",
        }
    ),
    "lint": _COMMON_ENGINE_KEYS
    | frozenset(
        {
            "ruff_required",
            "clang_tidy",
            "clang_tidy_checks",
            "clang_tidy_config",
            "clazy",
            "clazy_profile",
            "clazy_checks",
        }
    ),
    "compile_db": _COMMON_ENGINE_KEYS
    | frozenset({"database_required", "required_flags", "forbidden_flags"}),
    "test": _COMMON_ENGINE_KEYS
    | frozenset(
        {
            "min_tem_score",
            "min_branch_cov",
            "min_func_cov",
            "coverage_required",
            "python",
            "quality",
        }
    ),
    "type": _COMMON_ENGINE_KEYS
    | frozenset({"fail_on_error", "warn_on_missing_annotation", "mypy_required", "mypy_profile"}),
    "python_compat": _COMMON_ENGINE_KEYS
    | frozenset(
        {
            "interpreters",
            "required_interpreters",
            "imports",
            "target_version",
            "wheel_globs",
            "wheel_required",
            "wheel_policy",
            "check_entrypoints",
            "check_package_files",
            "max_wheels",
            "max_wheel_members",
            "max_wheel_uncompressed_bytes",
        }
    ),
    "complexity": _COMMON_ENGINE_KEYS
    | frozenset({"warn_cc", "fail_cc", "warn_nesting", "cpp_boundaries"}),
    "sanitize": _COMMON_ENGINE_KEYS,
    "thread_sanitize": _COMMON_ENGINE_KEYS,
    "dead": _COMMON_ENGINE_KEYS
    | frozenset({"cpp_unused", "cpp_linker", "include_generated", "include_vendor"}),
    "dup": _COMMON_ENGINE_KEYS
    | frozenset(
        {
            "warn_pct",
            "fail_pct",
            "min_window",
            "python_semantic",
            "include_generated",
            "include_vendor",
        }
    ),
    "exception": _COMMON_ENGINE_KEYS,
    "cycle": _COMMON_ENGINE_KEYS | frozenset({"max_reported"}),
    "cognitive": _COMMON_ENGINE_KEYS | frozenset({"warn", "fail", "warn_nesting"}),
    "security": _COMMON_ENGINE_KEYS | frozenset({"scan_tests", "secret_name_allowlist"}),
    "resource": _COMMON_ENGINE_KEYS,
    "build": _COMMON_ENGINE_KEYS,
    "binary_compat": _COMMON_ENGINE_KEYS
    | frozenset(
        {
            "artifacts",
            "expected_class",
            "expected_machine",
            "max_glibc",
            "max_glibcxx",
            "max_cxxabi",
            "forbid_absolute_rpath",
            "forbidden_needed",
            "allowed_needed",
            "forbid_build_paths",
            "allow_non_elf",
            "max_artifacts",
        }
    ),
    "integration": _COMMON_ENGINE_KEYS
    | frozenset({"max_cases", "max_output_bytes", "python_targets", "cases"}),
}


def resolve_project_path(base: Path, value: str) -> Path:
    """Resolve a project-relative setting and require it to stay in ``base``."""

    from ici.core.path_utils import resolve_project_path as _core_resolve

    try:
        return _core_resolve(base, value)
    except ValueError as err:
        raise ConfigError(str(err)) from err


def _error(path: str, message: str) -> ConfigError:
    return ConfigError(f"{path} {message}" if path else message)


def _reject_unknown(table: dict[str, Any], allowed: set[str] | frozenset[str], path: str) -> None:
    for key in table:
        if key not in allowed:
            raise _error(f"{path}.{key}" if path else str(key), "is an unknown configuration key")


def _require_bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise _error(path, "must be a boolean")


def _require_string(value: Any, path: str, *, non_empty: bool = False) -> None:
    if not isinstance(value, str) or (non_empty and not value.strip()):
        suffix = " non-empty" if non_empty else ""
        raise _error(path, f"must be a{suffix} string")


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _error(path, "must be an integer")
    if minimum is not None and value < minimum:
        raise _error(path, f"must be greater than or equal to {minimum}")


def _require_number(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(path, "must be a number")
    try:
        numeric_value = float(value)
    except (OverflowError, TypeError, ValueError) as err:
        raise _error(path, "must be a finite number") from err
    if not math.isfinite(numeric_value):
        raise _error(path, "must be finite")
    if minimum is not None and numeric_value < minimum:
        raise _error(path, f"must be greater than or equal to {minimum:g}")
    if maximum is not None and numeric_value > maximum:
        raise _error(path, f"must be less than or equal to {maximum:g}")


def _require_string_list(value: Any, path: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise _error(path, "must be a list of non-empty strings")


def _validate_mode(table: dict[str, Any], path: str) -> None:
    if "mode" not in table:
        return
    mode = table["mode"]
    _require_string(mode, f"{path}.mode", non_empty=True)
    if mode not in MODES:
        allowed = ", ".join(sorted(MODES))
        raise _error(f"{path}.mode", f"must be one of: {allowed}")


def _validate_common_engine(table: dict[str, Any], path: str) -> None:
    if "enabled" in table:
        _require_bool(table["enabled"], f"{path}.enabled")
    if "required" in table:
        _require_bool(table["required"], f"{path}.required")
    _validate_mode(table, path)


def _validate_line(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "warn_limit" in table:
        _require_int(table["warn_limit"], f"{path}.warn_limit", minimum=1)
    if "fail_limit" in table:
        _require_int(table["fail_limit"], f"{path}.fail_limit", minimum=1)
    if "gate_dirs" in table:
        _require_string_list(table["gate_dirs"], f"{path}.gate_dirs")
    if "include_dirs" in table:
        _require_string_list(table["include_dirs"], f"{path}.include_dirs")
    if "exclude_dirs" in table:
        _require_string_list(table["exclude_dirs"], f"{path}.exclude_dirs")

    warn_limit = table.get("warn_limit", 500)
    fail_limit = table.get("fail_limit", 1000)
    if fail_limit < warn_limit:
        raise _error(
            f"{path}.warn_limit",
            "must be less than or equal to engines.line.fail_limit",
        )


def _validate_test(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "coverage_required" in table:
        _require_bool(table["coverage_required"], f"{path}.coverage_required")
    if "python" in table:
        _require_string(table["python"], f"{path}.python", non_empty=True)
    if "min_tem_score" in table:
        _require_number(table["min_tem_score"], f"{path}.min_tem_score", minimum=0, maximum=5)
    if "min_branch_cov" in table:
        _require_number(table["min_branch_cov"], f"{path}.min_branch_cov", minimum=0, maximum=100)
    if "min_func_cov" in table:
        _require_number(table["min_func_cov"], f"{path}.min_func_cov", minimum=0, maximum=100)
    if "quality" in table:
        _validate_test_quality(table["quality"], f"{path}.quality")


def _validate_test_quality(table: Any, path: str) -> None:
    """Validate bounded deep-profile test-quality observations."""

    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _TEST_QUALITY_KEYS, path)
    if "enabled" in table:
        _require_bool(table["enabled"], f"{path}.enabled")
    if "mode" in table:
        mode_path = f"{path}.mode"
        _require_string(table["mode"], mode_path, non_empty=True)
        if table["mode"] not in {"report", "warn"}:
            raise _error(mode_path, "must be one of: report, warn")
    for key in ("repeat_runs", "repeat"):
        if key in table:
            _require_int(table[key], f"{path}.{key}", minimum=1)
            if table[key] > 3:
                raise _error(f"{path}.{key}", "must be less than or equal to 3 total runs")
    if "repeat_runs" in table and "repeat" in table:
        raise _error(path, "must define only one of repeat_runs or repeat")
    if "timeout" in table:
        _require_number(table["timeout"], f"{path}.timeout", minimum=0.1, maximum=3600)
    for key in ("slow_test_threshold", "slow_threshold"):
        if key in table:
            _require_number(table[key], f"{path}.{key}", minimum=0, maximum=86400)
    if "slow_test_threshold" in table and "slow_threshold" in table:
        raise _error(path, "must define only one of slow_test_threshold or slow_threshold")
    if "max_slow_tests" in table:
        _require_int(table["max_slow_tests"], f"{path}.max_slow_tests", minimum=1)
        if table["max_slow_tests"] > 1000:
            raise _error(f"{path}.max_slow_tests", "must be less than or equal to 1000")
    if "mutation" not in table:
        return

    mutation = table["mutation"]
    if isinstance(mutation, bool):
        return
    mutation_path = f"{path}.mutation"
    if not isinstance(mutation, dict):
        raise _error(mutation_path, "must be a boolean or table")
    _reject_unknown(mutation, _MUTATION_KEYS, mutation_path)
    if "enabled" in mutation:
        _require_bool(mutation["enabled"], f"{mutation_path}.enabled")
    if "tool" in mutation:
        tool_path = f"{mutation_path}.tool"
        _require_string(mutation["tool"], tool_path, non_empty=True)
        if mutation["tool"] not in _MUTATION_TOOLS:
            allowed = ", ".join(sorted(_MUTATION_TOOLS))
            raise _error(tool_path, f"must be one of: {allowed}")
    if "command" in mutation:
        _validate_bounded_argv(mutation["command"], f"{mutation_path}.command")


def _validate_bounded_argv(value: Any, path: str) -> None:
    """Validate a shell-free probe argv with a small deterministic budget."""

    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise _error(path, "must be a list of 1 to 32 non-empty strings")
    shell_names = {"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"}
    first_name = Path(str(value[0])).name.casefold() if value else ""
    if first_name in shell_names:
        raise _error(path, "must invoke a tool directly without a shell wrapper")
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        if len(item) > 1024 or any(ord(character) < 32 for character in item):
            raise _error(item_path, "must be a safe string of at most 1024 characters")
    if any(item in {"-c", "/c", "-Command", "-command"} for item in value[1:]):
        raise _error(path, "must invoke a tool directly without a shell wrapper")


def _validate_type(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key in ("fail_on_error", "warn_on_missing_annotation", "mypy_required"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")
    if "mypy_profile" in table:
        profile_path = f"{path}.mypy_profile"
        _require_string(table["mypy_profile"], profile_path, non_empty=True)
        if table["mypy_profile"] not in MYPY_PROFILES:
            allowed = ", ".join(sorted(MYPY_PROFILES))
            raise _error(profile_path, f"must be one of: {allowed}")


def _validate_lint(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "ruff_required" in table:
        _require_bool(table["ruff_required"], f"{path}.ruff_required")
    if "clang_tidy" in table:
        clang_tidy_path = f"{path}.clang_tidy"
        _require_string(table["clang_tidy"], clang_tidy_path, non_empty=True)
        if table["clang_tidy"] not in CLANG_TIDY_MODES:
            allowed = ", ".join(sorted(CLANG_TIDY_MODES))
            raise _error(clang_tidy_path, f"must be one of: {allowed}")
    if "clang_tidy_checks" in table:
        _validate_clang_tidy_checks(table["clang_tidy_checks"], f"{path}.clang_tidy_checks")
    if "clang_tidy_config" in table:
        clang_tidy_config_path = f"{path}.clang_tidy_config"
        _require_string(table["clang_tidy_config"], clang_tidy_config_path, non_empty=True)
        if len(table["clang_tidy_config"]) > _MAX_CLANG_TIDY_CONFIG_LENGTH:
            raise _error(
                clang_tidy_config_path,
                f"must be at most {_MAX_CLANG_TIDY_CONFIG_LENGTH} characters",
            )
    if "clazy" in table:
        clazy_path = f"{path}.clazy"
        _require_string(table["clazy"], clazy_path, non_empty=True)
        if table["clazy"] not in CLAZY_MODES:
            allowed = ", ".join(sorted(CLAZY_MODES))
            raise _error(clazy_path, f"must be one of: {allowed}")
    if "clazy_profile" in table:
        profile_path = f"{path}.clazy_profile"
        _require_string(table["clazy_profile"], profile_path, non_empty=True)
        if table["clazy_profile"] not in CLAZY_PROFILES:
            allowed = ", ".join(sorted(CLAZY_PROFILES))
            raise _error(profile_path, f"must be one of: {allowed}")
    if "clazy_checks" in table:
        _validate_clazy_checks(table["clazy_checks"], f"{path}.clazy_checks")


def _validate_clang_tidy_checks(value: Any, path: str) -> None:
    _validate_tool_checks(value, path)


def _validate_clazy_checks(value: Any, path: str) -> None:
    """Validate explicit Clazy check names before an adapter builds argv."""

    _validate_tool_checks(value, path)


def _validate_tool_checks(value: Any, path: str) -> None:
    if not isinstance(value, list):
        raise _error(path, "must be a list of 1 to 128 unique non-empty strings")
    if not 1 <= len(value) <= _MAX_TOOL_CHECKS:
        raise _error(
            path,
            f"must contain between 1 and {_MAX_TOOL_CHECKS} items",
        )

    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        if len(item) > _MAX_TOOL_CHECK_LENGTH:
            raise _error(
                item_path,
                f"must be at most {_MAX_TOOL_CHECK_LENGTH} characters",
            )
        if item in seen:
            raise _error(item_path, "must be unique")
        if any(character not in _TOOL_CHECK_CHARS for character in item):
            raise _error(item_path, "contains unsafe characters")
        seen.add(item)

    if len(",".join(value)) > _MAX_TOOL_CHECKS_JOINED_LENGTH:
        raise _error(
            path,
            f"joined length must be at most {_MAX_TOOL_CHECKS_JOINED_LENGTH} characters",
        )


def _validate_complexity(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key in ("warn_cc", "fail_cc", "warn_nesting"):
        if key in table:
            _require_int(table[key], f"{path}.{key}", minimum=1)
    warn_cc = table.get("warn_cc", 15)
    fail_cc = table.get("fail_cc", 25)
    if fail_cc < warn_cc:
        raise _error(f"{path}.warn_cc", "must be less than or equal to engines.complexity.fail_cc")
    if "cpp_boundaries" in table:
        boundary_path = f"{path}.cpp_boundaries"
        _require_string(table["cpp_boundaries"], boundary_path, non_empty=True)
        if table["cpp_boundaries"] not in {"auto", "required", "off"}:
            raise _error(boundary_path, "must be one of: auto, off, required")


def _validate_dup(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    _validate_analysis_includes(table, path)
    for key in ("warn_pct", "fail_pct"):
        if key in table:
            _require_number(table[key], f"{path}.{key}", minimum=0, maximum=100)
    if "min_window" in table:
        _require_int(table["min_window"], f"{path}.min_window", minimum=1)
    if "python_semantic" in table:
        semantic_path = f"{path}.python_semantic"
        _require_string(table["python_semantic"], semantic_path, non_empty=True)
        if table["python_semantic"] not in {"auto", "required", "off"}:
            raise _error(semantic_path, "must be one of: auto, off, required")
    warn_pct = table.get("warn_pct", 5.0)
    fail_pct = table.get("fail_pct", 15.0)
    if fail_pct < warn_pct:
        raise _error(f"{path}.warn_pct", "must be less than or equal to engines.dup.fail_pct")


def _validate_analysis_includes(table: dict[str, Any], path: str) -> None:
    for key in ("include_generated", "include_vendor"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")


def _validate_dead(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    _validate_analysis_includes(table, path)
    for key in ("cpp_unused", "cpp_linker"):
        if key not in table:
            continue
        policy_path = f"{path}.{key}"
        _require_string(table[key], policy_path, non_empty=True)
        if table[key] not in {"auto", "required", "off"}:
            raise _error(policy_path, "must be one of: auto, off, required")


def _validate_relative_setting(value: Any, path: str, *, allow_root: bool) -> None:
    """Validate a project-relative POSIX path before it is resolved."""

    if not isinstance(value, str) or not value or len(value) > 256:
        raise _error(path, "must be a non-empty path of at most 256 characters")
    if any(ord(character) < 32 for character in value):
        raise _error(path, "must not contain control characters")
    pure = PurePosixPath(value)
    if (
        "\\" in value
        or pure.is_absolute()
        or PureWindowsPath(value).drive
        or ".." in pure.parts
        or (not allow_root and pure == PurePosixPath("."))
    ):
        suffix = " or the project root" if not allow_root else ""
        raise _error(path, f"must be a contained POSIX path{suffix}")


def _validate_direct_argv(
    value: Any, path: str, *, allow_empty: bool, allow_placeholders: bool = False
) -> None:
    """Validate bounded argv values used by adapters and process contracts."""

    if not isinstance(value, list) or len(value) > _MAX_MAKE_ARGV:
        raise _error(path, f"must be a list of at most {_MAX_MAKE_ARGV} strings")
    if not allow_empty and not value:
        raise _error(path, "must contain a direct command argv")
    shell_names = {"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"}
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        if len(item) > _MAX_MAKE_ARG_LENGTH or any(ord(character) < 32 for character in item):
            raise _error(
                item_path,
                f"must be a safe string of at most {_MAX_MAKE_ARG_LENGTH} characters",
            )
        if not allow_placeholders and ("$" in item or "`" in item):
            raise _error(item_path, "must not contain shell metacharacters")
        if ("{" in item or "}" in item) and item != "{jobs}" and not allow_placeholders:
            raise _error(item_path, "contains an unknown placeholder")
    if value:
        executable = Path(value[0]).name.casefold()
        if executable in shell_names or any(
            item.casefold() in {"-c", "/c", "-command"} for item in value[1:]
        ):
            raise _error(path, "must invoke a command directly without a command shell")
    if sum(len(item) for item in value) > _MAX_MAKE_ARGV_LENGTH:
        raise _error(path, "exceeds the aggregate character bound")


def _validate_string_list_bounded(
    value: Any,
    path: str,
    *,
    maximum: int,
    item_length: int = 1024,
    allow_empty: bool = True,
    unique: bool = True,
) -> None:
    if not isinstance(value, list) or (not allow_empty and not value) or len(value) > maximum:
        requirement = f"1 to {maximum}" if not allow_empty else f"at most {maximum}"
        raise _error(path, f"must contain {requirement} strings")
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item or len(item) > item_length:
            raise _error(
                item_path, f"must be a non-empty string of at most {item_length} characters"
            )
        if any(ord(character) < 32 for character in item):
            raise _error(item_path, "must not contain control characters")
        if unique and item in seen:
            raise _error(item_path, "must be unique")
        seen.add(item)


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
    for key in (
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
    ):
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
            _require_string(table[key], f"{path}.{key}")
            if len(table[key]) > 128 or any(ord(character) < 32 for character in table[key]):
                raise _error(f"{path}.{key}", "must be a safe string of at most 128 characters")
    for key in ("max_glibc", "max_glibcxx", "max_cxxabi"):
        if key not in table:
            continue
        value = table[key]
        _require_string(value, f"{path}.{key}")
        if value and (
            len(value) > _MAX_BINARY_VERSION_LENGTH or _ABI_VERSION.fullmatch(value) is None
        ):
            raise _error(
                f"{path}.{key}",
                "must be empty or an ABI version such as 2.17",
            )
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
    if argv and _INTEGRATION_PLACEHOLDER.fullmatch(argv[0]) is None:
        raise _error(
            f"{path}.argv[0]",
            "must be a typed Python or artifact placeholder",
        )
    if argv and any("{" in token or "}" in token for token in argv):
        for index, token in enumerate(argv):
            if ("{" in token or "}" in token) and _INTEGRATION_PLACEHOLDER.fullmatch(token) is None:
                raise _error(f"{path}.argv[{index}]", "must use a known whole-token placeholder")
    expected_exit = case.get("expected_exit", 0)
    if type(expected_exit) is not int or not -(2**31) <= expected_exit < 2**31:
        raise _error(f"{path}.expected_exit", "must be a signed 32-bit integer")
    for key in (
        "stdout_contains",
        "stderr_contains",
        "stdout_not_contains",
        "stderr_not_contains",
    ):
        if key in case:
            _validate_integration_text_list(case[key], f"{path}.{key}")
    if "timeout_seconds" in case:
        _require_number(
            case["timeout_seconds"], f"{path}.timeout_seconds", minimum=0.1, maximum=300
        )
    if "inherit_env" in case:
        _validate_string_list_bounded(
            case["inherit_env"], f"{path}.inherit_env", maximum=_MAX_INTEGRATION_ENV
        )
        for index, name in enumerate(case["inherit_env"]):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z", name):
                raise _error(f"{path}.inherit_env[{index}]", "must be a valid environment name")
    if "env" in case:
        env = case["env"]
        if not isinstance(env, dict) or len(env) > _MAX_INTEGRATION_ENV:
            raise _error(
                f"{path}.env", f"must be a table with at most {_MAX_INTEGRATION_ENV} values"
            )
        for name, value in env.items():
            name_path = f"{path}.env.{name}"
            if not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z", name
            ):
                raise _error(name_path, "must be a valid environment name")
            if (
                not isinstance(value, str)
                or len(value) > 4096
                or any(ord(character) < 32 for character in value)
            ):
                raise _error(name_path, "must be a safe string of at most 4096 characters")
    if "output_artifacts" in case:
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
            if "kind" in output:
                _require_string(output["kind"], f"{output_path}.kind", non_empty=True)
                if len(output["kind"]) > 128 or any(
                    ord(character) < 32 for character in output["kind"]
                ):
                    raise _error(
                        f"{output_path}.kind", "must be a safe string of at most 128 characters"
                    )
            if "min_size" in output:
                _require_int(output["min_size"], f"{output_path}.min_size", minimum=0)
                if output["min_size"] > 64 * 1024 * 1024:
                    raise _error(
                        f"{output_path}.min_size", "must be less than or equal to 67108864"
                    )
    if "required" in case:
        _require_bool(case["required"], f"{path}.required")


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
    targets = table.get("python_targets", {})
    if not isinstance(targets, dict) or len(targets) > _MAX_INTEGRATION_ENV:
        raise _error(
            f"{path}.python_targets", f"must be a table with at most {_MAX_INTEGRATION_ENV} values"
        )
    for name, value in targets.items():
        _validate_integration_id(name, f"{path}.python_targets.{name}")
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise _error(
                f"{path}.python_targets.{name}",
                "must be a non-empty executable path of at most 1024 characters",
            )
        if any(ord(character) < 32 for character in value):
            raise _error(f"{path}.python_targets.{name}", "must not contain control characters")
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


def _validate_build(table: Any) -> None:
    path = "build"
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _BUILD_KEYS, path)
    if "python" in table:
        python = table["python"]
        if not isinstance(python, dict):
            raise _error("build.python", "must be a table")
        _reject_unknown(python, _BUILD_PYTHON_KEYS, "build.python")
        if "entrypoint" in python:
            _require_string(python["entrypoint"], "build.python.entrypoint", non_empty=True)
    if "make" in table:
        _validate_build_make(table["make"], "build.make")


def _validate_doctor(table: Any) -> None:
    path = "doctor"
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _DOCTOR_KEYS, path)
    if "required_tools" in table:
        _require_string_list(table["required_tools"], f"{path}.required_tools")


def _validate_security(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "scan_tests" in table:
        _require_bool(table["scan_tests"], f"{path}.scan_tests")
    if "secret_name_allowlist" in table:
        allowlist_path = f"{path}.secret_name_allowlist"
        _require_string_list(table["secret_name_allowlist"], allowlist_path)
        values = table["secret_name_allowlist"]
        if len(values) > 128:
            raise _error(allowlist_path, "must contain at most 128 names")
        normalized: set[str] = set()
        for index, value in enumerate(values):
            item_path = f"{allowlist_path}[{index}]"
            if len(value) > 128:
                raise _error(item_path, "must be at most 128 characters")
            if not value.isidentifier():
                raise _error(item_path, "must be a Python identifier")
            folded = value.casefold()
            if folded in normalized:
                raise _error(item_path, "must be unique ignoring case")
            normalized.add(folded)


def _validate_python_compat(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key, limit in (("interpreters", 32), ("required_interpreters", 32), ("imports", 64)):
        if key not in table:
            continue
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
    interpreters = table.get("interpreters", [])
    required = table.get("required_interpreters", [])
    if any(value not in interpreters for value in required):
        raise _error(f"{path}.required_interpreters", "must be a subset of interpreters")
    if "target_version" in table:
        value = table["target_version"]
        _require_string(value, f"{path}.target_version")
        if value and not re.fullmatch(r"3\.(?:[7-9]|[1-9][0-9])", value):
            raise _error(f"{path}.target_version", "must be empty or a Python 3 minor such as 3.10")
    for key in ("wheel_required", "check_entrypoints", "check_package_files"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")
    if "wheel_policy" in table:
        value = table["wheel_policy"]
        _require_string(value, f"{path}.wheel_policy", non_empty=True)
        if value not in {"allow-native", "pure"}:
            raise _error(f"{path}.wheel_policy", "must be one of: allow-native, pure")
    if "wheel_globs" in table:
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
    for key, maximum in (
        ("max_wheels", 32),
        ("max_wheel_members", 8192),
        ("max_wheel_uncompressed_bytes", 64 * 1024 * 1024),
    ):
        if key in table:
            _require_int(table[key], f"{path}.{key}", minimum=1)
            if table[key] > maximum:
                raise _error(f"{path}.{key}", f"must be less than or equal to {maximum}")


def _validate_compile_db(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "database_required" in table:
        _require_bool(table["database_required"], f"{path}.database_required")
    for key in ("required_flags", "forbidden_flags"):
        if key in table:
            _require_string_list(table[key], f"{path}.{key}")


def _validate_cognitive(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key in ("warn", "fail", "warn_nesting"):
        if key in table:
            _require_int(table[key], f"{path}.{key}", minimum=1)
    # Matches CognitiveEngine's own fallback and DEFAULT_CONFIG's shipped
    # policy (warn=30, fail=60) — kept in sync deliberately.
    w = table.get("warn", 30)
    f = table.get("fail", 60)
    if f < w:
        raise _error(f"{path}.warn", "must be <= fail")


def _validate_cycle(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    if "max_reported" in table:
        _require_int(table["max_reported"], f"{path}.max_reported", minimum=1)


def _validate_engine(name: str, table: Any) -> None:
    path = f"engines.{name}"
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _ENGINE_KEYS[name], path)
    validators = {
        "line": _validate_line,
        "test": _validate_test,
        "type": _validate_type,
        "python_compat": _validate_python_compat,
        "lint": _validate_lint,
        "compile_db": _validate_compile_db,
        "complexity": _validate_complexity,
        "dead": _validate_dead,
        "dup": _validate_dup,
        "cycle": _validate_cycle,
        "cognitive": _validate_cognitive,
        "security": _validate_security,
        "resource": _validate_common_engine,
        "build": _validate_common_engine,
        "binary_compat": _validate_binary_compat,
        "integration": _validate_integration,
    }
    validator = validators.get(name, _validate_common_engine)
    validator(table, path)


def _validate_metadata(table: Any, path: str) -> None:
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    allowed = _ICI_KEYS if path == "ici" else _PROJECT_KEYS
    _reject_unknown(table, allowed, path)
    for key, value in table.items():
        if path == "project" and key in (
            "source_dirs",
            "cpp_pkg_config",
            "cpp_external_build_dirs",
        ):
            _require_string_list(value, f"project.{key}")
        elif key == "type":
            _require_string(value, f"{path}.{key}", non_empty=True)
            if value not in PROJECT_TYPES:
                raise _error(f"{path}.{key}", "must be one of: cpp, hybrid, python")
        elif path == "ici" and key == "profile":
            _require_string(value, "ici.profile", non_empty=True)
            if value not in ANALYSIS_PROFILES:
                raise _error("ici.profile", "must be one of: deep, fast, standard")
        else:
            _require_string(value, f"{path}.{key}", non_empty=True)


def _validate_path_list(value: Any, setting: str, base: Path) -> None:
    """Validate and canonicalize a list of project-relative path settings."""

    if not isinstance(value, list):
        raise _error(setting, "must be a list of non-empty strings")
    for index, item in enumerate(value):
        item_path = f"{setting}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        try:
            resolve_project_path(base, item)
        except ConfigError as err:
            raise ConfigError(f"{item_path}: {err}") from err


def validate_config_paths(config: dict[str, Any], base: Path) -> None:
    """Reject configured paths that resolve outside the project root.

    This function deliberately accepts partial engine configurations so
    standalone engine construction remains safe even when tests or callers
    provide only the relevant engine table instead of a fully merged policy.
    """

    if not isinstance(config, dict):
        raise ConfigError("configuration must be a table")

    project = config.get("project")
    if isinstance(project, dict):
        # cpp_pkg_config holds package names, not paths, so it is not resolved here.
        for key in ("source_dirs", "cpp_external_build_dirs"):
            if key in project:
                _validate_path_list(project[key], f"project.{key}", base)
        compile_database = project.get("compile_database")
        if compile_database is not None:
            if not isinstance(compile_database, str) or not compile_database:
                raise _error("project.compile_database", "must be a non-empty string")
            if os.name != "nt" and (
                "\\" in compile_database or bool(PureWindowsPath(compile_database).drive)
            ):
                raise _error(
                    "project.compile_database",
                    "must use native project path syntax",
                )
            try:
                resolve_project_path(base, compile_database)
            except ConfigError as err:
                raise ConfigError(f"project.compile_database: {err}") from err

    build = config.get("build")
    if isinstance(build, dict):
        make = build.get("make")
        if isinstance(make, dict):
            canonical_base = base.resolve(strict=False)
            resolved_make_paths: dict[str, Path] = {}
            for key in ("workdir", "shadow_dir"):
                if key not in make:
                    continue
                setting = f"build.make.{key}"
                try:
                    resolved = resolve_project_path(base, make[key])
                except ConfigError as err:
                    raise ConfigError(f"{setting}: {err}") from err
                if key == "shadow_dir" and resolved == canonical_base:
                    raise ConfigError(f"{setting} must not be the project root")
                resolved_make_paths[key] = resolved
            if (
                make.get("out_of_tree", "allow") == "required"
                and resolved_make_paths.get("workdir", canonical_base) == canonical_base
            ):
                raise ConfigError("build.make.out_of_tree=required needs a non-root workdir")

    engines = config.get("engines")
    if not isinstance(engines, dict):
        return
    line = engines.get("line")
    if isinstance(line, dict):
        for key in ("gate_dirs", "include_dirs", "exclude_dirs"):
            if key in line:
                _validate_path_list(line[key], f"engines.line.{key}", base)
    lint = engines.get("lint")
    if isinstance(lint, dict) and "clang_tidy_config" in lint:
        value = lint["clang_tidy_config"]
        if not isinstance(value, str) or not value:
            raise _error("engines.lint.clang_tidy_config", "must be a non-empty string")
        try:
            resolve_project_path(base, value)
        except ConfigError as err:
            raise ConfigError(f"engines.lint.clang_tidy_config: {err}") from err


def validate_config(config: dict[str, Any]) -> None:
    """Validate an effective configuration and raise :class:`ConfigError` on failure."""

    if not isinstance(config, dict):
        raise ConfigError("configuration must be a table")
    _reject_unknown(config, _TOP_LEVEL_KEYS, "")

    for key in ("ici", "project"):
        if key in config:
            _validate_metadata(config[key], key)
    for key in ("name", "version"):
        if key in config:
            _require_string(config[key], key, non_empty=True)
    if "type" in config:
        _require_string(config["type"], "type", non_empty=True)
        if config["type"] not in PROJECT_TYPES:
            raise _error("type", "must be one of: cpp, hybrid, python")

    if "build" in config:
        _validate_build(config["build"])

    if "doctor" in config:
        _validate_doctor(config["doctor"])

    engines = config.get("engines")
    if not isinstance(engines, dict):
        raise ConfigError("engines must be a table")
    _reject_unknown(engines, frozenset(_ENGINE_KEYS), "engines")
    for name, table in engines.items():
        _validate_engine(name, table)
