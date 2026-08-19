"""Validation for the layered ici configuration.

The configuration is intentionally validated without a third-party schema
library.  Keeping the schema in Python makes the zipapp self-contained and
lets us report the exact dotted key that caused a configuration error.
"""

import math
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when an ici configuration is malformed or violates policy."""


MODES = frozenset({"pass_warn_fail", "pass_fail", "pass_warn"})
PROJECT_TYPES = frozenset({"python", "cpp", "hybrid"})

_TOP_LEVEL_KEYS = frozenset({"ici", "project", "engines", "name", "type", "version"})
_ICI_KEYS = frozenset({"version", "policy_name"})
_PROJECT_KEYS = frozenset({"source_dirs", "name", "type", "version"})
_ENGINE_KEYS = {
    "line": frozenset(
        {
            "enabled",
            "mode",
            "warn_limit",
            "fail_limit",
            "gate_dirs",
            "include_dirs",
            "exclude_dirs",
        }
    ),
    "lint": frozenset({"enabled", "mode"}),
    "test": frozenset(
        {
            "enabled",
            "mode",
            "min_tem_score",
            "min_branch_cov",
            "min_func_cov",
            "coverage_required",
            "python",
        }
    ),
    "type": frozenset({"enabled", "mode", "fail_on_error", "warn_on_missing_annotation"}),
    "complexity": frozenset({"enabled", "mode", "warn_cc", "fail_cc", "warn_nesting"}),
    "sanitize": frozenset({"enabled", "mode"}),
    "dead": frozenset({"enabled", "mode"}),
    "dup": frozenset({"enabled", "mode", "warn_pct", "fail_pct", "min_window"}),
    "exception": frozenset({"enabled", "mode"}),
}


def resolve_project_path(base: Path, value: str) -> Path:
    """Resolve a project-relative setting and require it to stay in ``base``.

    The canonical path check follows symlinks, so lexical checks cannot be
    bypassed with ``..`` segments or a link into another tree.  The helper is
    kept separate from the schema walk so project discovery can reuse the
    same boundary rule without duplicating it.
    """

    try:
        project_root = Path(base).resolve(strict=False)
        candidate = (project_root / value).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as err:
        raise ConfigError(f"could not resolve project path {value!r}: {err}") from err

    try:
        candidate.relative_to(project_root)
    except ValueError as err:
        raise ConfigError(f"path is outside project root: {value}") from err
    return candidate


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
    for key in ("fail_on_error", "warn_on_missing_annotation"):
        if key in table:
            _require_bool(table[key], f"{path}.{key}")


def _validate_complexity(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key in ("warn_cc", "fail_cc", "warn_nesting"):
        if key in table:
            _require_int(table[key], f"{path}.{key}", minimum=1)
    warn_cc = table.get("warn_cc", 15)
    fail_cc = table.get("fail_cc", 25)
    if fail_cc < warn_cc:
        raise _error(f"{path}.warn_cc", "must be less than or equal to engines.complexity.fail_cc")


def _validate_dup(table: dict[str, Any], path: str) -> None:
    _validate_common_engine(table, path)
    for key in ("warn_pct", "fail_pct"):
        if key in table:
            _require_number(table[key], f"{path}.{key}", minimum=0, maximum=100)
    if "min_window" in table:
        _require_int(table["min_window"], f"{path}.min_window", minimum=1)
    warn_pct = table.get("warn_pct", 5.0)
    fail_pct = table.get("fail_pct", 15.0)
    if fail_pct < warn_pct:
        raise _error(f"{path}.warn_pct", "must be less than or equal to engines.dup.fail_pct")


def _validate_engine(name: str, table: Any) -> None:
    path = f"engines.{name}"
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    _reject_unknown(table, _ENGINE_KEYS[name], path)
    if name == "line":
        _validate_line(table, path)
    elif name == "test":
        _validate_test(table, path)
    elif name == "type":
        _validate_type(table, path)
    elif name == "complexity":
        _validate_complexity(table, path)
    elif name == "dup":
        _validate_dup(table, path)
    else:
        _validate_common_engine(table, path)


def _validate_metadata(table: Any, path: str) -> None:
    if not isinstance(table, dict):
        raise _error(path, "must be a table")
    allowed = _ICI_KEYS if path == "ici" else _PROJECT_KEYS
    _reject_unknown(table, allowed, path)
    for key, value in table.items():
        if path == "project" and key == "source_dirs":
            _require_string_list(value, "project.source_dirs")
        elif key == "type":
            _require_string(value, f"{path}.{key}", non_empty=True)
            if value not in PROJECT_TYPES:
                raise _error(f"{path}.{key}", "must be one of: cpp, hybrid, python")
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
    if isinstance(project, dict) and "source_dirs" in project:
        _validate_path_list(project["source_dirs"], "project.source_dirs", base)

    engines = config.get("engines")
    if not isinstance(engines, dict):
        return
    line = engines.get("line")
    if not isinstance(line, dict):
        return
    for key in ("gate_dirs", "include_dirs", "exclude_dirs"):
        if key in line:
            _validate_path_list(line[key], f"engines.line.{key}", base)


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

    engines = config.get("engines")
    if not isinstance(engines, dict):
        raise ConfigError("engines must be a table")
    _reject_unknown(engines, frozenset(_ENGINE_KEYS), "engines")
    for name, table in engines.items():
        _validate_engine(name, table)
