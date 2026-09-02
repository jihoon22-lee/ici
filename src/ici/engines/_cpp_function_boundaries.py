"""Compiler-backed C++ function boundaries from a bounded clang-tidy probe.

The regular complexity fallback deliberately remains a lightweight source
scanner.  When an exact compilation database and an approved ``clang-tidy``
are available, this adapter asks Clang's AST-backed
``readability-function-size`` check to identify real function definitions.
Only diagnostic locations and size notes are consumed; project configuration,
fixes, shell execution, and output files are never enabled.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.cpp_replay import ReplayCommandError, build_replay_command, replay_environment
from ici.core.models import ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_function_boundary_parser import (
    _MAX_BOUNDARIES,
    _MAX_FUNCTION_NAME_CHARS,
    _MAX_SOURCE_BYTES,
    _MAX_SOURCE_CACHE_BYTES,
    _PARSER_TIMEOUT_SECONDS,
    CppFunctionBoundary,
    CppFunctionConfigurationMetric,
    _mapped_source,
    _MappedSource,
    _merge_boundaries,
    _PendingBoundary,
    read_cpp_source_text,
)
from ici.engines._cpp_function_boundary_parser import (
    parse_function_boundaries as _parse_function_boundaries,
)
from ici.engines._cpp_tooling import (
    GccStdlibProjectionCache,
    gcc_standard_library_for_replay,
    regular_executable,
    selected_units,
    tooling_arguments,
)

__all__ = [
    "_MAX_BOUNDARIES",
    "_MAX_FUNCTION_NAME_CHARS",
    "_MAX_SOURCE_BYTES",
    "_MAX_SOURCE_CACHE_BYTES",
    "_PARSER_TIMEOUT_SECONDS",
    "CppFunctionBoundary",
    "CppFunctionBoundaryOutcome",
    "CppFunctionConfigurationMetric",
    "_MappedSource",
    "_PendingBoundary",
    "_mapped_source",
    "_merge_boundaries",
    "parse_function_boundaries",
    "read_cpp_source_text",
    "run_cpp_function_boundaries",
]

_CHECK = "readability-function-size"
_CHECKS = f"-*,{_CHECK}"
_MAX_SELECTED_UNITS = 2_048
_MAX_RUN_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_OUTPUT_CHARS = 1_000_000
_UNIT_TIMEOUT_SECONDS = 120.0
_GLOBAL_TIMEOUT_SECONDS = 600.0


def _metrics_config() -> str:
    payload = {
        "CheckOptions": {
            f"{_CHECK}.CountMemberInitAsStmt": "true",
            f"{_CHECK}.IgnoreMacros": "true",
            f"{_CHECK}.LineThreshold": "0",
            f"{_CHECK}.ParameterThreshold": "0",
            f"{_CHECK}.StatementThreshold": "0",
        }
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _executable_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    details = path.stat()
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _command(
    executable: Path,
    source: Path,
    compiler_arguments: list[str],
    project_root: Path,
) -> list[str]:
    # A dedicated config prevents parent/user .clang-tidy discovery and keeps
    # this structural probe independent from lint policy and NOLINT fixes.
    header_filter = rf"^{re.escape(str(project_root))}(?:/|$)"
    return [
        str(executable),
        "--use-color=false",
        f"--checks={_CHECKS}",
        f"--config={_metrics_config()}",
        f"--header-filter={header_filter}",
        str(source),
        "--",
        *compiler_arguments,
    ]


def parse_function_boundaries(
    project_root: Path,
    cwd: Path,
    stdout: str,
    stderr: str,
    *,
    configuration: str,
    source_file: str | None = None,
    sources: dict[str, _MappedSource] | None = None,
    deadline: float | None = None,
    excluded_scopes: list[tuple[str, int, int | None, str]] | None = None,
) -> tuple[CppFunctionBoundary, ...]:
    """Compatibility wrapper around the parser module.

    The source reader remains late-bound through this module so callers that
    historically monkeypatch ``read_cpp_source_text`` keep working.
    """

    return _parse_function_boundaries(
        project_root,
        cwd,
        stdout,
        stderr,
        configuration=configuration,
        source_file=source_file,
        sources=sources,
        deadline=deadline,
        excluded_scopes=excluded_scopes,
        source_reader=read_cpp_source_text,
    )


@dataclass
class CppFunctionBoundaryOutcome:
    """Bounded adapter result consumed by :class:`ComplexityEngine`."""

    boundaries: list[CppFunctionBoundary] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "unavailable"
    configurations_checked: int = 0
    sources_checked: int = 0
    lambdas_excluded: int = 0
    macro_functions_excluded: int = 0


@dataclass(frozen=True)
class _BoundaryRun:
    root: Path
    context: AnalysisContext
    capability: ToolCapability
    executable: Path
    executable_identity: tuple[int, int, int, int, int, int]
    units: tuple[CompilationUnit, ...]
    sources: dict[str, _MappedSource]


@dataclass(frozen=True)
class _BoundaryUnitResult:
    boundaries: tuple[CppFunctionBoundary, ...]
    excluded_macro_functions: tuple[tuple[str, int, int | None, str], ...]


def _record_projection(
    outcome: CppFunctionBoundaryOutcome,
    context: AnalysisContext,
    projection,
    recorded: set[tuple[str, ...]],
) -> None:
    capability = context.capabilities.capabilities.get("g++")
    for probe in projection.probes:
        if probe.argv in recorded:
            continue
        recorded.add(probe.argv)
        result = probe.result
        outcome.evidence.append(
            ToolEvidence(
                name="g++ stdlib include search",
                path=probe.argv[0],
                version=capability.version if capability is not None else "",
                argv=list(probe.argv),
                returncode=result.returncode if result is not None else None,
                timed_out=result.timed_out if result is not None else False,
                truncated=result.truncated if result is not None else False,
                error=(
                    projection.error if projection.error and probe is projection.probes[-1] else ""
                ),
            )
        )


def _run_unit(
    project_root: Path,
    unit: CompilationUnit,
    context: AnalysisContext,
    capability: ToolCapability,
    executable: Path,
    executable_identity: tuple[int, int, int, int, int, int],
    outcome: CppFunctionBoundaryOutcome,
    runner: Callable[..., ProcessResult],
    timeout: float,
    projection_cache: GccStdlibProjectionCache,
    recorded_probes: set[tuple[str, ...]],
    source_snapshot: _MappedSource,
) -> _BoundaryUnitResult | None:
    if any(item.level == "error" for item in unit.diagnostics):
        outcome.errors.append(f"function boundary context has errors: {unit.source}")
        return None
    try:
        replay = build_replay_command(
            project_root,
            unit,
            context.capabilities,
            operation="syntax",
        )
        compiler_arguments = tooling_arguments(replay.argv, replay.source)
    except ReplayCommandError as err:
        outcome.errors.append(f"function boundary replay {err.code}: {unit.source}: {err}")
        return None
    try:
        if read_cpp_source_text(project_root, unit.source) != source_snapshot.text:
            raise ValueError
    except ValueError:
        outcome.errors.append(f"function boundary source changed before analysis: {unit.source}")
        return None

    deadline = time.monotonic() + timeout
    projection = gcc_standard_library_for_replay(
        project_root,
        replay.argv[0],
        replay.cwd,
        context,
        compiler_arguments,
        projection_cache,
        runner=runner,
        timeout=max(0.0, deadline - time.monotonic()),
    )
    _record_projection(outcome, context, projection, recorded_probes)
    if projection.error:
        outcome.errors.append(
            f"function boundary GCC stdlib replay {projection.error_code}: "
            f"{unit.source}: {projection.error}"
        )
        return None
    compiler_arguments.extend(projection.arguments)
    # Compiler warning policy is irrelevant to AST function boundaries and can
    # otherwise inject unrelated ``-W...`` diagnostics into this dedicated
    # structural channel.  Clang-tidy check diagnostics remain enabled.
    compiler_arguments.append("-w")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        outcome.errors.append(f"function boundary unit budget expired: {unit.source}")
        return None
    command = _command(executable, replay.source, compiler_arguments, project_root)
    try:
        current_executable = regular_executable(project_root, capability)
        current_identity = (
            _executable_identity(current_executable) if current_executable is not None else None
        )
    except OSError:
        current_executable = None
        current_identity = None
    if current_executable != executable or current_identity != executable_identity:
        message = f"function boundary tool identity changed before execution: {unit.source}"
        outcome.evidence.append(
            ToolEvidence(
                name="clang-tidy function boundaries",
                path=str(executable),
                version=capability.version,
                argv=command,
                error=message,
            )
        )
        outcome.errors.append(message)
        return None
    try:
        result = runner(
            command,
            cwd=replay.cwd,
            env=replay_environment(),
            input_text="",
            replace_env=True,
            timeout=remaining,
            max_output_chars=_MAX_OUTPUT_CHARS,
        )
    except Exception as exc:
        message = f"function boundary tool could not execute: {unit.source}: {type(exc).__name__}"
        outcome.evidence.append(
            ToolEvidence(
                name="clang-tidy function boundaries",
                path=str(executable),
                version=capability.version,
                argv=command,
                error=message,
            )
        )
        outcome.errors.append(message)
        return None
    evidence = ToolEvidence(
        name="clang-tidy function boundaries",
        path=str(executable),
        version=capability.version,
        argv=command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
    )
    outcome.evidence.append(evidence)
    if result.timed_out or result.truncated or result.returncode != 0:
        message = f"function boundary tool failed for {unit.source}"
        evidence.error = message
        outcome.errors.append(message)
        return None
    try:
        if read_cpp_source_text(project_root, unit.source) != source_snapshot.text:
            raise ValueError
    except ValueError:
        message = f"function boundary source changed during analysis: {unit.source}"
        evidence.error = message
        outcome.errors.append(message)
        return None
    try:
        excluded_scopes: list[tuple[str, int, int | None, str]] = []
        boundaries = parse_function_boundaries(
            project_root,
            replay.cwd,
            result.stdout,
            result.stderr,
            configuration=unit.configuration,
            source_file=unit.source,
            sources={unit.source: source_snapshot},
            deadline=deadline,
            excluded_scopes=excluded_scopes,
        )
        return _BoundaryUnitResult(boundaries, tuple(excluded_scopes))
    except ValueError as err:
        message = f"function boundary output rejected for {unit.source}: {err}"
        evidence.error = message
        outcome.errors.append(message)
        return None


def _prepare_boundary_run(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext | None,
    outcome: CppFunctionBoundaryOutcome,
    source_texts: dict[str, str] | None,
) -> _BoundaryRun | None:
    try:
        root = project_root.resolve(strict=True)
    except (OSError, RuntimeError):
        outcome.errors.append("function boundary project root is unavailable")
        outcome.mode = "error"
        return None
    if context is None or context.project.root != root:
        return None
    capability = context.capabilities.capabilities.get("clang-tidy")
    executable = regular_executable(root, capability)
    if executable is None or capability is None or context.compilation.database_path is None:
        return None
    if any(item.level == "error" for item in context.compilation.diagnostics):
        outcome.errors.append("function boundary compilation context has ingestion errors")
        outcome.mode = "error"
        return None
    try:
        executable_identity = _executable_identity(executable)
    except OSError:
        outcome.errors.append("function boundary tool identity is unavailable")
        outcome.mode = "error"
        return None

    try:
        selected, missing = selected_units(root, cpp_files, context)
    except (OSError, RuntimeError, ValueError):
        outcome.errors.append("function boundary source selection is outside the project")
        outcome.mode = "error"
        return None
    if missing:
        outcome.errors.append(
            f"function boundary context misses {len(missing)} production source command(s)"
        )
    if not selected:
        outcome.errors.append("function boundary context has no replayable production units")
    if len(selected) > _MAX_SELECTED_UNITS:
        outcome.errors.append("function boundary translation-unit count exceeds the bounded limit")
        outcome.mode = "error"
        return None
    sources: dict[str, _MappedSource] = {}
    source_bytes = 0
    for unit in selected:
        if unit.source in sources:
            continue
        try:
            text = read_cpp_source_text(root, unit.source)
        except ValueError:
            outcome.errors.append(f"function boundary source is unavailable: {unit.source}")
            continue
        expected = source_texts.get(unit.source) if source_texts is not None else text
        if expected is None or expected != text:
            outcome.errors.append(f"function boundary source snapshot changed: {unit.source}")
            continue
        source_bytes += len(text.encode("utf-8"))
        if source_bytes > _MAX_RUN_SOURCE_BYTES:
            outcome.errors.append("function boundary source snapshot budget exceeded")
            break
        if source_bytes > _MAX_SOURCE_CACHE_BYTES:
            outcome.errors.append("function boundary source cache exceeds the bounded limit")
            break
        try:
            mapped = _mapped_source(text)
        except ValueError as err:
            outcome.errors.append(
                f"function boundary source scope is invalid: {unit.source}: {err}"
            )
            continue
        sources[unit.source] = mapped
        outcome.lambdas_excluded += mapped.lambda_count
    if outcome.errors:
        outcome.mode = "error"
        return None
    return _BoundaryRun(
        root,
        context,
        capability,
        executable,
        executable_identity,
        tuple(selected),
        sources,
    )


def _collect_boundary_rows(
    prepared: _BoundaryRun,
    outcome: CppFunctionBoundaryOutcome,
    runner: Callable[..., ProcessResult],
) -> tuple[list[CppFunctionBoundary], dict[str, set[str]]]:
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    collected: list[CppFunctionBoundary] = []
    checked_sources: set[str] = set()
    projection_cache: GccStdlibProjectionCache = {}
    recorded_probes: set[tuple[str, ...]] = set()
    successful_configurations: dict[str, set[str]] = {}
    excluded_macro_functions: set[tuple[str, int, int | None, str]] = set()
    for index, unit in enumerate(prepared.units):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outcome.errors.append(
                f"function boundary global budget left "
                f"{len(prepared.units) - index} unit(s) unexamined"
            )
            break
        unit_result = _run_unit(
            prepared.root,
            unit,
            prepared.context,
            prepared.capability,
            prepared.executable,
            prepared.executable_identity,
            outcome,
            runner,
            min(_UNIT_TIMEOUT_SECONDS, remaining),
            projection_cache,
            recorded_probes,
            prepared.sources[unit.source],
        )
        if unit_result is not None:
            collected.extend(unit_result.boundaries)
            excluded_macro_functions.update(unit_result.excluded_macro_functions)
            checked_sources.add(unit.source)
            successful_configurations.setdefault(unit.source, set()).add(unit.configuration)
            outcome.configurations_checked += 1
        if len(collected) > _MAX_BOUNDARIES:
            outcome.errors.append("function boundary count exceeds the bounded limit")
            break
    outcome.macro_functions_excluded = len(excluded_macro_functions)
    outcome.sources_checked = len(checked_sources)
    return collected, successful_configurations


def run_cpp_function_boundaries(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext | None,
    *,
    runner: Callable[..., ProcessResult],
    source_texts: dict[str, str] | None = None,
) -> CppFunctionBoundaryOutcome:
    """Collect AST-confirmed boundaries without executing project programs."""

    outcome = CppFunctionBoundaryOutcome()
    prepared = _prepare_boundary_run(project_root, cpp_files, context, outcome, source_texts)
    if prepared is None:
        return outcome
    collected, successful_configurations = _collect_boundary_rows(prepared, outcome, runner)
    if outcome.errors:
        outcome.mode = "error"
        return outcome
    outcome.boundaries, merge_warnings = _merge_boundaries(
        collected,
        successful_configurations,
    )
    outcome.warnings.extend(merge_warnings)
    outcome.mode = "exact" if not outcome.warnings else "partial"
    return outcome
