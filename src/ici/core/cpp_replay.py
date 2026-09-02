"""Safe, compiler-exact replay commands for normalized translation units.

The compilation database is project-controlled input.  Replaying it verbatim
would allow output writes, compiler plugins, wrapper programs, or extra source
operands.  This module accepts only a directly probed GCC/Clang driver, removes
compile/dependency output options, revalidates the working tree, and adds one
controlled read-only operation.
"""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ici.core._cpp_replay_policy import (
    COMPILER_CAPABILITIES,
    COMPILER_NAME_RE,
    DROP_VALUE,
    PRESERVE_VALUE,
    SAFE_LANGUAGE_VALUES,
    is_rejected,
    is_safe,
    should_drop,
)
from ici.core.capabilities import CapabilityInventory
from ici.core.context import CompilationContext, CompilationUnit

MAX_REPLAY_ARGUMENTS = 32_768
MAX_REPLAY_ARGUMENT_CHARS = 1024 * 1024

_WARNING_POLICY_DEMOTIONS = {
    "-pedantic-errors": "-pedantic",
    "--pedantic-errors": "-pedantic",
    "-Werror-implicit-function-declaration": "-Wimplicit-function-declaration",
}


class ReplayCommandError(ValueError):
    """A compilation command cannot be replayed without unsafe guessing."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ReplayCommand:
    """One revalidated, read-only compiler invocation."""

    argv: tuple[str, ...]
    cwd: Path
    source: Path
    compiler: str
    configuration: str


def diagnostic_warning_argument(argument: str) -> str | None:
    """Demote one warning policy for a diagnostic-only compiler invocation."""

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


def compilation_context_present(context: CompilationContext) -> bool:
    """Distinguish a genuinely absent database from an unusable selected one."""

    return (
        bool(context.units)
        or context.database_path is not None
        or bool(context.origin)
        or any(item.level == "error" for item in context.diagnostics)
    )


def replay_environment() -> dict[str, str]:
    """Return a minimal compiler environment without argv/config override hooks."""

    environment = {"LANG": "C", "LC_ALL": "C", "TERM": "dumb"}
    if os.name == "nt":
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        if system_root:
            environment["SYSTEMROOT"] = system_root
            environment["WINDIR"] = system_root
            environment["PATH"] = str(Path(system_root) / "System32")
        for name in ("COMSPEC", "PATHEXT"):
            if value := os.environ.get(name):
                environment[name] = value
    else:
        environment["PATH"] = os.defpath
    return environment


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _regular_executable(path: Path) -> bool:
    try:
        details = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(details.st_mode) and os.access(path, os.X_OK)


def _approved_compiler_paths(inventory: CapabilityInventory) -> set[Path]:
    approved: set[Path] = set()
    for name in COMPILER_CAPABILITIES:
        capability = inventory.capabilities.get(name)
        if (
            capability is None
            or not capability.available
            or not capability.complete
            or not capability.path
        ):
            continue
        try:
            candidate = Path(capability.path).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if _regular_executable(candidate):
            approved.add(candidate)
    return approved


def _resolve_compiler(
    value: str,
    *,
    root: Path,
    cwd: Path,
    inventory: CapabilityInventory,
) -> Path:
    name = Path(value).name
    if COMPILER_NAME_RE.fullmatch(name) is None:
        raise ReplayCommandError(
            "unsupported-compiler-driver",
            "The translation unit does not use a direct GCC/Clang compiler driver.",
        )
    lexical = Path(value)
    if lexical.is_absolute():
        candidate = lexical
    elif len(lexical.parts) > 1:
        candidate = cwd / lexical
    else:
        discovered = shutil.which(value)
        if discovered is None:
            raise ReplayCommandError(
                "compiler-unavailable",
                "The translation unit compiler is not available on PATH.",
            )
        candidate = Path(discovered)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        raise ReplayCommandError(
            "compiler-unavailable",
            "The translation unit compiler could not be resolved.",
        ) from err
    if not _regular_executable(resolved):
        raise ReplayCommandError(
            "compiler-not-executable",
            "The translation unit compiler is not a regular executable.",
        )
    if _is_within(resolved, root):
        raise ReplayCommandError(
            "project-compiler-rejected",
            "Project-contained compiler programs are not executed during analysis.",
        )
    if resolved not in _approved_compiler_paths(inventory):
        raise ReplayCommandError(
            "compiler-not-probed",
            "The translation unit compiler does not match a successfully probed GCC/Clang tool.",
        )
    return candidate.resolve(strict=False)


def _resolved_operand(value: str, cwd: Path) -> Path | None:
    if not value or "\0" in value or value.startswith("-"):
        return None
    try:
        path = Path(value)
        return (path if path.is_absolute() else cwd / path).resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _require_value(argv: tuple[str, ...], index: int, option: str) -> str:
    if (
        index + 1 >= len(argv)
        or not argv[index + 1]
        or "\0" in argv[index + 1]
        or argv[index + 1].startswith("-")
    ):
        raise ReplayCommandError(
            "missing-option-value",
            f"The compiler option {option} has no safe value.",
        )
    return argv[index + 1]


def _consume_option(argv: tuple[str, ...], index: int) -> tuple[int, tuple[str, ...]]:
    """Validate one non-operand option and return its sanitized contribution."""

    token = argv[index]
    if should_drop(token):
        return index + 1, ()
    if is_rejected(token):
        raise ReplayCommandError(
            "unsafe-compiler-option",
            f"The compiler option {token} is not allowed during analysis replay.",
        )
    if token in DROP_VALUE:
        _require_value(argv, index, token)
        return index + 2, ()
    if token in PRESERVE_VALUE:
        value = _require_value(argv, index, token)
        if token in {"-x", "--language"} and value not in SAFE_LANGUAGE_VALUES:
            raise ReplayCommandError(
                "unsafe-compiler-option",
                f"The compiler language {value} is not allowed during analysis replay.",
            )
        return index + 2, (token, value)
    if is_safe(token):
        return index + 1, (token,)
    raise ReplayCommandError(
        "unsafe-compiler-option",
        f"The compiler option {token} is outside the replay allowlist.",
    )


def _filtered_arguments(
    unit: CompilationUnit,
    cwd: Path,
    source: Path,
    *,
    drop_warning_suppression: bool = False,
) -> tuple[str, ...]:
    argv = unit.argv
    if (
        len(argv) > MAX_REPLAY_ARGUMENTS
        or sum(len(value) for value in argv) > MAX_REPLAY_ARGUMENT_CHARS
    ):
        raise ReplayCommandError(
            "command-too-large",
            "The translation unit command exceeds the bounded replay size.",
        )
    if any(not value or "\0" in value for value in argv):
        raise ReplayCommandError(
            "invalid-command-argument",
            "The translation unit command contains an invalid argument.",
        )

    kept: list[str] = []
    source_operands = 0
    after_separator = False
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            after_separator = True
            index += 1
            continue
        if token.startswith("@"):
            raise ReplayCommandError(
                "unexpanded-response-file",
                "Compiler response files must be expanded before replay.",
            )
        if token == "-":
            raise ReplayCommandError(
                "extra-compiler-operand",
                "Compiler stdin cannot be used as an additional replay input.",
            )
        if token == "-w" and drop_warning_suppression:
            index += 1
            continue
        operand = _resolved_operand(token, cwd)
        if operand == source:
            source_operands += 1
            index += 1
            continue
        if after_separator or not token.startswith("-"):
            raise ReplayCommandError(
                "extra-compiler-operand",
                "The translation unit command contains an extra or mismatched input operand.",
            )
        index, preserved = _consume_option(argv, index)
        kept.extend(preserved)

    if source_operands != 1:
        raise ReplayCommandError(
            "invalid-source-operands",
            "The translation unit command must identify its canonical source exactly once.",
        )
    return tuple(kept)


def build_replay_command(
    root: Path,
    unit: CompilationUnit,
    inventory: CapabilityInventory,
    *,
    operation: Literal["syntax", "includes", "unused-functions"],
) -> ReplayCommand:
    """Build one bounded diagnostic command without executing it."""

    project_root = root.resolve(strict=True)
    try:
        cwd = (project_root / PurePosixPath(unit.directory)).resolve(strict=True)
        source = (project_root / PurePosixPath(unit.source)).resolve(strict=True)
    except (OSError, RuntimeError) as err:
        raise ReplayCommandError(
            "stale-translation-unit",
            "The translation unit working directory or source no longer exists.",
        ) from err
    if not _is_within(cwd, project_root) or not cwd.is_dir():
        raise ReplayCommandError(
            "unsafe-working-directory",
            "The translation unit working directory is outside the project or not a directory.",
        )
    if not _is_within(source, project_root) or not source.is_file():
        raise ReplayCommandError(
            "unsafe-source",
            "The translation unit source is outside the project or not a regular file.",
        )
    compiler = _resolve_compiler(
        unit.argv[0],
        root=project_root,
        cwd=cwd,
        inventory=inventory,
    )
    arguments = list(
        _filtered_arguments(
            unit,
            cwd,
            source,
            drop_warning_suppression=operation == "unused-functions",
        )
    )
    arguments.append("-fdiagnostics-color=never")
    if operation == "syntax":
        arguments.extend(("-Wall", "-Wextra", "-fsyntax-only"))
    elif operation == "includes":
        arguments.extend(("-w", "-E", "-H", "-o", os.devnull))
    elif operation == "unused-functions":
        projected: list[str] = []
        for argument in arguments:
            if argument == "-w":
                continue
            warning_argument = diagnostic_warning_argument(argument)
            if warning_argument is not None:
                projected.append(warning_argument)
        arguments = projected
        # GCC intentionally omits -Wunused-function during -fsyntax-only.
        # Compile to discarded assembly so the front end completes the phase
        # that owns this diagnostic without creating a project artifact or
        # invoking the assembler/linker.
        arguments.extend(
            (
                "-Wunused-function",
                "-Wno-error=unused-function",
                "-S",
                "-o",
                os.devnull,
            )
        )
    else:  # pragma: no cover - Literal plus runtime guard for non-typed callers
        raise ReplayCommandError("unsupported-operation", "Unsupported compiler replay operation.")
    arguments.append(str(source))
    command = (str(compiler), *arguments)
    if (
        len(command) > MAX_REPLAY_ARGUMENTS
        or sum(len(value) for value in command) > MAX_REPLAY_ARGUMENT_CHARS
    ):
        raise ReplayCommandError(
            "command-too-large",
            "The sanitized translation unit command exceeds the bounded replay size.",
        )
    return ReplayCommand(
        argv=command,
        cwd=cwd,
        source=source,
        compiler=unit.compiler or Path(unit.argv[0]).name,
        configuration=unit.configuration,
    )
