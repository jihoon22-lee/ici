"""Shared, dependency-free primitives for validating ici configuration."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ici.core.path_utils import resolve_project_path as _core_resolve_project_path

_MAX_DIRECT_ARGV = 64
_MAX_DIRECT_ARG_LENGTH = 1024
_MAX_DIRECT_ARGV_LENGTH = 32 * 1024
_MAX_BOUNDED_ARGV = 32
_MAX_BOUNDED_ARG_LENGTH = 1024
MODES = frozenset({"pass_warn_fail", "pass_fail", "pass_warn"})


class ConfigError(ValueError):
    """Raised when an ici configuration is malformed or violates policy."""


def resolve_project_path(base: Path, value: str) -> Path:
    """Resolve a project-relative setting and require it to stay in ``base``."""

    try:
        return _core_resolve_project_path(base, value)
    except ValueError as err:
        raise ConfigError(str(err)) from err


def _error(path: str, message: str) -> ConfigError:
    return ConfigError(f"{path} {message}" if path else message)


def _reject_unknown(table: dict[str, Any], allowed: set[str] | frozenset[str], path: str) -> None:
    for key in table:
        if key not in allowed:
            key_path = f"{path}.{key}" if path else str(key)
            raise _error(key_path, "is an unknown configuration key")


def _require_bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise _error(path, "must be a boolean")


def _require_string(value: Any, path: str, *, non_empty: bool = False) -> None:
    if not isinstance(value, str) or (non_empty and not value.strip()):
        suffix = " non-empty" if non_empty else ""
        raise _error(path, f"must be a{suffix} string")


def _validate_common_engine(table: dict[str, Any], path: str) -> None:
    if "enabled" in table:
        _require_bool(table["enabled"], f"{path}.enabled")
    if "required" in table:
        _require_bool(table["required"], f"{path}.required")
    if "mode" in table:
        _require_string(table["mode"], f"{path}.mode", non_empty=True)
        if table["mode"] not in MODES:
            allowed = ", ".join(sorted(MODES))
            raise _error(f"{path}.mode", f"must be one of: {allowed}")


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
    import math

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


def _validate_bounded_argv(value: Any, path: str) -> None:
    """Validate a shell-free probe argv with a small deterministic budget."""

    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_BOUNDED_ARGV:
        raise _error(path, "must be a list of 1 to 32 non-empty strings")
    shell_names = {"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"}
    first_name = Path(str(value[0])).name.casefold() if value else ""
    if first_name in shell_names:
        raise _error(path, "must invoke a tool directly without a shell wrapper")
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        if len(item) > _MAX_BOUNDED_ARG_LENGTH or any(ord(character) < 32 for character in item):
            raise _error(item_path, "must be a safe string of at most 1024 characters")
    if any(item in {"-c", "/c", "-Command", "-command"} for item in value[1:]):
        raise _error(path, "must invoke a tool directly without a shell wrapper")


def _validate_direct_argv(
    value: Any, path: str, *, allow_empty: bool, allow_placeholders: bool = False
) -> None:
    """Validate bounded argv values used by adapters and process contracts."""

    if not isinstance(value, list) or len(value) > _MAX_DIRECT_ARGV:
        raise _error(path, f"must be a list of at most {_MAX_DIRECT_ARGV} strings")
    if not allow_empty and not value:
        raise _error(path, "must contain a direct command argv")
    shell_names = {"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"}
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or not item:
            raise _error(item_path, "must be a non-empty string")
        if len(item) > _MAX_DIRECT_ARG_LENGTH or any(ord(character) < 32 for character in item):
            raise _error(
                item_path,
                f"must be a safe string of at most {_MAX_DIRECT_ARG_LENGTH} characters",
            )
        if not allow_placeholders and ("$" in item or "`" in item):
            raise _error(item_path, "must not contain shell metacharacters")
        if ("{" in item or "}" in item) and item != "{jobs}" and not allow_placeholders:
            raise _error(item_path, "contains an unknown placeholder")
    if value:
        executable = Path(value[0]).name.casefold()
        shell_args = {"-c", "/c", "-command"}
        if executable in shell_names or any(item.casefold() in shell_args for item in value[1:]):
            raise _error(path, "must invoke a command directly without a command shell")
    if sum(len(item) for item in value) > _MAX_DIRECT_ARGV_LENGTH:
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
    """Validate a bounded list of safe strings."""

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
