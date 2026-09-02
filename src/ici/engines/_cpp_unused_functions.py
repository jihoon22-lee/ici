"""Exact compiler diagnostics for unused internal-linkage C/C++ functions.

This adapter deliberately has a narrow claim.  It replays every selected
production translation-unit configuration with ``-Wunused-function`` and
retains a finding only when all configurations for that source report the
same located diagnostic.  It does not infer whole-program or linker
reachability, and diagnostics originating in headers are outside this first
source-owned contract.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.cpp_replay import (
    ReplayCommand,
    ReplayCommandError,
    build_replay_command,
    replay_environment,
)
from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_diagnostics import CppDiagnostic, parse_compiler_diagnostics
from ici.engines._cpp_tooling import (
    compiler_capability,
    compiler_diagnostic_command,
    regular_executable,
    selected_units,
)
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources

_MAX_SELECTED_UNITS = 2_048
_MAX_OUTPUT_CHARS = 1_000_000
_UNIT_TIMEOUT_SECONDS = 120.0
_GLOBAL_TIMEOUT_SECONDS = 600.0
_UNUSED_RULE = "-Wunused-function"
_CONFIGURATION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_ExecutableIdentity = tuple[int, int, int, int, int, int]
_DiagnosticKey = tuple[str, int, int | None, int | None, int | None]


@dataclass(frozen=True)
class CppUnusedFunction:
    """One source-level diagnostic agreed on by every source configuration."""

    target: InspectionTarget
    configurations: tuple[str, ...]
    tool_names: tuple[str, ...]
    tool_versions: tuple[str, ...]
    diagnostic_message: str = ""


@dataclass
class CppUnusedFunctionOutcome:
    """Bounded compiler-probe result consumed by the dead-code facade."""

    targets: list[InspectionTarget] = field(default_factory=list)
    functions: list[CppUnusedFunction] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "unavailable"
    configurations_checked: int = 0
    sources_checked: int = 0
    non_tu_diagnostics_excluded: int = 0


@dataclass(frozen=True)
class _PreparedUnit:
    unit: CompilationUnit
    replay: ReplayCommand
    command: tuple[str, ...]
    capability: ToolCapability
    executable: Path
    executable_identity: _ExecutableIdentity
    source_text: str


@dataclass(frozen=True)
class _Observation:
    source: str
    configuration: str
    diagnostics: tuple[tuple[_DiagnosticKey, str], ...]
    tool_name: str
    tool_version: str


def _executable_identity(path: Path) -> _ExecutableIdentity:
    details = path.stat()
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _source_text(project_root: Path, file_path: str) -> str:
    try:
        inventory = read_analysis_sources(
            project_root,
            (project_root / file_path,),
            include_generated=True,
            include_vendor=True,
        )
    except (AnalysisSourceError, OSError, RuntimeError, ValueError) as err:
        raise ValueError("compiler unused-function source is unavailable") from err
    if len(inventory.sources) != 1:
        raise ValueError("compiler unused-function source is unavailable")
    return inventory.sources[0].text


def _error_target(source: str, message: str) -> InspectionTarget:
    return InspectionTarget(
        file_path=source,
        start_line=1,
        target_name="C++UnusedFunctionError",
        status=EngineStatus.ERROR,
        message=message,
    )


def _append_error(
    outcome: CppUnusedFunctionOutcome,
    source: str,
    message: str,
) -> None:
    outcome.errors.append(message)
    outcome.targets.append(_error_target(source, message))


def _prepare_units(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext | None,
    source_texts: dict[str, str],
    outcome: CppUnusedFunctionOutcome,
) -> tuple[Path, tuple[_PreparedUnit, ...]] | None:
    try:
        root = project_root.resolve(strict=True)
    except (OSError, RuntimeError):
        _append_error(outcome, ".", "C++ unused-function project root is unavailable")
        outcome.mode = "error"
        return None
    if not cpp_files:
        outcome.mode = "not-applicable"
        return None
    if context is None or context.compilation.database_path is None:
        outcome.warnings.append(
            "Exact C++ unused-function analysis requires a compilation database"
        )
        return None
    if _CONFIGURATION_RE.fullmatch(context.compilation.database_digest) is None:
        _append_error(
            outcome,
            context.compilation.database_path,
            "C++ unused-function compilation database has no canonical digest",
        )
        outcome.mode = "error"
        return None
    if context.project.root != root:
        _append_error(outcome, ".", "C++ unused-function context belongs to another project")
        outcome.mode = "error"
        return None
    if context.compilation.unity_build:
        _append_error(
            outcome,
            context.compilation.database_path,
            "C++ unused-function analysis cannot prove source ownership through a unity build",
        )
        outcome.mode = "error"
        return None
    if any(item.level == "error" for item in context.compilation.diagnostics):
        _append_error(
            outcome,
            context.compilation.database_path,
            "C++ unused-function compilation context has ingestion errors",
        )
        outcome.mode = "error"
        return None
    try:
        selected, missing = selected_units(root, cpp_files, context)
    except (OSError, RuntimeError, ValueError):
        _append_error(outcome, ".", "C++ unused-function source selection is unsafe")
        outcome.mode = "error"
        return None
    if missing:
        for source in missing:
            _append_error(
                outcome,
                source,
                "No exact translation-unit configuration covers this production source",
            )
        outcome.mode = "error"
        return None
    if not selected:
        _append_error(
            outcome,
            context.compilation.database_path,
            "C++ unused-function context has no replayable production units",
        )
        outcome.mode = "error"
        return None
    if len(selected) > _MAX_SELECTED_UNITS:
        _append_error(
            outcome,
            context.compilation.database_path,
            "C++ unused-function translation-unit count exceeds the bounded limit",
        )
        outcome.mode = "error"
        return None

    prepared: list[_PreparedUnit] = []
    unit_keys: set[tuple[str, str]] = set()
    validated_sources: dict[str, str] = {}
    for unit in selected:
        if any(item.level == "error" for item in unit.diagnostics):
            codes = ", ".join(
                sorted({item.code for item in unit.diagnostics if item.level == "error"})
            )
            _append_error(
                outcome,
                unit.source,
                f"C++ unused-function translation unit has ingestion errors: {codes}",
            )
            outcome.mode = "error"
            return None
        if _CONFIGURATION_RE.fullmatch(unit.configuration) is None:
            _append_error(
                outcome,
                unit.source,
                "C++ unused-function context has no canonical configuration identity",
            )
            outcome.mode = "error"
            return None
        unit_key = (unit.source, unit.configuration)
        if unit_key in unit_keys:
            _append_error(
                outcome,
                unit.source,
                "C++ unused-function context repeats a source configuration",
            )
            outcome.mode = "error"
            return None
        unit_keys.add(unit_key)
        if unit.source in validated_sources:
            current_text = validated_sources[unit.source]
        else:
            expected_text = source_texts.get(unit.source)
            try:
                current_text = _source_text(root, unit.source)
            except ValueError:
                current_text = ""
            if expected_text is None or current_text != expected_text:
                _append_error(
                    outcome,
                    unit.source,
                    "C++ unused-function source snapshot changed before analysis",
                )
                outcome.mode = "error"
                return None
            validated_sources[unit.source] = current_text
        try:
            replay = build_replay_command(
                root,
                unit,
                context.capabilities,
                operation="unused-functions",
            )
        except ReplayCommandError as err:
            message = f"C++ unused-function replay {err.code}: {unit.source}: {err}"
            if err.code in {"compiler-unavailable", "compiler-not-probed"}:
                outcome.warnings.append(message)
                return None
            _append_error(outcome, unit.source, message)
            outcome.mode = "error"
            return None
        capability = compiler_capability(replay.argv[0], context.capabilities)
        executable = regular_executable(root, capability)
        try:
            identity = _executable_identity(executable) if executable is not None else None
        except OSError:
            identity = None
        if capability is None or executable is None or identity is None:
            outcome.warnings.append(
                f"Approved compiler is unavailable for C++ unused-function analysis: {unit.source}"
            )
            return None
        command = compiler_diagnostic_command(list(replay.argv), context.capabilities)
        prepared.append(
            _PreparedUnit(
                unit=unit,
                replay=replay,
                command=tuple(command),
                capability=capability,
                executable=executable,
                executable_identity=identity,
                source_text=current_text,
            )
        )
    return root, tuple(prepared)


def _identity_matches(project_root: Path, prepared: _PreparedUnit) -> bool:
    current = regular_executable(project_root, prepared.capability)
    if current != prepared.executable:
        return False
    try:
        return _executable_identity(current) == prepared.executable_identity
    except OSError:
        return False


def _located_unused_diagnostics(
    prepared: _PreparedUnit,
    diagnostics: tuple[CppDiagnostic, ...],
    outcome: CppUnusedFunctionOutcome,
) -> tuple[tuple[_DiagnosticKey, str], ...]:
    located: list[tuple[_DiagnosticKey, str]] = []
    seen: set[_DiagnosticKey] = set()
    source_lines = prepared.source_text.splitlines()
    for diagnostic in diagnostics:
        target = diagnostic.target
        if diagnostic.tool_rule_id != _UNUSED_RULE:
            continue
        if target.status != EngineStatus.WARN or not target.message.startswith("warning: "):
            continue
        if target.file_path != prepared.unit.source:
            outcome.non_tu_diagnostics_excluded += 1
            continue
        if target.start_line > len(source_lines):
            raise ValueError("unused-function diagnostic line is outside the source snapshot")
        if target.start_column is not None:
            line_bytes = len(source_lines[target.start_line - 1].encode("utf-8"))
            if target.start_column > line_bytes + 1:
                raise ValueError("unused-function diagnostic column is outside the source line")
        if target.end_line is not None and target.end_line > len(source_lines):
            raise ValueError("unused-function diagnostic end line is outside the source snapshot")
        if target.end_line is not None and target.end_column is not None:
            end_line_bytes = len(source_lines[target.end_line - 1].encode("utf-8"))
            if target.end_column > end_line_bytes + 1:
                raise ValueError("unused-function diagnostic end column is outside the source line")
        key = (
            target.file_path,
            target.start_line,
            target.start_column,
            target.end_line,
            target.end_column,
        )
        if key in seen:
            raise ValueError("unused-function output repeats a source location")
        located.append((key, target.message))
        seen.add(key)
    return tuple(
        sorted(
            located,
            key=lambda item: (
                item[0][0],
                item[0][1],
                item[0][2] or 0,
                item[0][3] or item[0][1],
                item[0][4] or 0,
            ),
        )
    )


def _run_unit(
    root: Path,
    prepared: _PreparedUnit,
    outcome: CppUnusedFunctionOutcome,
    runner: Callable[..., ProcessResult],
    timeout: float,
) -> _Observation | None:
    if not _identity_matches(root, prepared):
        _append_error(
            outcome,
            prepared.unit.source,
            "C++ unused-function compiler identity changed before execution",
        )
        return None
    try:
        if _source_text(root, prepared.unit.source) != prepared.source_text:
            raise ValueError
    except ValueError:
        _append_error(
            outcome,
            prepared.unit.source,
            "C++ unused-function source changed before execution",
        )
        return None
    command = list(prepared.command)
    try:
        result = runner(
            command,
            cwd=prepared.replay.cwd,
            env=replay_environment(),
            input_text="",
            replace_env=True,
            timeout=timeout,
            max_output_chars=_MAX_OUTPUT_CHARS,
        )
    except Exception as exc:
        message = (
            f"C++ unused-function compiler could not execute: "
            f"{prepared.unit.source}: {type(exc).__name__}"
        )
        outcome.evidence.append(
            ToolEvidence(
                name=f"{prepared.capability.name} unused-function",
                path=command[0],
                version=prepared.capability.version,
                argv=command,
                error=message,
            )
        )
        _append_error(outcome, prepared.unit.source, message)
        return None
    evidence = ToolEvidence(
        name=f"{prepared.capability.name} unused-function",
        path=command[0],
        version=prepared.capability.version,
        argv=command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
    )
    outcome.evidence.append(evidence)
    failure = ""
    if result.timed_out:
        failure = "compiler timed out"
    elif result.truncated:
        failure = "compiler diagnostic output was truncated"
    elif not isinstance(result.returncode, int) or result.returncode < 0:
        failure = "compiler terminated unexpectedly"
    parsed = parse_compiler_diagnostics(root, prepared.replay.cwd, result.stdout, result.stderr)
    if not failure and parsed.error:
        failure = "compiler diagnostic output was not parseable"
    elif not failure and result.returncode != 0:
        failure = f"compiler exited with code {result.returncode}"
    elif not failure and any(
        item.target.status == EngineStatus.FAIL for item in parsed.diagnostics
    ):
        failure = "compiler reported an error with a successful exit"
    if not failure and not _identity_matches(root, prepared):
        failure = "compiler identity changed during execution"
    try:
        source_changed = _source_text(root, prepared.unit.source) != prepared.source_text
    except ValueError:
        source_changed = True
    if not failure and source_changed:
        failure = "source changed during compiler analysis"
    if failure:
        message = f"C++ unused-function {failure}: {prepared.unit.source}"
        evidence.error = message
        _append_error(outcome, prepared.unit.source, message)
        return None
    try:
        diagnostics = _located_unused_diagnostics(prepared, parsed.diagnostics, outcome)
    except ValueError as err:
        message = f"C++ unused-function output rejected: {prepared.unit.source}: {err}"
        evidence.error = message
        _append_error(outcome, prepared.unit.source, message)
        return None
    return _Observation(
        source=prepared.unit.source,
        configuration=prepared.unit.configuration,
        diagnostics=diagnostics,
        tool_name=prepared.capability.name,
        tool_version=prepared.capability.version,
    )


def _merge_observations(
    observations: list[_Observation],
    outcome: CppUnusedFunctionOutcome,
) -> None:
    by_source: dict[str, list[_Observation]] = {}
    for observation in observations:
        by_source.setdefault(observation.source, []).append(observation)
    accepted_targets: list[InspectionTarget] = []
    accepted_functions: list[CppUnusedFunction] = []
    for source, rows in sorted(by_source.items()):
        configurations = tuple(sorted(row.configuration for row in rows))
        diagnostic_sets = {tuple(item[0] for item in row.diagnostics) for row in rows}
        if len(diagnostic_sets) != 1:
            _append_error(
                outcome,
                source,
                "C++ unused-function diagnostics vary across source configurations",
            )
            continue
        diagnostics = next(iter(diagnostic_sets))
        tool_names = tuple(sorted({row.tool_name for row in rows}))
        tool_versions = tuple(sorted({row.tool_version for row in rows if row.tool_version}))
        if not diagnostics:
            accepted_targets.append(
                InspectionTarget(
                    file_path=source,
                    start_line=1,
                    target_name="C++UnusedFunctions",
                    status=EngineStatus.PASS,
                    message=(
                        "Compiler found no unused internal-linkage function in "
                        f"{len(configurations)} analyzed configuration(s)"
                    ),
                    metrics={"configurations_checked": len(configurations)},
                )
            )
            continue
        for file_path, line, column, end_line, end_column in diagnostics:
            messages = sorted(
                {
                    message
                    for row in rows
                    for key, message in row.diagnostics
                    if key == (file_path, line, column, end_line, end_column)
                }
            )
            diagnostic_message = messages[0] if messages else ""
            detail = diagnostic_message.removeprefix("warning: ")
            target = InspectionTarget(
                file_path=file_path,
                start_line=line,
                end_line=end_line,
                start_column=column,
                end_column=end_column,
                target_name="Compiler:-Wunused-function",
                status=EngineStatus.WARN,
                message=(
                    f"Compiler reports {detail} in all "
                    f"{len(configurations)} analyzed configuration(s)"
                ),
                metrics={"configurations_checked": len(configurations)},
            )
            accepted_targets.append(target)
            accepted_functions.append(
                CppUnusedFunction(
                    target=target,
                    configurations=configurations,
                    tool_names=tool_names,
                    tool_versions=tool_versions,
                    diagnostic_message=diagnostic_message,
                )
            )
    if outcome.errors:
        return
    outcome.targets.extend(accepted_targets)
    outcome.functions.extend(accepted_functions)


def run_cpp_unused_functions(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext | None,
    *,
    source_texts: dict[str, str],
    runner: Callable[..., ProcessResult],
) -> CppUnusedFunctionOutcome:
    """Return exact TU-local unused-function diagnostics without project writes."""

    outcome = CppUnusedFunctionOutcome()
    prepared_result = _prepare_units(
        project_root,
        cpp_files,
        context,
        source_texts,
        outcome,
    )
    if prepared_result is None:
        return outcome
    root, prepared_units = prepared_result
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    observations: list[_Observation] = []
    checked_sources: set[str] = set()
    for index, prepared in enumerate(prepared_units):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _append_error(
                outcome,
                prepared.unit.source,
                "C++ unused-function global budget left "
                f"{len(prepared_units) - index} configuration(s) unexamined",
            )
            break
        observation = _run_unit(
            root,
            prepared,
            outcome,
            runner,
            min(_UNIT_TIMEOUT_SECONDS, remaining),
        )
        if observation is not None:
            observations.append(observation)
            checked_sources.add(observation.source)
            outcome.configurations_checked += 1
    outcome.sources_checked = len(checked_sources)
    if outcome.errors or len(observations) != len(prepared_units):
        outcome.mode = "error"
        outcome.functions.clear()
        return outcome
    _merge_observations(observations, outcome)
    if outcome.errors:
        outcome.mode = "error"
        outcome.functions.clear()
        return outcome
    outcome.mode = "exact"
    return outcome
