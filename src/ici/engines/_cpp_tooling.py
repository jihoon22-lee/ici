"""Shared safety primitives for read-only Clang tooling adapters."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ici.core._cpp_replay_policy import COMPILER_CAPABILITIES
from ici.core.capabilities import CapabilityInventory
from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.cpp_replay import (
    ReplayCommandError,
    diagnostic_warning_argument,
    replay_environment,
)
from ici.core.runner import ProcessResult, run_process
from ici.core.toolchain import ToolCapability

_MAX_INCLUDE_ROOTS = 512
_SEPARATE_INCLUDE_OPTIONS = {
    "-I",
    "-F",
    "-idirafter",
    "-iframework",
    "-iquote",
    "-isystem",
    "/I",
    "/external:I",
    "/imsvc",
}
_JOINED_INCLUDE_OPTIONS = (
    "/external:I",
    "-iframework",
    "-idirafter",
    "-isystem",
    "-iquote",
    "/imsvc",
    "-I",
    "-F",
    "/I",
)
_AMBIGUOUS_INCLUDE_PREFIXES = (
    "-iframeworkwithsysroot",
    "-isystem-after",
)
_PATH_SEPARATED_JOINED_OPTIONS = {
    "-idirafter",
    "-iframework",
    "-iquote",
    "-isystem",
}
_INCLUDE_SEARCH_START = "#include <...> search starts here:"
_INCLUDE_SEARCH_END = "End of search list."
_MAX_IMPLICIT_INCLUDE_PATHS = 64
_MAX_IMPLICIT_INCLUDE_OUTPUT_CHARS = 131_072
_MAX_IMPLICIT_INCLUDE_PATH_CHARS = 4_096
_IMPLICIT_INCLUDE_TIMEOUT_SECONDS = 5.0
_IMPLICIT_INCLUDE_TOTAL_TIMEOUT_SECONDS = 10.0
_TOOLCHAIN_SELECTOR_EXACT = {"-m32", "-m64", "-mx32"}
_TOOLCHAIN_SELECTOR_SEPARATE = {
    "--sysroot",
    "-isysroot",
    "-mabi",
    "-march",
    "-mcpu",
    "-mtune",
}
_TOOLCHAIN_SELECTOR_JOINED = (
    "--sysroot=",
    "-isysroot=",
)


@dataclass(frozen=True)
class CompilerIncludeProbe:
    """One bounded, read-only compiler include-search probe."""

    language: str
    argv: tuple[str, ...]
    result: ProcessResult | None


@dataclass(frozen=True)
class GccStdlibProjection:
    """Exact libstdc++ arguments and the probes that justified them."""

    arguments: tuple[str, ...] = ()
    probes: tuple[CompilerIncludeProbe, ...] = ()
    error_code: str = ""
    error: str = ""


CompilerExecutableIdentity = tuple[int, int, int, int, int]
GccStdlibProjectionCache = dict[
    tuple[str, str, tuple[str, ...], CompilerExecutableIdentity],
    GccStdlibProjection,
]


def compiler_capability(
    executable: str | Path,
    inventory: CapabilityInventory,
) -> ToolCapability | None:
    """Return the probed capability matching one resolved compiler driver."""

    compiler = Path(executable).resolve(strict=False)
    matches: list[ToolCapability] = []
    for capability in inventory.capabilities.values():
        if capability.name not in COMPILER_CAPABILITIES or not capability.path:
            continue
        try:
            if Path(capability.path).resolve(strict=False) == compiler:
                matches.append(capability)
        except (OSError, RuntimeError):
            continue
    if not matches:
        return None

    # Apple and distro alternatives can expose one Clang executable through
    # both g++/gcc and clang++/clang spellings. Prefer the capability whose
    # observed version identifies the real family instead of trusting the
    # compilation-database alias. This also keeps the diagnostic format and
    # tool attribution aligned with the process that will actually run.
    clang_matches = [item for item in matches if "clang" in item.version.casefold()]
    if clang_matches:
        return next(
            (item for item in clang_matches if item.name in {"clang", "clang++"}),
            clang_matches[0],
        )
    compiler_name = compiler.name.casefold()
    if "clang" in compiler_name:
        return next(
            (item for item in matches if item.name in {"clang", "clang++"}),
            matches[0],
        )
    return matches[0]


def _compiler_family(capability: ToolCapability) -> str:
    """Return the observed compiler family before falling back to its probe name."""

    version = capability.version.casefold()
    if "clang" in version:
        return "clang"
    if "gcc" in version or "g++" in version or "gnu compiler" in version:
        return "gcc"
    return "gcc" if capability.name in {"gcc", "g++"} else "clang"


def compiler_diagnostic_command(
    command: list[str],
    inventory: CapabilityInventory,
    *,
    source_last: bool = True,
) -> list[str]:
    """Request bounded structured diagnostics from an approved compiler."""

    if len(command) < 2:
        raise ValueError("compiler diagnostic command is incomplete")
    capability = compiler_capability(command[0], inventory)
    gcc_json = (
        capability is not None
        and _compiler_family(capability) == "gcc"
        and capability.version_tuple >= (9,)
    )
    diagnostic_flag = "-fdiagnostics-format=json" if gcc_json else "-fdiagnostics-parseable-fixits"
    controlled = (diagnostic_flag, "-fdiagnostics-show-option")
    if source_last:
        return [*command[:-1], *controlled, command[-1]]
    return [*command, *controlled]


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


def gcc_compiler_for_replay(
    project_root: Path,
    replay_executable: str,
    context: AnalysisContext,
) -> Path | None:
    """Return the selected approved GNU C++ driver, if the replay uses it.

    Hosted build images commonly install several libstdc++ development versions.
    Clang tooling otherwise chooses the newest one it can find instead of the one
    used by the compile database.  Compare resolved executable identity rather
    than an argv basename so ``c++`` and distro alternatives remain exact.
    """

    approved = regular_executable(
        project_root,
        context.capabilities.capabilities.get("g++"),
    )
    if approved is None:
        return None
    try:
        replay = Path(replay_executable).resolve(strict=True)
        if not replay.is_file() or not os.access(replay, os.X_OK):
            return None
        if not replay.samefile(approved):
            return None
    except (OSError, RuntimeError):
        return None
    # On Apple platforms and some custom images, a ``g++`` alternative can
    # resolve to Clang.  Do not apply GCC-specific header projection merely
    # because the command was reached through a GNU-compatible alias.
    if "clang" in replay.name.casefold():
        return None
    return replay


def _toolchain_selector_arguments(arguments: list[str]) -> tuple[str, ...]:
    """Keep only GCC options that can change its implicit include search."""

    selected: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in _TOOLCHAIN_SELECTOR_EXACT:
            selected.append(argument)
            index += 1
            continue
        if argument in _TOOLCHAIN_SELECTOR_SEPARATE:
            if (
                index + 1 >= len(arguments)
                or not arguments[index + 1]
                or "\x00" in arguments[index + 1]
                or arguments[index + 1].startswith("-")
            ):
                raise ReplayCommandError(
                    "missing-toolchain-selector-value",
                    "A GCC toolchain selector has no value.",
                )
            selected.extend((argument, arguments[index + 1]))
            index += 2
            continue
        for prefix in _TOOLCHAIN_SELECTOR_JOINED:
            if argument.startswith(prefix):
                value = argument[len(prefix) :]
                if not value or "\x00" in value:
                    raise ReplayCommandError(
                        "missing-toolchain-selector-value",
                        "A GCC toolchain selector has no value.",
                    )
                selected.append(argument)
                break
        else:
            if argument.startswith("-m") and len(argument) > 2:
                selected.append(argument)
        index += 1
    return tuple(selected)


def _is_gnu_cxx_verbose_output(output: str) -> bool:
    """Recognize GCC driver evidence without trusting an executable alias."""

    return any(
        line.startswith("COLLECT_GCC=") and bool(line.removeprefix("COLLECT_GCC=").strip())
        for line in output.splitlines()
    )


def _compact_probe_result(result: ProcessResult) -> ProcessResult:
    """Discard compiler prose after parsing while retaining evidence metadata."""

    return ProcessResult(
        returncode=result.returncode,
        stdout="",
        stderr="",
        duration=result.duration,
        timed_out=result.timed_out,
        truncated=result.truncated,
    )


def _compiler_executable_identity(compiler: Path) -> CompilerExecutableIdentity | None:
    """Return replacement-sensitive identity for an already approved driver."""

    try:
        details = compiler.stat()
    except OSError:
        return None
    if not stat.S_ISREG(details.st_mode) or not os.access(compiler, os.X_OK):
        return None
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def parse_compiler_include_search(output: str, cwd: Path) -> tuple[Path, ...]:
    """Parse one strict GCC angle-bracket include-search block.

    The verbose compiler stream is host evidence, not trusted structured data.
    Accept exactly one bounded block and only existing, non-root absolute
    directories.  Error messages deliberately omit the raw host path.
    """

    if not isinstance(output, str):
        raise ValueError("compiler include search output must be text")
    if len(output) > _MAX_IMPLICIT_INCLUDE_OUTPUT_CHARS:
        raise ValueError("compiler include search output exceeds the bounded size")
    if "\x00" in output:
        raise ValueError("compiler include search output contains a null byte")
    try:
        resolved_cwd = cwd.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        raise ValueError("compiler include search working directory is unavailable") from err
    if not resolved_cwd.is_dir():
        raise ValueError("compiler include search working directory is not a directory")
    lines = output.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == _INCLUDE_SEARCH_START]
    ends = [index for index, line in enumerate(lines) if line.strip() == _INCLUDE_SEARCH_END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise ValueError("compiler include search block is missing, duplicated, or malformed")

    roots: list[Path] = []
    seen: set[Path] = set()
    for index, raw_line in enumerate(lines[starts[0] + 1 : ends[0]]):
        value = raw_line.strip()
        framework_suffix = " (framework directory)"
        if value.endswith(framework_suffix):
            value = value[: -len(framework_suffix)].rstrip()
        if (
            not value
            or len(value) > _MAX_IMPLICIT_INCLUDE_PATH_CHARS
            or "\x00" in value
            or "\r" in value
            or "\n" in value
        ):
            raise ValueError(f"compiler include search path {index} is malformed")
        lexical = Path(value)
        if not lexical.is_absolute():
            raise ValueError(f"compiler include search path {index} is not absolute")
        try:
            root = lexical.resolve(strict=True)
        except (OSError, RuntimeError) as err:
            raise ValueError(f"compiler include search path {index} is unavailable") from err
        if not root.is_dir() or root == Path(root.anchor):
            raise ValueError(f"compiler include search path {index} is not a bounded directory")
        if root in seen:
            raise ValueError("compiler include search contains a duplicate directory")
        roots.append(root)
        seen.add(root)
        if len(roots) > _MAX_IMPLICIT_INCLUDE_PATHS:
            raise ValueError("compiler include search contains too many directories")
    if not roots:
        raise ValueError("compiler include search contains no directories")
    return tuple(roots)


def gcc_standard_library_projection(
    compiler: Path,
    cwd: Path,
    compiler_arguments: list[str],
    *,
    runner: Callable[..., ProcessResult] = run_process,
    timeout: float = _IMPLICIT_INCLUDE_TOTAL_TIMEOUT_SECONDS,
) -> GccStdlibProjection:
    """Pin Clang tooling to the selected GCC driver's exact libstdc++ roots.

    GCC's C++ include search minus its C include search is the portable evidence
    for its C++ standard-library directories.  Replaying those directories with
    ``-nostdinc++`` prevents Clang/clazy from silently selecting another installed
    GCC version while leaving compiler builtin and C system headers untouched.
    """

    if "-nostdinc" in compiler_arguments or "-nostdinc++" in compiler_arguments:
        return GccStdlibProjection()
    try:
        selectors = _toolchain_selector_arguments(compiler_arguments)
    except ReplayCommandError as err:
        return GccStdlibProjection(error_code=err.code, error=str(err))

    if timeout <= 0:
        return GccStdlibProjection(
            error_code="gcc-include-probe-timeout",
            error="GCC include search has no remaining time budget.",
        )

    probes: list[CompilerIncludeProbe] = []
    searches: dict[str, tuple[Path, ...]] = {}
    deadline = time.monotonic() + min(timeout, _IMPLICIT_INCLUDE_TOTAL_TIMEOUT_SECONDS)
    for language in ("c++", "c"):
        command = (
            str(compiler),
            *selectors,
            "-E",
            "-x",
            language,
            "-v",
            "-",
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            probes.append(CompilerIncludeProbe(language, command, None))
            return GccStdlibProjection(
                probes=tuple(probes),
                error_code="gcc-include-probe-timeout",
                error="GCC include search exhausted its bounded time budget.",
            )
        try:
            result = runner(
                list(command),
                cwd=cwd,
                env=replay_environment(),
                input_text="",
                replace_env=True,
                timeout=min(_IMPLICIT_INCLUDE_TIMEOUT_SECONDS, remaining),
                max_output_chars=_MAX_IMPLICIT_INCLUDE_OUTPUT_CHARS,
            )
        except Exception as exc:
            probes.append(CompilerIncludeProbe(language, command, None))
            return GccStdlibProjection(
                probes=tuple(probes),
                error_code="gcc-include-probe-exception",
                error=f"GCC include search could not execute: {type(exc).__name__}",
            )
        probes.append(CompilerIncludeProbe(language, command, _compact_probe_result(result)))
        if result.timed_out:
            return GccStdlibProjection(
                probes=tuple(probes),
                error_code="gcc-include-probe-timeout",
                error="GCC include search timed out.",
            )
        if result.truncated:
            return GccStdlibProjection(
                probes=tuple(probes),
                error_code="gcc-include-probe-truncated",
                error="GCC include search output was truncated.",
            )
        if result.returncode != 0:
            return GccStdlibProjection(
                probes=tuple(probes),
                error_code="gcc-include-probe-failed",
                error="GCC include search failed.",
            )
        if language == "c++" and not _is_gnu_cxx_verbose_output(result.stderr):
            # A g++-compatible alias can be backed by Clang.  Preserve the
            # original tooling replay in that case instead of manufacturing a
            # GCC projection from a different driver's search path.
            return GccStdlibProjection(probes=tuple(probes))
        try:
            searches[language] = parse_compiler_include_search(result.stderr, cwd)
        except ValueError as err:
            return GccStdlibProjection(
                probes=tuple(probes),
                error_code="gcc-include-probe-unparseable",
                error=str(err),
            )

    c_roots = set(searches["c"])
    cpp_only = tuple(root for root in searches["c++"] if root not in c_roots)
    if not cpp_only:
        return GccStdlibProjection(
            probes=tuple(probes),
            error_code="gcc-stdlib-unresolved",
            error="GCC include search did not identify C++ standard-library directories.",
        )
    projected: list[str] = ["-nostdinc++"]
    for root in cpp_only:
        projected.extend(("-isystem", str(root)))
    return GccStdlibProjection(arguments=tuple(projected), probes=tuple(probes))


def gcc_standard_library_for_replay(
    project_root: Path,
    replay_executable: str,
    cwd: Path,
    context: AnalysisContext,
    compiler_arguments: list[str],
    cache: GccStdlibProjectionCache,
    *,
    runner: Callable[..., ProcessResult] = run_process,
    timeout: float = _IMPLICIT_INCLUDE_TOTAL_TIMEOUT_SECONDS,
) -> GccStdlibProjection:
    """Return a cached exact GCC stdlib projection for one tooling replay."""

    compiler = gcc_compiler_for_replay(project_root, replay_executable, context)
    if compiler is None:
        return GccStdlibProjection()
    try:
        selectors = _toolchain_selector_arguments(compiler_arguments)
    except ReplayCommandError as err:
        return GccStdlibProjection(error_code=err.code, error=str(err))
    try:
        resolved_cwd = cwd.resolve(strict=True)
    except (OSError, RuntimeError):
        return GccStdlibProjection(
            error_code="gcc-include-probe-cwd",
            error="GCC include search working directory is unavailable.",
        )
    compiler_identity = _compiler_executable_identity(compiler)
    if compiler_identity is None:
        return GccStdlibProjection(
            error_code="gcc-include-probe-compiler",
            error="GCC include search compiler identity is unavailable.",
        )
    key = (str(compiler), str(resolved_cwd), selectors, compiler_identity)
    cached = cache.get(key)
    if cached is not None:
        return cached
    projection = gcc_standard_library_projection(
        compiler,
        cwd,
        compiler_arguments,
        runner=runner,
        timeout=timeout,
    )
    if _compiler_executable_identity(compiler) != compiler_identity:
        return GccStdlibProjection(
            probes=projection.probes,
            error_code="gcc-include-probe-compiler-changed",
            error="GCC include search compiler identity changed during probing.",
        )
    cache[key] = projection
    return projection


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
        projected = diagnostic_warning_argument(argument)
        if projected is not None:
            arguments.append(projected)
    return arguments


def tooling_include_roots(arguments: list[str], cwd: Path) -> tuple[Path, ...]:
    """Resolve explicit compiler include roots used to validate diagnostic previews.

    Clang can echo source previews for notes in Qt or other external headers.  The
    diagnostic parser may compare those previews with disk only when the path is
    covered by the exact, sanitized compilation context.  This projection keeps
    that read authority narrower than the whole host filesystem.
    """

    roots: list[Path] = []
    seen: set[Path] = set()
    pending = False
    ignored_pending = False
    for argument in arguments:
        value = ""
        if ignored_pending:
            ignored_pending = False
            continue
        if pending:
            value = argument
            pending = False
        elif argument in _SEPARATE_INCLUDE_OPTIONS:
            pending = True
            continue
        elif argument in _AMBIGUOUS_INCLUDE_PREFIXES:
            ignored_pending = True
            continue
        elif argument.startswith(_AMBIGUOUS_INCLUDE_PREFIXES):
            # These are distinct compiler options with different sysroot
            # semantics, not joined spellings of -iframework/-isystem.
            continue
        else:
            for option in _JOINED_INCLUDE_OPTIONS:
                if argument.startswith(option) and len(argument) > len(option):
                    candidate = argument[len(option) :]
                    if option in _PATH_SEPARATED_JOINED_OPTIONS and candidate[0] not in ".\\/":
                        continue
                    value = candidate
                    break
        if not value or value.startswith("=") or "\x00" in value:
            continue
        try:
            lexical = Path(value)
            root = (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=False)
            if not root.is_dir() or root == Path(root.anchor):
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        if root in seen:
            continue
        roots.append(root)
        seen.add(root)
        if len(roots) > _MAX_INCLUDE_ROOTS:
            raise ReplayCommandError(
                "too-many-tooling-include-roots",
                "The sanitized compiler context contains too many include roots.",
            )
    return tuple(roots)
