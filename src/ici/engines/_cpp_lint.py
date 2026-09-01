"""C++ syntax analysis backed by exact translation-unit commands."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    CompilationContext,
    CompilationDiagnostic,
    CompilationUnit,
)
from ici.core.cpp_replay import (
    ReplayCommandError,
    build_replay_command,
    compilation_context_present,
    replay_environment,
)
from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_diagnostics import CppDiagnostic, parse_compiler_diagnostics

_MAX_SELECTED_UNITS = 2_048
_TIMEOUT_SECONDS = 120.0
_GLOBAL_TIMEOUT_SECONDS = 600.0
_OUTPUT_CHARS = 1_000_000


@dataclass
class CppLintOutcome:
    """Mutable execution accumulator returned to the lint facade."""

    targets: list[InspectionTarget]
    evidence: list[ToolEvidence]
    errors: list[str]
    warnings: list[str]
    mode: str
    diagnostics: list[CppDiagnostic] = field(default_factory=list)
    configurations_checked: int = 0
    sources_checked: int = 0
    missing_sources: int = 0


def _record_process(
    evidence: list[ToolEvidence],
    name: str,
    command: list[str],
    result: ProcessResult,
    version: str,
) -> ToolEvidence:
    error = ""
    if result.timed_out:
        error = "timed out"
    elif result.truncated:
        error = "output truncated"
    elif not isinstance(result.returncode, int) or result.returncode < 0:
        error = "process failed to start or terminated by signal"
    item = ToolEvidence(
        name=name,
        path=command[0],
        version=version,
        argv=command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
        error=error,
    )
    evidence.append(item)
    return item


def _record_exception(
    evidence: list[ToolEvidence], name: str, command: list[str], exc: Exception
) -> None:
    evidence.append(
        ToolEvidence(
            name=name,
            path=command[0],
            argv=command,
            error=f"{type(exc).__name__}: {exc}",
        )
    )


def _compiler_capability(
    command: list[str], inventory: CapabilityInventory
) -> ToolCapability | None:
    compiler = Path(command[0]).resolve(strict=False)
    for capability in inventory.capabilities.values():
        if not capability.path:
            continue
        try:
            if Path(capability.path).resolve(strict=False) == compiler:
                return capability
        except (OSError, RuntimeError):
            continue
    return None


def _diagnostic_command(command: list[str], inventory: CapabilityInventory) -> list[str]:
    """Request structured GCC diagnostics or bounded text fix-its when supported."""

    compiler = Path(command[0]).resolve(strict=False)
    gcc_json = False
    for name in ("gcc", "g++"):
        capability = inventory.capabilities.get(name)
        if capability is None or capability.version_tuple < (9,) or not capability.path:
            continue
        try:
            gcc_json = Path(capability.path).resolve(strict=False) == compiler
        except (OSError, RuntimeError):
            gcc_json = False
        if gcc_json:
            break
    diagnostic_flag = "-fdiagnostics-format=json" if gcc_json else "-fdiagnostics-parseable-fixits"
    return [*command[:-1], diagnostic_flag, command[-1]]


def parse_cpp_diagnostics(
    project_root: Path, cwd: Path, stdout: str, stderr: str
) -> tuple[list[InspectionTarget], bool, bool]:
    """Compatibility facade for the normalized atomic diagnostic parser."""

    result = parse_compiler_diagnostics(project_root, cwd, stdout, stderr)
    targets = [diagnostic.target for diagnostic in result.diagnostics]
    return targets, bool(result.error), bool(result.diagnostics)


def _syntax_process_error(result: ProcessResult, source_name: str) -> str | None:
    if result.timed_out:
        return f"C++ syntax check timed out: {source_name}"
    if result.truncated:
        return f"C++ syntax output was truncated: {source_name}"
    if not isinstance(result.returncode, int) or result.returncode < 0:
        return f"C++ syntax check terminated unexpectedly: {source_name}"
    return None


def _syntax_output_error(
    project_root: Path,
    cwd: Path,
    result: ProcessResult,
    source_name: str,
    compiler_name: str,
) -> tuple[list[CppDiagnostic], str | None]:
    parsed = parse_compiler_diagnostics(project_root, cwd, result.stdout, result.stderr)
    if parsed.error:
        return [], f"C++ syntax output was not parseable: {source_name}"
    if result.returncode >= 2:
        return [], f"{compiler_name} failed with exit code {result.returncode}: {source_name}"
    if result.returncode != 0 and not parsed.diagnostics:
        return [], f"C++ syntax output had no diagnostics: {source_name}"
    has_error = any(
        diagnostic.target.status == EngineStatus.FAIL for diagnostic in parsed.diagnostics
    )
    if result.returncode == 0 and has_error:
        return [], f"{compiler_name} reported an error with a successful exit: {source_name}"
    if result.returncode == 1 and not has_error:
        return [], f"{compiler_name} failed without an error diagnostic: {source_name}"
    return list(parsed.diagnostics), None


def _evaluate_process(
    project_root: Path,
    source: Path,
    cwd: Path,
    configuration: str,
    name: str,
    result: ProcessResult,
    tool_record: ToolEvidence,
    outcome: CppLintOutcome,
) -> None:
    source_name = source.name
    message = _syntax_process_error(result, source_name)
    parsed: list[CppDiagnostic] = []
    if message is None:
        parsed, message = _syntax_output_error(project_root, cwd, result, source_name, name)
        outcome.diagnostics.extend(parsed)
        outcome.targets.extend(diagnostic.target for diagnostic in parsed)
    if message is None:
        if not parsed:
            relative = str(source.relative_to(project_root))
            suffix = configuration.removeprefix("sha256:")[:12] or "fallback"
            outcome.targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name=f"C++Syntax:{suffix}",
                    status=EngineStatus.PASS,
                    message="Compiler syntax analysis completed without diagnostics.",
                )
            )
        return
    outcome.targets.append(
        InspectionTarget(
            file_path=str(source.relative_to(project_root)),
            start_line=1,
            target_name="C++SyntaxExecutionError",
            status=EngineStatus.ERROR,
            message=message,
        )
    )
    tool_record.error = message
    outcome.errors.append(message)


def _run_command(
    project_root: Path,
    source: Path,
    configuration: str,
    name: str,
    command: list[str],
    cwd: Path,
    version: str,
    outcome: CppLintOutcome,
    runner: Callable[..., ProcessResult],
    timeout: float,
) -> None:
    try:
        result = runner(
            command,
            cwd=cwd,
            env=replay_environment(),
            input_text="",
            replace_env=True,
            timeout=timeout,
            max_output_chars=_OUTPUT_CHARS,
        )
    except Exception as exc:
        _record_exception(outcome.evidence, name, command, exc)
        message = f"C++ syntax check could not execute: {source.name}"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=str(source.relative_to(project_root)),
                start_line=1,
                target_name="C++SyntaxExecutionError",
                status=EngineStatus.ERROR,
                message=f"{message}: {type(exc).__name__}",
            )
        )
        return
    tool_record = _record_process(outcome.evidence, name, command, result, version)
    _evaluate_process(
        project_root,
        source,
        cwd,
        configuration,
        name,
        result,
        tool_record,
        outcome,
    )


def _context_has_errors(context: CompilationContext) -> bool:
    return any(item.level == "error" for item in context.diagnostics)


def _context_diagnostic_target(
    diagnostic: CompilationDiagnostic,
    fallback_path: str,
) -> InspectionTarget:
    level = diagnostic.level
    status = (
        EngineStatus.ERROR
        if level == "error"
        else EngineStatus.WARN
        if level == "warning"
        else EngineStatus.PASS
    )
    return InspectionTarget(
        file_path=diagnostic.source or fallback_path,
        start_line=1,
        target_name="C++SyntaxContextDiagnostic",
        status=status,
        message=f"{diagnostic.code}: {diagnostic.message}",
    )


def _selected_units(
    project_root: Path,
    cpp_files: list[Path],
    context: CompilationContext,
) -> tuple[list[CompilationUnit], list[str]]:
    sources = {str(path.relative_to(project_root)).replace("\\", "/") for path in cpp_files}
    by_source = {unit.source for unit in context.units}
    if context.unity_build:
        selected = [unit for unit in context.units if unit.language in {"c", "c++"}]
        return selected, []
    selected = [unit for unit in context.units if unit.source in sources]
    return selected, sorted(sources - by_source)


def _run_exact(
    project_root: Path,
    cpp_files: list[Path],
    analysis_context: AnalysisContext,
    outcome: CppLintOutcome,
    runner: Callable[..., ProcessResult],
) -> None:
    context = analysis_context.compilation
    outcome.targets.extend(
        _context_diagnostic_target(diagnostic, context.database_path or ".")
        for diagnostic in context.diagnostics
    )
    if _context_has_errors(context):
        outcome.errors.append("Compilation context contains error diagnostics")
        return
    selected, missing = _selected_units(project_root, cpp_files, context)
    outcome.missing_sources = len(missing)
    for source in missing:
        outcome.targets.append(
            InspectionTarget(
                file_path=source,
                start_line=1,
                target_name="C++SyntaxContextMissing",
                status=EngineStatus.WARN,
                message="No exact translation-unit command covers this production source.",
            )
        )
    if missing:
        outcome.errors.append(
            f"Compilation context is missing {len(missing)} production source command(s)"
        )
    if not selected:
        message = "Compilation context contains no replayable production units"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=context.database_path or ".",
                start_line=1,
                target_name="C++SyntaxContextError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        return
    if len(selected) > _MAX_SELECTED_UNITS:
        message = "C++ syntax translation-unit count exceeds the bounded limit"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=context.database_path or ".",
                start_line=1,
                target_name="C++SyntaxBudgetError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        return

    checked_sources: set[str] = set()
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    for index, unit in enumerate(selected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            unexamined = len(selected) - index
            message = f"C++ syntax global time budget left {unexamined} unit(s) unexamined"
            outcome.errors.append(message)
            outcome.targets.append(
                InspectionTarget(
                    file_path=unit.source,
                    start_line=1,
                    target_name="C++SyntaxBudgetError",
                    status=EngineStatus.ERROR,
                    message=message,
                )
            )
            break
        outcome.targets.extend(
            _context_diagnostic_target(diagnostic, unit.source) for diagnostic in unit.diagnostics
        )
        if any(item.level == "error" for item in unit.diagnostics):
            outcome.errors.append(f"Compilation unit has error diagnostics: {unit.source}")
            continue
        try:
            replay = build_replay_command(
                project_root,
                unit,
                analysis_context.capabilities,
                operation="syntax",
            )
        except ReplayCommandError as err:
            outcome.errors.append(f"C++ replay {err.code}: {unit.source}: {err}")
            outcome.targets.append(
                InspectionTarget(
                    file_path=unit.source,
                    start_line=1,
                    target_name="C++SyntaxReplayError",
                    status=EngineStatus.ERROR,
                    message=f"{err.code}: {err}",
                )
            )
            continue
        command = _diagnostic_command(list(replay.argv), analysis_context.capabilities)
        capability = _compiler_capability(command, analysis_context.capabilities)
        _run_command(
            project_root,
            replay.source,
            replay.configuration,
            replay.compiler,
            command,
            replay.cwd,
            capability.version if capability is not None else "",
            outcome,
            runner,
            min(_TIMEOUT_SECONDS, remaining),
        )
        outcome.configurations_checked += 1
        checked_sources.add(unit.source)
    outcome.sources_checked = len(checked_sources)


def _run_fallback(
    project_root: Path,
    cpp_files: list[Path],
    include_flags: list[str],
    analysis_context: AnalysisContext | None,
    outcome: CppLintOutcome,
    runner: Callable[..., ProcessResult],
    which: Callable[[str], str | None],
) -> None:
    inventory = analysis_context.capabilities if analysis_context is not None else None
    capability = inventory.capabilities.get("g++") if inventory is not None else None
    gxx: str | None
    if capability is not None:
        gxx = capability.path
        compiler_ready = capability.available and capability.complete and bool(gxx)
    else:
        gxx = which("g++")
        compiler_ready = bool(gxx) and inventory is None
    if not compiler_ready or not gxx:
        outcome.evidence.append(
            ToolEvidence(name="g++", path="", error="tool not found; no command was executed")
        )
        outcome.errors.append("g++ is required when C++ sources are present without build context")
        return
    if inventory is None:
        if Path(gxx).name.casefold() not in {"g++", "g++.exe"}:
            outcome.evidence.append(
                ToolEvidence(
                    name="g++",
                    path=gxx,
                    error="non-canonical fallback driver; no command was executed",
                )
            )
            outcome.errors.append("C++ fallback rejected a non-canonical g++ driver")
            return
        inventory = CapabilityInventory(
            capabilities={
                "g++": ToolCapability(
                    name="g++",
                    path=gxx,
                    available=True,
                    complete=True,
                )
            }
        )
    outcome.warnings.append(
        "No compilation database is available; C++ syntax analysis used the c++17 heuristic fallback"
    )
    checked_sources = 0
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    for index, cpp in enumerate(cpp_files):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            unexamined = len(cpp_files) - index
            message = f"C++ syntax global time budget left {unexamined} unit(s) unexamined"
            outcome.errors.append(message)
            outcome.targets.append(
                InspectionTarget(
                    file_path=str(cpp.relative_to(project_root)),
                    start_line=1,
                    target_name="C++SyntaxBudgetError",
                    status=EngineStatus.ERROR,
                    message=message,
                )
            )
            break
        try:
            relative = cpp.relative_to(project_root).as_posix()
        except ValueError:
            outcome.errors.append(f"C++ fallback source is outside the project: {cpp.name}")
            outcome.targets.append(
                InspectionTarget(
                    file_path=str(cpp),
                    start_line=1,
                    target_name="C++SyntaxReplayError",
                    status=EngineStatus.ERROR,
                    message="unsafe-source: fallback source is outside the project",
                )
            )
            continue
        unit = CompilationUnit(
            source=relative,
            directory=".",
            argv=(gxx, "-std=c++17", *include_flags, relative),
            compiler="g++",
            language="c++",
            standard="c++17",
        )
        try:
            replay = build_replay_command(
                project_root,
                unit,
                inventory,
                operation="syntax",
            )
        except ReplayCommandError as err:
            outcome.errors.append(f"C++ fallback {err.code}: {relative}: {err}")
            outcome.targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name="C++SyntaxReplayError",
                    status=EngineStatus.ERROR,
                    message=f"{err.code}: {err}",
                )
            )
            continue
        command = _diagnostic_command(list(replay.argv), inventory)
        capability = _compiler_capability(command, inventory)
        _run_command(
            project_root,
            replay.source,
            "",
            "g++",
            command,
            replay.cwd,
            capability.version if capability is not None else "",
            outcome,
            runner,
            min(_TIMEOUT_SECONDS, remaining),
        )
        outcome.configurations_checked += 1
        checked_sources += 1
    outcome.sources_checked = checked_sources


def run_cpp_lint(
    project_root: Path,
    cpp_files: list[Path],
    analysis_context: AnalysisContext | None,
    include_flags: list[str],
    *,
    runner: Callable[..., ProcessResult],
    which: Callable[[str], str | None],
) -> CppLintOutcome:
    """Use exact commands when context exists; fallback only when it is absent."""

    context = analysis_context.compilation if analysis_context is not None else CompilationContext()
    exact = compilation_context_present(context)
    outcome = CppLintOutcome([], [], [], [], "exact" if exact else "heuristic")
    try:
        project_root = project_root.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        message = f"C++ project root could not be resolved: {type(err).__name__}"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="C++SyntaxContextError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        return outcome
    normalized_files: list[Path] = []
    for source in cpp_files:
        safe_path = "."
        try:
            lexical = (source if source.is_absolute() else project_root / source).absolute()
            relative = lexical.relative_to(project_root).as_posix()
            safe_path = relative
            resolved = lexical.resolve(strict=False)
            resolved.relative_to(project_root)
        except (OSError, RuntimeError, ValueError):
            message = f"C++ source is outside the project or unreadable: {source.name}"
            outcome.errors.append(message)
            outcome.targets.append(
                InspectionTarget(
                    file_path=safe_path,
                    start_line=1,
                    target_name="C++SyntaxContextError",
                    status=EngineStatus.ERROR,
                    message=message,
                )
            )
            continue
        if not resolved.exists():
            normalized_files.append(resolved)
            continue
        try:
            details = resolved.stat()
        except OSError:
            details = None
        if details is None or not stat.S_ISREG(details.st_mode) or not os.access(resolved, os.R_OK):
            message = f"C++ source is not a readable regular file: {source.name}"
            outcome.errors.append(message)
            outcome.targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name="C++SyntaxContextError",
                    status=EngineStatus.ERROR,
                    message=message,
                )
            )
            continue
        normalized_files.append(resolved)
    if outcome.errors:
        return outcome
    if len(normalized_files) > _MAX_SELECTED_UNITS:
        message = "C++ source count exceeds the bounded limit"
        outcome.errors.append(message)
        outcome.targets.append(
            InspectionTarget(
                file_path=".",
                start_line=1,
                target_name="C++SyntaxBudgetError",
                status=EngineStatus.ERROR,
                message=message,
            )
        )
        return outcome
    if exact:
        assert analysis_context is not None
        _run_exact(project_root, normalized_files, analysis_context, outcome, runner)
    else:
        _run_fallback(
            project_root,
            normalized_files,
            include_flags,
            analysis_context,
            outcome,
            runner,
            which,
        )
    return outcome
