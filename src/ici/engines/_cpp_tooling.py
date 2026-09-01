"""Shared safety primitives for read-only Clang tooling adapters."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ici.core._cpp_replay_policy import is_rejected, is_safe, should_drop
from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.cpp_replay import ReplayCommandError
from ici.core.toolchain import ToolCapability

_WARNING_POLICY_DEMOTIONS = {
    "-pedantic-errors": "-pedantic",
    "--pedantic-errors": "-pedantic",
    "-Werror-implicit-function-declaration": "-Wimplicit-function-declaration",
}


def _diagnostic_warning_argument(argument: str) -> str | None:
    """Demote one warning policy without creating an unsafe compiler option."""

    original = argument
    while True:
        if argument == "-Werror":
            return None
        if argument in _WARNING_POLICY_DEMOTIONS:
            argument = _WARNING_POLICY_DEMOTIONS[argument]
            continue
        if argument.startswith("-Werror="):
            warning = argument.removeprefix("-Werror=")
            if not warning:
                return None
            argument = f"-W{warning}"
            continue
        break
    if argument != original and (
        should_drop(argument) or is_rejected(argument) or not is_safe(argument)
    ):
        raise ReplayCommandError(
            "unsafe-tooling-warning-policy",
            "A warning-as-error option cannot be projected to a safe diagnostic flag.",
        )
    return argument


def inside(root: Path, path: Path) -> bool:
    """Return whether an already-normalized path is contained by ``root``."""

    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def regular_executable(root: Path, capability: ToolCapability | None) -> Path | None:
    """Resolve an approved capability to a non-project regular executable."""

    if (
        capability is None
        or not capability.available
        or not capability.complete
        or not capability.path
    ):
        return None
    try:
        path = Path(capability.path).resolve(strict=True)
        details = path.stat()
    except (OSError, RuntimeError):
        return None
    if (
        not path.is_absolute()
        or inside(root, path)
        or not stat.S_ISREG(details.st_mode)
        or not os.access(path, os.X_OK)
    ):
        return None
    return path


def selected_units(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext,
) -> tuple[list[CompilationUnit], list[str]]:
    """Select exact production units, preserving unity-build coverage semantics."""

    sources = {path.relative_to(project_root).as_posix() for path in cpp_files}
    units = context.compilation.units
    if context.compilation.unity_build:
        selected = [unit for unit in units if unit.language in {"c", "c++"}]
        return selected, []
    selected = [unit for unit in units if unit.source in sources]
    covered = {unit.source for unit in selected}
    return selected, sorted(sources - covered)


def tooling_arguments(argv: tuple[str, ...], source: Path) -> list[str]:
    """Return non-fatal compiler context for diagnostic-only tooling.

    Production builds may intentionally promote warnings to errors.  Reusing
    that policy with clang-tidy or clazy turns an ordinary finding into a tool
    execution failure and prevents the adapter from reporting the diagnostic.
    Keep the selected warning set, but demote warning-as-error switches before
    handing the exact context to those diagnostic tools.
    """

    expected = ("-Wall", "-Wextra", "-fsyntax-only", str(source))
    if len(argv) < 5 or tuple(argv[-4:]) != expected:
        raise ReplayCommandError(
            "unexpected-replay-shape",
            "The sanitized compiler replay did not have the expected analysis suffix.",
        )
    arguments: list[str] = []
    for argument in argv[1:-4]:
        projected = _diagnostic_warning_argument(argument)
        if projected is not None:
            arguments.append(projected)
    return arguments
