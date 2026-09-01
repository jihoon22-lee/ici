"""Read-only clazy execution over ici-approved compilation contexts."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.cpp_replay import ReplayCommandError, build_replay_command, replay_environment
from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_diagnostics import CppDiagnostic, parse_clazy_diagnostics
from ici.engines._cpp_tooling import regular_executable, selected_units, tooling_arguments

_MAX_SELECTED_UNITS = 2_048
_TIMEOUT_SECONDS = 120.0
_GLOBAL_TIMEOUT_SECONDS = 600.0
_OUTPUT_CHARS = 1_000_000
_PROVIDERS = {"clazy-standalone": "standalone", "clazy": "compiler-wrapper"}


@dataclass
class ClazyOutcome:
    """Mutable adapter result consumed by the lint facade."""

    targets: list[InspectionTarget] = field(default_factory=list)
    diagnostics: list[CppDiagnostic] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "off"
    provider: str = "none"
    profile: str = "level0"
    configurations_checked: int = 0
    sources_checked: int = 0


def _target(status: EngineStatus, name: str, message: str, path: str = ".") -> InspectionTarget:
    return InspectionTarget(
        file_path=path,
        start_line=1,
        target_name=name,
        status=status,
        message=message,
    )


def _problem(
    outcome: ClazyOutcome,
    status: EngineStatus,
    target_name: str,
    message: str,
    path: str = ".",
) -> None:
    outcome.targets.append(_target(status, target_name, message, path))
    (outcome.errors if status == EngineStatus.ERROR else outcome.warnings).append(message)
    if status == EngineStatus.ERROR:
        outcome.mode = "error"


def _unavailable(outcome: ClazyOutcome, message: str, *, required: bool) -> None:
    _problem(
        outcome,
        EngineStatus.ERROR if required else EngineStatus.WARN,
        "ClazyUnavailable",
        message,
    )
    if not required:
        outcome.mode = "unavailable"


def _checks(config: Mapping[str, Any]) -> str:
    values = config.get("clazy_checks")
    if isinstance(values, list):
        return ",".join(values)
    return str(config.get("clazy_profile", "level0"))


def _normalized_sources(
    project_root: Path,
    cpp_files: list[Path],
    outcome: ClazyOutcome,
) -> list[Path] | None:
    normalized: list[Path] = []
    for source in cpp_files:
        safe_path = "."
        try:
            lexical = (source if source.is_absolute() else project_root / source).absolute()
            relative = lexical.relative_to(project_root).as_posix()
            safe_path = relative
            resolved = lexical.resolve(strict=False)
            resolved.relative_to(project_root)
        except (OSError, RuntimeError, ValueError):
            _problem(
                outcome,
                EngineStatus.ERROR,
                "ClazyContextError",
                f"clazy source is outside the project or unreadable: {source.name}",
                safe_path,
            )
            continue
        if resolved.exists():
            try:
                details = resolved.stat()
            except OSError:
                details = None
            if (
                details is None
                or not stat.S_ISREG(details.st_mode)
                or not os.access(resolved, os.R_OK)
            ):
                _problem(
                    outcome,
                    EngineStatus.ERROR,
                    "ClazyContextError",
                    f"clazy source is not a readable regular file: {source.name}",
                    relative,
                )
                continue
        normalized.append(resolved)
    if outcome.errors:
        return None
    if len(normalized) > _MAX_SELECTED_UNITS:
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyContextError",
            "clazy source count exceeds the bounded limit",
        )
        return None
    return normalized


def _validated_context(
    project_root: Path,
    context: AnalysisContext | None,
    outcome: ClazyOutcome,
    *,
    required: bool,
) -> AnalysisContext | None:
    if context is None:
        _unavailable(
            outcome,
            "clazy requires an approved capability inventory and exact compilation context",
            required=required,
        )
        outcome.evidence.append(
            ToolEvidence(name="clazy", path="", error="analysis context unavailable")
        )
        return None
    if context.project.root != project_root:
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyContextError",
            "clazy analysis context belongs to another project root",
        )
        return None
    return context


def _ready_tool(
    project_root: Path,
    context: AnalysisContext,
    outcome: ClazyOutcome,
    *,
    required: bool,
) -> tuple[ToolCapability, Path, str] | None:
    capability = context.capabilities.capabilities.get("clazy")
    executable = regular_executable(project_root, capability)
    alias = capability.details.get("resolved_alias", "") if capability is not None else ""
    provider = _PROVIDERS.get(alias, "")
    if executable is None or capability is None or not provider:
        _unavailable(outcome, "clazy is unavailable or incompletely probed", required=required)
        outcome.evidence.append(
            ToolEvidence(
                name="clazy",
                path=capability.path if capability is not None else "",
                version=capability.version if capability is not None else "",
                error="tool not found or capability probe incomplete; no command was executed",
            )
        )
        return None
    if context.compilation.database_path is None:
        _unavailable(outcome, "clazy requires an exact compilation database", required=required)
        outcome.evidence.append(
            ToolEvidence(
                name="clazy",
                path=str(executable),
                version=capability.version,
                error="exact compilation database unavailable; no command was executed",
            )
        )
        return None
    if any(item.level == "error" for item in context.compilation.diagnostics):
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyContextError",
            "clazy did not run because compilation context ingestion has errors",
        )
        return None
    if provider == "compiler-wrapper":
        compiler = context.capabilities.capabilities.get("clang++")
        if regular_executable(project_root, compiler) is None:
            _unavailable(
                outcome,
                "the clazy compiler wrapper requires an approved clang++ capability",
                required=required,
            )
            outcome.evidence.append(
                ToolEvidence(
                    name="clazy",
                    path=str(executable),
                    version=capability.version,
                    error="approved clang++ capability unavailable; no command was executed",
                )
            )
            return None
    outcome.provider = provider
    return capability, executable, provider


def _selected_context_units(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext,
    outcome: ClazyOutcome,
) -> list[CompilationUnit] | None:
    selected, missing = selected_units(project_root, cpp_files, context)
    if len(selected) > _MAX_SELECTED_UNITS:
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyContextError",
            "clazy translation-unit count exceeds the bounded limit",
        )
        return None
    if missing:
        message = f"clazy context is missing {len(missing)} production source command(s)"
        outcome.errors.append(message)
        outcome.targets.extend(
            _target(
                EngineStatus.ERROR,
                "ClazyContextMissing",
                "No exact translation-unit command covers this production source.",
                source,
            )
            for source in missing
        )
    if not selected:
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyContextError",
            "clazy context contains no replayable production units",
        )
        return None
    return selected


def _wrapper_compiler(context: AnalysisContext, project_root: Path) -> Path:
    capability = context.capabilities.capabilities["clang++"]
    executable = regular_executable(project_root, capability)
    assert executable is not None
    return executable


def _command_and_environment(
    executable: Path,
    provider: str,
    source: Path,
    replay_argv: tuple[str, ...],
    compiler_arguments: list[str],
    checks: str,
    context: AnalysisContext,
    project_root: Path,
) -> tuple[list[str], dict[str, str]]:
    environment = replay_environment()
    if provider == "standalone":
        return (
            [
                str(executable),
                f"--checks={checks}",
                "--only-qt",
                str(source),
                "--",
                *compiler_arguments,
            ],
            environment,
        )
    environment.update(
        {
            "CLANGXX": str(_wrapper_compiler(context, project_root)),
            "CLAZY_CHECKS": checks,
        }
    )
    return [str(executable), *replay_argv[1:]], environment


def _process_error(result: ProcessResult, source: str) -> str:
    if result.timed_out:
        return f"clazy timed out: {source}"
    if result.truncated:
        return f"clazy output was truncated: {source}"
    if not isinstance(result.returncode, int) or result.returncode < 0:
        return f"clazy terminated unexpectedly: {source}"
    if result.returncode != 0:
        return f"clazy failed with exit code {result.returncode}: {source}"
    return ""


def _run_unit(
    project_root: Path,
    unit: CompilationUnit,
    context: AnalysisContext,
    capability: ToolCapability,
    executable: Path,
    provider: str,
    checks: str,
    outcome: ClazyOutcome,
    runner: Callable[..., ProcessResult],
    timeout: float,
) -> bool:
    if any(item.level == "error" for item in unit.diagnostics):
        message = f"clazy skipped a compilation unit with context errors: {unit.source}"
        _problem(outcome, EngineStatus.ERROR, "ClazyContextError", message, unit.source)
        return False
    try:
        replay = build_replay_command(
            project_root,
            unit,
            context.capabilities,
            operation="syntax",
        )
        compiler_arguments = tooling_arguments(replay.argv, replay.source)
        command, environment = _command_and_environment(
            executable,
            provider,
            replay.source,
            replay.argv,
            compiler_arguments,
            checks,
            context,
            project_root,
        )
    except ReplayCommandError as err:
        message = f"clazy replay {err.code}: {unit.source}: {err}"
        _problem(outcome, EngineStatus.ERROR, "ClazyReplayError", message, unit.source)
        return False
    try:
        result = runner(
            command,
            cwd=replay.cwd,
            env=environment,
            input_text="",
            replace_env=True,
            timeout=timeout,
            max_output_chars=_OUTPUT_CHARS,
        )
    except Exception as exc:
        message = f"clazy could not execute: {unit.source}: {type(exc).__name__}"
        outcome.evidence.append(
            ToolEvidence(
                name="clazy",
                path=str(executable),
                version=capability.version,
                argv=command,
                error=message,
            )
        )
        _problem(outcome, EngineStatus.ERROR, "ClazyExecutionError", message, unit.source)
        return False
    tool_record = ToolEvidence(
        name="clazy",
        path=command[0],
        version=capability.version,
        argv=command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
    )
    outcome.evidence.append(tool_record)
    message = _process_error(result, unit.source)
    if message:
        tool_record.error = message
        _problem(outcome, EngineStatus.ERROR, "ClazyExecutionError", message, unit.source)
        return False
    parsed = parse_clazy_diagnostics(project_root, replay.cwd, result.stdout, result.stderr)
    if parsed.error:
        message = f"clazy output was not parseable: {unit.source}: {parsed.error}"
        tool_record.error = message
        _problem(outcome, EngineStatus.ERROR, "ClazyExecutionError", message, unit.source)
        return False
    diagnostics = list(parsed.diagnostics)
    outcome.diagnostics.extend(diagnostics)
    outcome.targets.extend(item.target for item in diagnostics)
    if not diagnostics:
        suffix = unit.configuration.removeprefix("sha256:")[:12] or "exact"
        outcome.targets.append(
            _target(
                EngineStatus.PASS,
                f"Clazy:{suffix}",
                "clazy completed without diagnostics.",
                unit.source,
            )
        )
    outcome.configurations_checked += 1
    return True


def run_clazy(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext | None,
    config: Mapping[str, Any],
    *,
    runner: Callable[..., ProcessResult],
) -> ClazyOutcome:
    """Run clazy only with approved executables and sanitized exact unit context."""

    mode = config.get("clazy", "auto")
    outcome = ClazyOutcome(mode=str(mode), profile=_checks(config))
    if mode == "off":
        return outcome
    if mode not in {"auto", "required"}:
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyConfigError",
            "clazy mode must be auto, required, or off",
        )
        return outcome
    try:
        resolved_root = project_root.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        _problem(
            outcome,
            EngineStatus.ERROR,
            "ClazyContextError",
            f"clazy project root could not be resolved: {type(err).__name__}",
        )
        return outcome
    required = mode == "required"
    context = _validated_context(resolved_root, context, outcome, required=required)
    if context is None:
        return outcome
    normalized_files = _normalized_sources(resolved_root, cpp_files, outcome)
    if normalized_files is None:
        return outcome
    ready = _ready_tool(resolved_root, context, outcome, required=required)
    if ready is None:
        return outcome
    capability, executable, provider = ready
    selected = _selected_context_units(resolved_root, normalized_files, context, outcome)
    if selected is None:
        return outcome
    checks = _checks(config)
    checked_sources: set[str] = set()
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    for index, unit in enumerate(selected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            unexamined = len(selected) - index
            message = f"clazy global time budget left {unexamined} unit(s) unexamined"
            _problem(outcome, EngineStatus.ERROR, "ClazyBudgetError", message, unit.source)
            break
        if _run_unit(
            resolved_root,
            unit,
            context,
            capability,
            executable,
            provider,
            checks,
            outcome,
            runner,
            min(_TIMEOUT_SECONDS, remaining),
        ):
            checked_sources.add(unit.source)
    outcome.sources_checked = len(checked_sources)
    outcome.mode = "exact" if not outcome.errors else "error"
    return outcome
