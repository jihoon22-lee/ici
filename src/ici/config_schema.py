"""Validation for the layered ici configuration.

The configuration is intentionally validated without a third-party schema
library.  Keeping the schema in Python makes the zipapp self-contained and
lets us report the exact dotted key that caused a configuration error.
"""

from typing import Any

from ici._analysis_config import (
    _validate_binary_compat,
    _validate_build_make,
    _validate_integration,
    _validate_python_compat,
)
from ici._config_paths import validate_config_paths
from ici._config_validation import (
    MODES,
    ConfigError,
    _error,
    _reject_unknown,
    _require_bool,
    _require_int,
    _require_number,
    _require_string,
    _require_string_list,
    _validate_bounded_argv,
    _validate_common_engine,
    resolve_project_path,
)

__all__ = [
    "ANALYSIS_PROFILES",
    "CLANG_TIDY_MODES",
    "CLAZY_MODES",
    "CLAZY_PROFILES",
    "MODES",
    "MYPY_PROFILES",
    "PROJECT_TYPES",
    "ConfigError",
    "resolve_project_path",
    "validate_config",
    "validate_config_paths",
]

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
            "min_line_cov",
            "min_file_cov",
            "min_file_statements",
            "min_branch_cov",
            "min_func_cov",
            "min_changed_line_cov",
            "changed_lines",
            "max_coverage_regression",
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
    "cognitive": _COMMON_ENGINE_KEYS
    | frozenset({"warn", "fail", "warn_nesting", "cpp_boundaries"}),
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
    if "min_line_cov" in table:
        _require_number(table["min_line_cov"], f"{path}.min_line_cov", minimum=0, maximum=100)
    if "min_file_cov" in table:
        _require_number(table["min_file_cov"], f"{path}.min_file_cov", minimum=0, maximum=100)
    if "min_file_statements" in table:
        _require_int(table["min_file_statements"], f"{path}.min_file_statements", minimum=1)
    if "min_branch_cov" in table:
        _require_number(table["min_branch_cov"], f"{path}.min_branch_cov", minimum=0, maximum=100)
    if "min_func_cov" in table:
        _require_number(table["min_func_cov"], f"{path}.min_func_cov", minimum=0, maximum=100)
    if "min_changed_line_cov" in table:
        _require_number(
            table["min_changed_line_cov"],
            f"{path}.min_changed_line_cov",
            minimum=0,
            maximum=100,
        )
    if "changed_lines" in table:
        _require_string_list(table["changed_lines"], f"{path}.changed_lines")
    if "max_coverage_regression" in table:
        _require_number(
            table["max_coverage_regression"],
            f"{path}.max_coverage_regression",
            minimum=0,
            maximum=100,
        )
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
    if "cpp_boundaries" in table:
        boundary_path = f"{path}.cpp_boundaries"
        _require_string(table["cpp_boundaries"], boundary_path, non_empty=True)
        if table["cpp_boundaries"] not in {"auto", "required", "off"}:
            raise _error(boundary_path, "must be one of: auto, off, required")


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
