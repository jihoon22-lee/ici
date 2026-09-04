"""Typed, bounded configuration for shell-free integration cases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

MAX_CASES = 32
MAX_ARGV = 64
MAX_ASSERTIONS = 32
MAX_ENV = 32
_EXECUTABLE_PLACEHOLDER_RE = re.compile(r"^\{(?:python|artifact):[^{}]+\}$")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


class IntegrationConfigError(ValueError):
    """Raised before any case runs when its transport contract is unsafe."""


@dataclass(frozen=True)
class OutputArtifactAssertion:
    path: str
    kind: str = "other"
    min_size: int = 1


@dataclass(frozen=True)
class IntegrationCase:
    name: str
    argv: tuple[str, ...]
    expected_exit: int = 0
    stdout_contains: tuple[str, ...] = ()
    stderr_contains: tuple[str, ...] = ()
    stdout_not_contains: tuple[str, ...] = ()
    stderr_not_contains: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    inherit_env: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    output_artifacts: tuple[OutputArtifactAssertion, ...] = ()
    required: bool = True


def _strings(value: Any, setting: str, maximum: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > maximum:
        raise IntegrationConfigError(f"{setting} must be a list with at most {maximum} values")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item or len(item) > 1024:
            raise IntegrationConfigError(f"{setting}[{index}] must be a bounded non-empty string")
        if any(ord(character) < 32 for character in item):
            raise IntegrationConfigError(f"{setting}[{index}] contains control characters")
        result.append(item)
    return tuple(result)


def _env_names(value: Any, setting: str) -> tuple[str, ...]:
    names = _strings(value, setting, MAX_ENV)
    if any(_ENV_NAME_RE.fullmatch(name) is None for name in names):
        raise IntegrationConfigError(f"{setting} contains an invalid environment name")
    return names


def _output_assertions(value: Any, setting: str) -> tuple[OutputArtifactAssertion, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_ASSERTIONS:
        raise IntegrationConfigError(f"{setting} must contain at most {MAX_ASSERTIONS} tables")
    result: list[OutputArtifactAssertion] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) - {"path", "kind", "min_size"}:
            raise IntegrationConfigError(f"{setting}[{index}] has unknown or invalid keys")
        path = item.get("path")
        if not isinstance(path, str) or not path or "\\" in path or PureWindowsPath(path).drive:
            raise IntegrationConfigError(f"{setting}[{index}].path must be project-relative")
        posix = PurePosixPath(path)
        if posix.is_absolute() or ".." in posix.parts or posix.as_posix() != path:
            raise IntegrationConfigError(f"{setting}[{index}].path must be contained")
        kind = item.get("kind", "other")
        min_size = item.get("min_size", 1)
        if (
            not isinstance(kind, str)
            or not kind
            or len(kind) > 128
            or any(ord(character) < 32 for character in kind)
        ):
            raise IntegrationConfigError(f"{setting}[{index}].kind must be bounded")
        if type(min_size) is not int or not 0 <= min_size <= 64 * 1024 * 1024:
            raise IntegrationConfigError(
                f"{setting}[{index}].min_size must be between 0 and 67108864"
            )
        result.append(OutputArtifactAssertion(path, kind, min_size))
    return tuple(result)


def parse_integration_cases(config: dict[str, Any]) -> tuple[IntegrationCase, ...]:
    raw = config.get("cases", [])
    if not isinstance(raw, list) or len(raw) > MAX_CASES:
        raise IntegrationConfigError(
            f"engines.integration.cases must contain at most {MAX_CASES} tables"
        )
    cases: list[IntegrationCase] = []
    seen: set[str] = set()
    allowed = {
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
    for index, item in enumerate(raw):
        setting = f"engines.integration.cases[{index}]"
        if not isinstance(item, dict) or set(item) - allowed:
            raise IntegrationConfigError(f"{setting} has unknown or invalid keys")
        name = item.get("name")
        if not isinstance(name, str) or not name or len(name) > 128 or name in seen:
            raise IntegrationConfigError(f"{setting}.name must be a unique bounded string")
        seen.add(name)
        argv = _strings(item.get("argv"), f"{setting}.argv", MAX_ARGV)
        if not argv:
            raise IntegrationConfigError(f"{setting}.argv must not be empty")
        if _EXECUTABLE_PLACEHOLDER_RE.fullmatch(argv[0]) is None:
            raise IntegrationConfigError(
                f"{setting}.argv[0] must be a typed Python or artifact placeholder"
            )
        if sum(len(token) for token in argv) > 32 * 1024:
            raise IntegrationConfigError(f"{setting}.argv exceeds the aggregate bound")
        expected_exit = item.get("expected_exit", 0)
        if type(expected_exit) is not int or not -(2**31) <= expected_exit < 2**31:
            raise IntegrationConfigError(f"{setting}.expected_exit must be a 32-bit integer")
        timeout = item.get("timeout_seconds", 30.0)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0.1 <= timeout <= 300
        ):
            raise IntegrationConfigError(f"{setting}.timeout_seconds must be between 0.1 and 300")
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise IntegrationConfigError(f"{setting}.required must be a boolean")
        raw_env = item.get("env", {})
        if not isinstance(raw_env, dict) or len(raw_env) > MAX_ENV:
            raise IntegrationConfigError(f"{setting}.env must contain at most {MAX_ENV} strings")
        env: list[tuple[str, str]] = []
        for key, value in raw_env.items():
            if (
                not isinstance(key, str)
                or _ENV_NAME_RE.fullmatch(key) is None
                or not isinstance(value, str)
                or len(value) > 4096
                or any(ord(char) < 32 for char in key + value)
            ):
                raise IntegrationConfigError(f"{setting}.env contains an unsafe name or value")
            env.append((key, value))
        cases.append(
            IntegrationCase(
                name=name,
                argv=argv,
                expected_exit=expected_exit,
                stdout_contains=_strings(
                    item.get("stdout_contains"), f"{setting}.stdout_contains", MAX_ASSERTIONS
                ),
                stderr_contains=_strings(
                    item.get("stderr_contains"), f"{setting}.stderr_contains", MAX_ASSERTIONS
                ),
                stdout_not_contains=_strings(
                    item.get("stdout_not_contains"),
                    f"{setting}.stdout_not_contains",
                    MAX_ASSERTIONS,
                ),
                stderr_not_contains=_strings(
                    item.get("stderr_not_contains"),
                    f"{setting}.stderr_not_contains",
                    MAX_ASSERTIONS,
                ),
                timeout_seconds=float(timeout),
                inherit_env=_env_names(item.get("inherit_env"), f"{setting}.inherit_env"),
                env=tuple(sorted(env)),
                output_artifacts=_output_assertions(
                    item.get("output_artifacts"), f"{setting}.output_artifacts"
                ),
                required=required,
            )
        )
    return tuple(cases)
