"""Compiler-measured C++ include graph construction."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ici.core.context import AnalysisContext, CompilationDiagnostic, CompilationUnit
from ici.core.cpp_replay import (
    ReplayCommandError,
    build_replay_command,
    replay_environment,
)
from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult
from ici.engines._cpp_include_trace import (
    parse_include_trace,
    parse_missing_include_targets,
)

_THIRD_PARTY_PARTS = frozenset(
    {"third_party", "third-party", "vendor", "external", "extern", "deps", "_deps"}
)


@dataclass
class CompilerGraphOutcome:
    """Exact graph observations and failures for the cycle engine."""

    graph: dict[Path, set[Path]]
    known: dict[Path, Path]
    targets: list[InspectionTarget] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scope_counts: Counter[str] = field(default_factory=Counter)
    configuration_graphs: list[tuple[str, dict[Path, set[Path]]]] = field(default_factory=list)
    configurations_checked: int = 0
    resolved_edges: int = 0
    unresolved_edges: int = 0


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _selected_units(
    root: Path,
    cpp_sources: list[Path],
    context: AnalysisContext,
) -> tuple[list[CompilationUnit], list[str]]:
    expected = {path.relative_to(root).as_posix() for path in cpp_sources}
    compilation = context.compilation
    if compilation.unity_build:
        return (
            [unit for unit in compilation.units if unit.language in {"c", "c++"}],
            [],
        )
    available = {unit.source for unit in compilation.units}
    return (
        [unit for unit in compilation.units if unit.source in expected],
        sorted(expected - available),
    )


def _include_roots(root: Path, unit: CompilationUnit) -> tuple[tuple[Path, str], ...]:
    values: list[tuple[Path, str]] = []
    for search in unit.include_paths:
        lexical = (
            root / PurePosixPath(search.path) if search.scope == "project" else Path(search.path)
        )
        try:
            resolved = lexical.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        values.append((resolved, "system" if search.kind == "system" else "third_party"))
    return tuple(values)


def _scope_for_header(
    path: Path,
    *,
    root: Path,
    known: dict[Path, Path],
    include_roots: tuple[tuple[Path, str], ...],
) -> str:
    if _is_within(path, root):
        relative = path.relative_to(root)
        if any(part.casefold() in _THIRD_PARTY_PARTS for part in relative.parts):
            return "third_party"
        if path in known:
            return "project"
        return "generated"
    for include_root, scope in include_roots:
        if _is_within(path, include_root):
            return scope
    return "system"


def _record_process(
    outcome: CompilerGraphOutcome,
    command: list[str],
    result: ProcessResult,
) -> ToolEvidence:
    error = ""
    if result.timed_out:
        error = "timed out"
    elif result.truncated:
        error = "output truncated"
    elif not isinstance(result.returncode, int) or result.returncode < 0:
        error = "process failed to start or terminated by signal"
    item = ToolEvidence(
        name="compiler include trace",
        path=command[0],
        argv=command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
        error=error,
    )
    outcome.evidence.append(item)
    return item


def _context_target(
    diagnostic: CompilationDiagnostic,
    fallback_path: str,
) -> InspectionTarget:
    status = (
        EngineStatus.ERROR
        if diagnostic.level == "error"
        else EngineStatus.WARN
        if diagnostic.level == "warning"
        else EngineStatus.PASS
    )
    return InspectionTarget(
        file_path=diagnostic.source or fallback_path,
        start_line=1,
        target_name="CppIncludeContextDiagnostic",
        status=status,
        message=f"{diagnostic.code}: {diagnostic.message}",
    )


def _trace_process_error(unit: CompilationUnit, result: ProcessResult) -> str | None:
    if result.timed_out:
        return f"Compiler include trace timed out: {unit.source}"
    if result.truncated:
        return f"Compiler include trace was truncated: {unit.source}"
    if not isinstance(result.returncode, int) or result.returncode < 0:
        return f"Compiler include trace terminated unexpectedly: {unit.source}"
    if result.stdout.strip():
        return f"Compiler include trace emitted unexpected stdout: {unit.source}"
    return None


def _consume_successful_trace(
    root: Path,
    unit: CompilationUnit,
    replay_source: Path,
    replay_cwd: Path,
    stderr: str,
    outcome: CompilerGraphOutcome,
) -> str | None:
    try:
        edges, unexpected = parse_include_trace(
            stderr,
            cwd=replay_cwd,
            source=replay_source,
        )
    except ValueError as err:
        return f"Compiler include trace was not parseable: {unit.source}: {err}"
    if unexpected:
        return f"Compiler include trace contained unexpected diagnostics: {unit.source}"

    include_roots = _include_roots(root, unit)
    seen: set[tuple[Path, Path]] = set()
    configuration_graph: dict[Path, set[Path]] = {}
    for parent, child in edges:
        edge = (parent, child)
        if parent == child or edge in seen:
            continue
        seen.add(edge)
        outcome.scope_counts[
            _scope_for_header(
                child,
                root=root,
                known=outcome.known,
                include_roots=include_roots,
            )
        ] += 1
        if parent in outcome.graph and child in outcome.graph:
            outcome.graph[parent].add(child)
            configuration_graph.setdefault(parent, set()).add(child)
            configuration_graph.setdefault(child, set())
    outcome.resolved_edges += len(seen)
    for configuration, graph in outcome.configuration_graphs:
        if configuration != unit.configuration:
            continue
        for parent, children in configuration_graph.items():
            graph.setdefault(parent, set()).update(children)
        break
    else:
        outcome.configuration_graphs.append((unit.configuration, configuration_graph))
    outcome.targets.append(
        InspectionTarget(
            file_path=unit.source,
            start_line=1,
            target_name="CppIncludeTrace",
            status=EngineStatus.PASS,
            message="Compiler include tracing completed for this translation-unit configuration.",
            metrics={
                "configuration": unit.configuration,
                "resolved_edges": len(seen),
                "resolution": "compiler_trace",
            },
        )
    )
    return None


def _evaluate_trace(
    root: Path,
    unit: CompilationUnit,
    replay_source: Path,
    replay_cwd: Path,
    result: ProcessResult,
    record: ToolEvidence,
    outcome: CompilerGraphOutcome,
) -> None:
    message = _trace_process_error(unit, result)
    if message is None:
        missing = parse_missing_include_targets(
            root,
            replay_cwd,
            replay_source,
            result.stderr,
        )
        if result.returncode != 0 and missing:
            outcome.targets.extend(missing)
            outcome.unresolved_edges += len(missing)
            return
        if result.returncode != 0:
            message = (
                f"Compiler include trace failed with exit code {result.returncode}: {unit.source}"
            )
        else:
            message = _consume_successful_trace(
                root,
                unit,
                replay_source,
                replay_cwd,
                result.stderr,
                outcome,
            )
            if message is None:
                return
    outcome.targets.append(
        InspectionTarget(
            file_path=unit.source,
            start_line=1,
            target_name="CppIncludeTraceError",
            status=EngineStatus.ERROR,
            message=message,
        )
    )
    record.error = message
    outcome.errors.append(message)


def build_compiler_cpp_graph(
    root: Path,
    cpp_sources: list[Path],
    cpp_files: list[Path],
    context: AnalysisContext,
    *,
    runner: Callable[..., ProcessResult],
) -> CompilerGraphOutcome:
    """Run bounded include traces for every production TU configuration."""

    project_root = root.resolve(strict=True)
    outcome = CompilerGraphOutcome(graph={}, known={})
    for path in cpp_files:
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as err:
            message = f"C++ include input is stale or unreadable: {path.name}: {err}"
        else:
            if resolved.is_file() and _is_within(resolved, project_root):
                outcome.known[resolved] = path
                outcome.graph[resolved] = set()
                continue
            message = f"C++ include input is outside the project or not a regular file: {path.name}"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=_relative_or_absolute(project_root, path),
                start_line=1,
                target_name="CppIncludeContextError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
    compilation = context.compilation
    if any(item.level == "error" for item in compilation.diagnostics):
        outcome.errors.append("Compilation context contains error diagnostics")
    outcome.targets.extend(
        _context_target(diagnostic, compilation.database_path or ".")
        for diagnostic in compilation.diagnostics
    )
    units, missing = _selected_units(project_root, cpp_sources, context)
    for source in missing:
        outcome.targets.append(
            InspectionTarget(
                file_path=source,
                start_line=1,
                target_name="CppIncludeContextMissing",
                status=EngineStatus.WARN,
                message="No exact translation-unit command covers this production source.",
            )
        )
    if missing:
        outcome.errors.append(
            f"Compilation context is missing {len(missing)} production source command(s)"
        )
    if not units:
        message = "Compilation context contains no include-trace production units"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=compilation.database_path or ".",
                start_line=1,
                target_name="CppIncludeContextError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        return outcome

    for unit in units:
        outcome.targets.extend(
            _context_target(diagnostic, unit.source) for diagnostic in unit.diagnostics
        )
        if any(item.level == "error" for item in unit.diagnostics):
            outcome.errors.append(f"Compilation unit has error diagnostics: {unit.source}")
            continue
        try:
            replay = build_replay_command(
                project_root,
                unit,
                context.capabilities,
                operation="includes",
            )
        except ReplayCommandError as err:
            outcome.errors.append(f"C++ replay {err.code}: {unit.source}: {err}")
            outcome.targets.append(
                InspectionTarget(
                    file_path=unit.source,
                    start_line=1,
                    target_name="CppIncludeReplayError",
                    status=EngineStatus.ERROR,
                    message=f"{err.code}: {err}",
                )
            )
            continue
        command = list(replay.argv)
        outcome.configurations_checked += 1
        try:
            result = runner(
                command,
                cwd=replay.cwd,
                env=replay_environment(),
                input_text="",
                replace_env=True,
            )
        except Exception as exc:
            outcome.evidence.append(
                ToolEvidence(
                    name="compiler include trace",
                    path=command[0],
                    argv=command,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            outcome.errors.append(f"Compiler include trace could not execute: {unit.source}")
            outcome.targets.append(
                InspectionTarget(
                    file_path=unit.source,
                    start_line=1,
                    target_name="CppIncludeTraceError",
                    status=EngineStatus.ERROR,
                    message=f"Compiler include trace could not execute: {type(exc).__name__}",
                )
            )
            continue
        record = _record_process(outcome, command, result)
        _evaluate_trace(
            project_root,
            unit,
            replay.source,
            replay.cwd,
            result,
            record,
            outcome,
        )
    return outcome
