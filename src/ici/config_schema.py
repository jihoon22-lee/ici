"""Validation for the layered ici configuration.

The configuration is intentionally validated without a third-party schema
library.  Keeping the schema in Python makes the zipapp self-contained and
lets us report the exact dotted key that caused a configuration error.
"""

import math
import os
from pathlib import Path, PureWindowsPath
from typing import Any


class ConfigError(ValueError):
    """Raised when an ici configuration is malformed or violates policy."""


MODES = frozenset({"pass_warn_fail", "pass_fail", "pass_warn"})
PROJECT_TYPES = frozenset({"python", "cpp", "hybrid"})
ANALYSIS_PROFILES = frozenset({"fast", "standard", "deep"})
CLANG_TIDY_MODES = frozenset({"auto", "required", "off"})
CLAZY_MODES = frozenset({"auto", "required", "off"})
CLAZY_PROFILES = frozenset({"level0", "level1"})
_TOOL_CHECK_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.*-")
_MAX_TOOL_CHECKS = 128
_MAX_TOOL_CHECK_LENGTH = 128
_MAX_TOOL_CHECKS_JOINED_LENGTH = 8192
_MAX_CLANG_TIDY_CONFIG_LENGTH = 4096

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
_BUILD_KEYS = frozenset({"python"})
_BUILD_PYTHON_KEYS = frozenset({"entrypoint"})
_DOCTOR_KEYS = frozenset({"required_tools"})
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
        }
    ),
    "type": _COMMON_ENGINE_KEYS
    | frozenset({"fail_on_error", "warn_on_missing_annotation", "mypy_required"}),
    "complexity": _COMMON_ENGINE_KEYS
    | frozenset({"warn_cc", "fail_cc", "warn_nesting", "cpp_boundaries"}),
    "sanitize": _COMMON_ENGINE_KEYS,
    "dead": _COMMON_ENGINE_KEYS | frozenset({"cpp_unused", "include_generated", "include_vendor"}),
    "dup": _COMMON_ENGINE_KEYS
    | frozenset({"warn_pct", "fail_pct", "min_window", "include_generated", "include_vendor"}),
    "exception": _COMMON_ENGINE_KEYS,
    "cycle": _COMMON_ENGINE_KEYS | frozenset({"max_reported"}),
    "cognitive": _COMMON_ENGINE_KEYS | frozenset({"warn", "fail", "warn_nesting"}),
    "security": _COMMON_ENGINE_KEYS | frozenset({"scan_tests"}),
    "resource": _COMMON_ENGINE_KEYS,
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


def _validate_type(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key in ("fail_on_error", "warn_on_missing_annotation", "mypy_required"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")


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
    if "cpp_unused" in table:
        unused_path = f"{path}.cpp_unused"
        _require_string(table["cpp_unused"], unused_path, non_empty=True)
        if table["cpp_unused"] not in {"auto", "required", "off"}:
            raise _error(unused_path, "must be one of: auto, off, required")


def _validate_build(table: Any) -> None:
    path = "build"
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _BUILD_KEYS, path)
    if "python" not in table:
        return
    python = table["python"]
    if not isinstance(python, dict):
        raise _error("build.python", "must be a table")
    _reject_unknown(python, _BUILD_PYTHON_KEYS, "build.python")
    if "entrypoint" in python:
        _require_string(python["entrypoint"], "build.python.entrypoint", non_empty=True)


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
        "lint": _validate_lint,
        "compile_db": _validate_compile_db,
        "complexity": _validate_complexity,
        "dead": _validate_dead,
        "dup": _validate_dup,
        "cycle": _validate_cycle,
        "cognitive": _validate_cognitive,
        "security": _validate_security,
        "resource": _validate_common_engine,
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
