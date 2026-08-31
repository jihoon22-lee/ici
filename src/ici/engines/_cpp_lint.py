"""C++ syntax analysis backed by exact translation-unit commands."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
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

_CPP_DIAGNOSTIC_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*"
    r"(?P<kind>fatal error|error|warning|note):\s*(?P<message>\S.*)$"
)
_CPP_CONTEXT_RE = re.compile(r"^\s*(?:\d+\s*\|.*|\|.*|[\^~].*)$")
_CPP_CONTEXT_HEADER_RE = re.compile(
    r"^.+:\s+In (?:function|member function|constructor|destructor|lambda function|"
    r"instantiation of)(?: .*)?:$"
)
_CPP_REQUIRED_FROM_RE = re.compile(r"^.+:[1-9]\d*(?::[1-9]\d*)?:\s+required from here$")
_MAX_DIAGNOSTIC_LINE = 2_147_483_647


@dataclass
class CppLintOutcome:
    """Mutable execution accumulator returned to the lint facade."""

    targets: list[InspectionTarget]
    evidence: list[ToolEvidence]
    errors: list[str]
    warnings: list[str]
    mode: str
    configurations_checked: int = 0
    sources_checked: int = 0
    missing_sources: int = 0


def _record_process(
    evidence: list[ToolEvidence], name: str, command: list[str], result: ProcessResult
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


def _diagnostic_path(project_root: Path, cwd: Path, value: str) -> str | None:
    try:
        lexical = Path(value.strip())
        path = (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return str(path)


def _diagnostic_number(value: str | None) -> int | None:
    if value is None or len(value) > 10:
        return None
    try:
        number = int(value)
    except (ValueError, OverflowError):
        return None
    return number if 0 < number <= _MAX_DIAGNOSTIC_LINE else None


def parse_cpp_diagnostics(
    project_root: Path, cwd: Path, stdout: str, stderr: str
) -> tuple[list[InspectionTarget], bool, bool]:
    """Parse bounded GCC/Clang text diagnostics and reject unknown output."""

    parsed: list[InspectionTarget] = []
    malformed = False
    found_diagnostic = False
    for raw_line in (stdout + "\n" + stderr).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _CPP_DIAGNOSTIC_RE.match(line)
        if match:
            file_path = _diagnostic_path(project_root, cwd, match.group("file"))
            line_number = _diagnostic_number(match.group("line"))
            column_number = _diagnostic_number(match.group("column"))
            if (
                file_path is None
                or line_number is None
                or (match.group("column") is not None and column_number is None)
            ):
                malformed = True
                continue
            found_diagnostic = True
            kind = match.group("kind")
            parsed.append(
                InspectionTarget(
                    file_path=file_path,
                    start_line=line_number,
                    target_name="C++Syntax",
                    status=EngineStatus.FAIL if "error" in kind else EngineStatus.WARN,
                    message=f"{kind}: {match.group('message')}",
                )
            )
            continue
        if line.startswith("In file included from") or line.startswith("from "):
            continue
        if found_diagnostic and _CPP_CONTEXT_RE.fullmatch(line) is not None:
            continue
        if _CPP_CONTEXT_HEADER_RE.fullmatch(line) or _CPP_REQUIRED_FROM_RE.fullmatch(line):
            continue
        malformed = True
    return parsed, malformed, found_diagnostic


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
) -> tuple[list[InspectionTarget], str | None]:
    parsed, malformed, found = parse_cpp_diagnostics(
        project_root, cwd, result.stdout, result.stderr
    )
    if malformed:
        return parsed, f"C++ syntax output was not parseable: {source_name}"
    if result.returncode >= 2:
        return parsed, f"{compiler_name} failed with exit code {result.returncode}: {source_name}"
    if result.returncode != 0 and not found:
        return parsed, f"C++ syntax output had no diagnostics: {source_name}"
    return parsed, None


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
    parsed: list[InspectionTarget] = []
    if message is None:
        parsed, message = _syntax_output_error(project_root, cwd, result, source_name, name)
        outcome.targets.extend(parsed)
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
    outcome: CppLintOutcome,
    runner: Callable[..., ProcessResult],
) -> None:
    try:
        result = runner(
            command,
            cwd=cwd,
            env=replay_environment(),
            input_text="",
            replace_env=True,
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
    tool_record = _record_process(outcome.evidence, name, command, result)
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
    selected, missing = _selected_units(project_root, cpp_files, context)
    outcome.missing_sources = len(missing)
    if _context_has_errors(context):
        outcome.errors.append("Compilation context contains error diagnostics")
    outcome.targets.extend(
        _context_diagnostic_target(diagnostic, context.database_path or ".")
        for diagnostic in context.diagnostics
    )
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

    checked_sources: set[str] = set()
    for unit in selected:
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
        _run_command(
            project_root,
            replay.source,
            replay.configuration,
            replay.compiler,
            list(replay.argv),
            replay.cwd,
            outcome,
            runner,
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
    for cpp in cpp_files:
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
        _run_command(
            project_root,
            replay.source,
            "",
            "g++",
            list(replay.argv),
            replay.cwd,
            outcome,
            runner,
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
    if exact:
        assert analysis_context is not None
        _run_exact(project_root, cpp_files, analysis_context, outcome, runner)
    else:
        _run_fallback(
            project_root,
            cpp_files,
            include_flags,
            analysis_context,
            outcome,
            runner,
            which,
        )
    return outcome
