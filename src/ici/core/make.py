"""Validated shell-free command plans for handwritten Make projects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ici.core.context import BuildVariant

MAX_ARGV_TOKENS = 64
MAX_ARGV_TOKEN_CHARS = 1024
MAX_ARGV_CHARS = 32 * 1024
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "fish", "cmd", "powershell", "pwsh"})
_SHELL_FLAGS = frozenset({"-c", "/c", "-command"})
_VARIANT_PREFIX = {
    BuildVariant.RELEASE: "",
    BuildVariant.COVERAGE: "coverage_",
    BuildVariant.SANITIZE: "sanitize_",
    BuildVariant.THREAD_SANITIZE: "thread_sanitize_",
}


class MakeConfigError(ValueError):
    """Raised when a handwritten Make command contract is unsafe or incomplete."""


@dataclass(frozen=True)
class MakePlan:
    descriptor: str
    workdir: Path
    shadow: Path
    configure_argv: tuple[str, ...]
    clean_argv: tuple[str, ...]
    build_argv: tuple[str, ...]
    test_argv: tuple[str, ...]
    jobs: int
    out_of_tree: str


def _contained_path(root: Path, value: Any, setting: str, *, allow_root: bool) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or PureWindowsPath(value).drive:
        raise MakeConfigError(f"{setting} must be a project-relative POSIX path")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise MakeConfigError(f"{setting} must stay within the project root")
    try:
        resolved = (root / candidate).resolve(strict=False)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as err:
        raise MakeConfigError(f"{setting} must stay within the project root") from err
    if not allow_root and resolved == root:
        raise MakeConfigError(f"{setting} must not be the project root")
    return resolved


def _argv(value: Any, setting: str, jobs: int, *, required: bool) -> tuple[str, ...]:
    if value in (None, []):
        if required:
            raise MakeConfigError(f"{setting} must contain a direct command argv")
        return ()
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ARGV_TOKENS:
        raise MakeConfigError(f"{setting} must contain 1 to {MAX_ARGV_TOKENS} tokens")
    result: list[str] = []
    for index, token in enumerate(value):
        if not isinstance(token, str) or not token:
            raise MakeConfigError(f"{setting}[{index}] must be a non-empty string")
        if len(token) > MAX_ARGV_TOKEN_CHARS or any(ord(char) < 32 for char in token):
            raise MakeConfigError(
                f"{setting}[{index}] must be a safe token of at most {MAX_ARGV_TOKEN_CHARS} characters"
            )
        if "$" in token or "`" in token:
            raise MakeConfigError(
                f"{setting}[{index}] contains a shell metacharacter or unknown placeholder"
            )
        if "{jobs}" in token and token != "{jobs}":
            raise MakeConfigError(f"{setting}[{index}] must use {{jobs}} as a whole token")
        if "{" in token or "}" in token:
            if token != "{jobs}":
                raise MakeConfigError(f"{setting}[{index}] contains an unknown placeholder")
            token = str(jobs)
        result.append(token)
    executable = Path(result[0]).name.casefold()
    if executable in _SHELLS or any(token.casefold() in _SHELL_FLAGS for token in result[1:]):
        raise MakeConfigError(f"{setting} must not invoke a command shell")
    if sum(len(token) for token in result) > MAX_ARGV_CHARS:
        raise MakeConfigError(f"{setting} exceeds the aggregate character bound")
    return tuple(result)


def make_enabled(config: dict[str, Any]) -> bool:
    build = config.get("build", {})
    table = build.get("make", {}) if isinstance(build, dict) else {}
    return isinstance(table, dict) and table.get("enabled") is True


def make_plan(root: Path, config: dict[str, Any], variant: BuildVariant) -> MakePlan:
    """Normalize the configured command transport for one build variant."""

    canonical_root = root.resolve(strict=False)
    build = config.get("build", {})
    table = build.get("make", {}) if isinstance(build, dict) else None
    if not isinstance(table, dict) or table.get("enabled") is not True:
        raise MakeConfigError("build.make.enabled must be true for the Make backend")
    jobs = table.get("jobs", 1)
    if type(jobs) is not int or not 1 <= jobs <= 64:
        raise MakeConfigError("build.make.jobs must be an integer from 1 to 64")
    out_of_tree = table.get("out_of_tree", "allow")
    if out_of_tree not in {"allow", "required"}:
        raise MakeConfigError("build.make.out_of_tree must be allow or required")
    workdir = _contained_path(
        canonical_root, table.get("workdir", "."), "build.make.workdir", allow_root=True
    )
    shadow = _contained_path(
        canonical_root,
        table.get("shadow_dir", "build/ici-make"),
        "build.make.shadow_dir",
        allow_root=False,
    )
    if out_of_tree == "required" and workdir == canonical_root:
        raise MakeConfigError("build.make.out_of_tree=required needs a non-root workdir")
    prefix = _VARIANT_PREFIX[variant]
    build_key = f"{prefix}build_argv" if prefix else "build_argv"
    test_key = f"{prefix}test_argv" if prefix else "test_argv"
    build_value = table.get(build_key)
    if prefix and not build_value:
        raise MakeConfigError(f"build.make.{build_key} is required for {variant.value}")
    test_value = (
        table.get(test_key) if prefix and table.get(test_key) else table.get("test_argv", [])
    )
    return MakePlan(
        descriptor=next(
            (
                name
                for name in ("Makefile", "makefile", "GNUmakefile")
                if (canonical_root / name).is_file()
            ),
            "Makefile",
        ),
        workdir=workdir,
        shadow=shadow,
        configure_argv=_argv(
            table.get("configure_argv", []), "build.make.configure_argv", jobs, required=False
        ),
        clean_argv=_argv(
            table.get("clean_argv", []), "build.make.clean_argv", jobs, required=False
        ),
        build_argv=_argv(build_value, f"build.make.{build_key}", jobs, required=True),
        test_argv=_argv(test_value, f"build.make.{test_key}", jobs, required=False),
        jobs=jobs,
        out_of_tree=str(out_of_tree),
    )


def executable_available(argv: tuple[str, ...]) -> str | None:
    """Resolve only the command token, preserving all other literal argv values."""

    if not argv:
        return None
    token = argv[0]
    if os.sep in token or (os.altsep and os.altsep in token):
        path = Path(token)
        return str(path.resolve()) if path.is_file() and os.access(path, os.X_OK) else None
    from shutil import which

    return which(token)


def resolved_argv(argv: tuple[str, ...]) -> list[str]:
    executable = executable_available(argv)
    if executable is None:
        raise MakeConfigError(f"configured executable is unavailable: {argv[0]}")
    return [executable, *argv[1:]]
