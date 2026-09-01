"""Read-only clang-tidy execution over ici-sanitized compilation contexts."""

from __future__ import annotations

import os
import re
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
from ici.engines._cpp_diagnostics import CppDiagnostic, parse_clang_tidy_diagnostics

_DEFAULT_CHECKS = "-*,bugprone-*,clang-analyzer-*,performance-*"
_MAX_CONFIG_BYTES = 1_048_576
_MAX_SELECTED_UNITS = 2_048
_TIMEOUT_SECONDS = 120.0
_GLOBAL_TIMEOUT_SECONDS = 600.0
_OUTPUT_CHARS = 1_000_000
_UNSAFE_CONFIG_ARGUMENT_RE = re.compile(
    rb"\b(?:ExtraArgs(?:Before)?|InheritParentConfig)\b",
    re.IGNORECASE,
)


@dataclass
class ClangTidyOutcome:
    """Mutable adapter result consumed by the lint facade."""

    targets: list[InspectionTarget] = field(default_factory=list)
    diagnostics: list[CppDiagnostic] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "off"
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


def _unavailable(outcome: ClangTidyOutcome, message: str, *, required: bool) -> None:
    status = EngineStatus.ERROR if required else EngineStatus.WARN
    outcome.targets.append(_target(status, "ClangTidyUnavailable", message))
    (outcome.errors if required else outcome.warnings).append(message)


def _regular_executable(root: Path, capability: ToolCapability | None) -> Path | None:
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
        or _inside(root, path)
        or not stat.S_ISREG(details.st_mode)
        or not os.access(path, os.X_OK)
    ):
        return None
    return path


def _selected_units(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext,
) -> tuple[list[CompilationUnit], list[str]]:
    sources = {path.relative_to(project_root).as_posix() for path in cpp_files}
    units = context.compilation.units
    if context.compilation.unity_build:
        selected = [unit for unit in units if unit.language in {"c", "c++"}]
        return selected, []
    selected = [unit for unit in units if unit.source in sources]
    covered = {unit.source for unit in selected}
    return selected, sorted(sources - covered)


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_config_file(root: Path, value: Path, *, explicit: bool) -> tuple[Path | None, str]:
    try:
        path = value.resolve(strict=True)
        details = path.stat()
    except (OSError, RuntimeError) as err:
        source = "Configured" if explicit else "Discovered"
        return None, f"{source} clang-tidy config could not be resolved: {type(err).__name__}"
    if not _inside(root, path):
        return None, "clang-tidy config resolves outside the project root"
    if not stat.S_ISREG(details.st_mode):
        return None, "clang-tidy config is not a regular file"
    if details.st_size > _MAX_CONFIG_BYTES:
        return None, "clang-tidy config exceeds the 1 MiB safety limit"
    try:
        content = path.read_bytes()
    except OSError as err:
        return None, f"clang-tidy config could not be read: {type(err).__name__}"
    if b"\x00" in content:
        return None, "clang-tidy config contains a null byte"
    if _UNSAFE_CONFIG_ARGUMENT_RE.search(content):
        return None, "clang-tidy config may not add compiler arguments or inherit parent config"
    return path, ""


def _explicit_config(root: Path, value: object) -> tuple[Path | None, str]:
    if not isinstance(value, str) or not value:
        return None, ""
    lexical = Path(value)
    return _validate_config_file(
        root,
        lexical if lexical.is_absolute() else root / lexical,
        explicit=True,
    )


def _discovered_config(root: Path, source: Path) -> tuple[Path | None, str]:
    current = source.parent
    while _inside(root, current):
        candidate = current / ".clang-tidy"
        if candidate.exists() or candidate.is_symlink():
            return _validate_config_file(root, candidate, explicit=False)
        if current == root:
            break
        current = current.parent
    return None, ""


def _tooling_arguments(argv: tuple[str, ...], source: Path) -> list[str]:
    expected = ("-Wall", "-Wextra", "-fsyntax-only", str(source))
    if len(argv) < 5 or tuple(argv[-4:]) != expected:
        raise ReplayCommandError(
            "unexpected-replay-shape",
            "The sanitized compiler replay did not have the expected analysis suffix.",
        )
    return list(argv[1:-4])


def _checks(config: Mapping[str, Any]) -> str:
    values = config.get("clang_tidy_checks")
    return ",".join(values) if isinstance(values, list) else ""


def _command(
    executable: Path,
    source: Path,
    compiler_arguments: list[str],
    config: Mapping[str, Any],
    explicit_config: Path | None,
    discovered_config: Path | None,
) -> list[str]:
    # Keep clang-tidy's suppression accounting enabled.  ``--quiet`` removes
    # the "Suppressed N warnings" trailer while retaining "N warnings
    # generated", which makes a clean translation unit indistinguishable from
    # silently discarded diagnostics.
    command = [str(executable), "--use-color=false"]
    configured_checks = _checks(config)
    selected_config = explicit_config or discovered_config
    if selected_config is not None:
        command.append(f"--config-file={selected_config}")
    else:
        # Prevent clang-tidy from walking above the project root and silently
        # inheriting a user- or machine-level configuration.
        command.append("--config={}")
    if configured_checks:
        command.append(f"--checks={configured_checks}")
    elif selected_config is None:
        command.append(f"--checks={_DEFAULT_CHECKS}")
    command.extend((str(source), "--", *compiler_arguments))
    return command


def _record_process(
    outcome: ClangTidyOutcome,
    capability: ToolCapability,
    command: list[str],
    result: ProcessResult,
) -> ToolEvidence:
    item = ToolEvidence(
        name="clang-tidy",
        path=command[0],
        version=capability.version,
        argv=command,
        returncode=result.returncode,
        timed_out=result.timed_out,
        truncated=result.truncated,
    )
    outcome.evidence.append(item)
    return item


def _process_error(result: ProcessResult, source: str) -> str:
    if result.timed_out:
        return f"clang-tidy timed out: {source}"
    if result.truncated:
        return f"clang-tidy output was truncated: {source}"
    if not isinstance(result.returncode, int) or result.returncode < 0:
        return f"clang-tidy terminated unexpectedly: {source}"
    if result.returncode != 0:
        return f"clang-tidy failed with exit code {result.returncode}: {source}"
    return ""


def _run_unit(
    project_root: Path,
    unit: CompilationUnit,
    context: AnalysisContext,
    capability: ToolCapability,
    executable: Path,
    config: Mapping[str, Any],
    explicit_config: Path | None,
    outcome: ClangTidyOutcome,
    runner: Callable[..., ProcessResult],
    timeout: float,
) -> bool:
    if any(item.level == "error" for item in unit.diagnostics):
        message = f"clang-tidy skipped a compilation unit with context errors: {unit.source}"
        outcome.errors.append(message)
        outcome.targets.append(
            _target(EngineStatus.ERROR, "ClangTidyContextError", message, unit.source)
        )
        return False
    try:
        replay = build_replay_command(
            project_root,
            unit,
            context.capabilities,
            operation="syntax",
        )
        compiler_arguments = _tooling_arguments(replay.argv, replay.source)
    except ReplayCommandError as err:
        message = f"clang-tidy replay {err.code}: {unit.source}: {err}"
        outcome.errors.append(message)
        outcome.targets.append(
            _target(EngineStatus.ERROR, "ClangTidyReplayError", message, unit.source)
        )
        return False
    discovered_config: Path | None = None
    if explicit_config is None:
        discovered_config, config_error = _discovered_config(project_root, replay.source)
        if config_error:
            outcome.errors.append(config_error)
            outcome.targets.append(
                _target(EngineStatus.ERROR, "ClangTidyConfigError", config_error, unit.source)
            )
            return False
    command = _command(
        executable,
        replay.source,
        compiler_arguments,
        config,
        explicit_config,
        discovered_config,
    )
    try:
        result = runner(
            command,
            cwd=replay.cwd,
            env=replay_environment(),
            input_text="",
            replace_env=True,
            timeout=timeout,
            max_output_chars=_OUTPUT_CHARS,
        )
    except Exception as exc:
        message = f"clang-tidy could not execute: {unit.source}: {type(exc).__name__}"
        outcome.evidence.append(
            ToolEvidence(
                name="clang-tidy",
                path=str(executable),
                version=capability.version,
                argv=command,
                error=message,
            )
        )
        outcome.errors.append(message)
        outcome.targets.append(
            _target(EngineStatus.ERROR, "ClangTidyExecutionError", message, unit.source)
        )
        return False
    tool_record = _record_process(outcome, capability, command, result)
    message = _process_error(result, unit.source)
    if message:
        tool_record.error = message
        outcome.errors.append(message)
        outcome.targets.append(
            _target(EngineStatus.ERROR, "ClangTidyExecutionError", message, unit.source)
        )
        return False
    parsed = parse_clang_tidy_diagnostics(
        project_root,
        replay.cwd,
        result.stdout,
        result.stderr,
    )
    if parsed.error:
        message = f"clang-tidy output was not parseable: {unit.source}"
        tool_record.error = message
        outcome.errors.append(message)
        outcome.targets.append(
            _target(EngineStatus.ERROR, "ClangTidyExecutionError", message, unit.source)
        )
        return False
    diagnostics = list(parsed.diagnostics)
    outcome.diagnostics.extend(diagnostics)
    outcome.targets.extend(item.target for item in diagnostics)
    if not diagnostics:
        suffix = unit.configuration.removeprefix("sha256:")[:12] or "exact"
        outcome.targets.append(
            _target(
                EngineStatus.PASS,
                f"ClangTidy:{suffix}",
                "clang-tidy completed without diagnostics.",
                unit.source,
            )
        )
    outcome.configurations_checked += 1
    return True


def _fail(
    outcome: ClangTidyOutcome,
    target_name: str,
    message: str,
    path: str = ".",
) -> None:
    outcome.errors.append(message)
    outcome.targets.append(_target(EngineStatus.ERROR, target_name, message, path))
    outcome.mode = "error"


def _resolved_project_root(
    project_root: Path,
    outcome: ClangTidyOutcome,
) -> Path | None:
    try:
        return project_root.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        message = f"clang-tidy project root could not be resolved: {type(err).__name__}"
        _fail(outcome, "ClangTidyContextError", message)
        return None


def _validated_context(
    project_root: Path,
    context: AnalysisContext | None,
    outcome: ClangTidyOutcome,
    *,
    required: bool,
) -> AnalysisContext | None:
    if context is None:
        _unavailable(
            outcome,
            "clang-tidy requires an approved capability inventory and exact compilation context",
            required=required,
        )
        outcome.evidence.append(
            ToolEvidence(name="clang-tidy", path="", error="analysis context unavailable")
        )
        outcome.mode = "unavailable"
        return None
    if context.project.root != project_root:
        _fail(
            outcome,
            "ClangTidyContextError",
            "clang-tidy analysis context belongs to another project root",
        )
        return None
    return context


def _normalized_sources(
    project_root: Path,
    cpp_files: list[Path],
    outcome: ClangTidyOutcome,
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
            message = f"clang-tidy source is outside the project or unreadable: {source.name}"
            _fail(outcome, "ClangTidyContextError", message, safe_path)
            continue
        if not resolved.exists():
            normalized.append(resolved)
            continue
        try:
            details = resolved.stat()
        except OSError:
            details = None
        if details is None or not stat.S_ISREG(details.st_mode) or not os.access(resolved, os.R_OK):
            message = f"clang-tidy source is not a readable regular file: {source.name}"
            _fail(outcome, "ClangTidyContextError", message, relative)
            continue
        normalized.append(resolved)
    if outcome.errors:
        return None
    if len(normalized) > _MAX_SELECTED_UNITS:
        _fail(
            outcome,
            "ClangTidyContextError",
            "clang-tidy source count exceeds the bounded limit",
        )
        return None
    return normalized


def _ready_tool(
    project_root: Path,
    context: AnalysisContext,
    outcome: ClangTidyOutcome,
    *,
    required: bool,
) -> tuple[ToolCapability, Path] | None:
    capability = context.capabilities.capabilities.get("clang-tidy")
    executable = _regular_executable(project_root, capability)
    if executable is None or capability is None:
        _unavailable(outcome, "clang-tidy is unavailable or incompletely probed", required=required)
        outcome.evidence.append(
            ToolEvidence(
                name="clang-tidy",
                path=capability.path if capability is not None else "",
                version=capability.version if capability is not None else "",
                error="tool not found or capability probe incomplete; no command was executed",
            )
        )
        outcome.mode = "unavailable"
        return None
    if context.compilation.database_path is None:
        _unavailable(
            outcome, "clang-tidy requires an exact compilation database", required=required
        )
        outcome.evidence.append(
            ToolEvidence(
                name="clang-tidy",
                path=str(executable),
                version=capability.version,
                error="exact compilation database unavailable; no command was executed",
            )
        )
        outcome.mode = "unavailable"
        return None
    if any(item.level == "error" for item in context.compilation.diagnostics):
        _fail(
            outcome,
            "ClangTidyContextError",
            "clang-tidy did not run because compilation context ingestion has errors",
        )
        return None
    return capability, executable


def _selected_context_units(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext,
    outcome: ClangTidyOutcome,
) -> list[CompilationUnit] | None:
    selected, missing = _selected_units(project_root, cpp_files, context)
    if len(selected) > _MAX_SELECTED_UNITS:
        _fail(
            outcome,
            "ClangTidyContextError",
            "clang-tidy translation-unit count exceeds the bounded limit",
        )
        return None
    if missing:
        message = f"clang-tidy context is missing {len(missing)} production source command(s)"
        outcome.errors.append(message)
        outcome.targets.extend(
            _target(
                EngineStatus.ERROR,
                "ClangTidyContextMissing",
                "No exact translation-unit command covers this production source.",
                source,
            )
            for source in missing
        )
    if not selected:
        _fail(
            outcome,
            "ClangTidyContextError",
            "clang-tidy context contains no replayable production units",
        )
        return None
    return selected


def _execute_selected_units(
    project_root: Path,
    selected: list[CompilationUnit],
    context: AnalysisContext,
    capability: ToolCapability,
    executable: Path,
    config: Mapping[str, Any],
    explicit_config: Path | None,
    outcome: ClangTidyOutcome,
    runner: Callable[..., ProcessResult],
) -> None:
    checked_sources: set[str] = set()
    deadline = time.monotonic() + _GLOBAL_TIMEOUT_SECONDS
    for index, unit in enumerate(selected):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            unexamined = len(selected) - index
            message = f"clang-tidy global time budget left {unexamined} unit(s) unexamined"
            outcome.errors.append(message)
            outcome.targets.append(
                _target(EngineStatus.ERROR, "ClangTidyBudgetError", message, unit.source)
            )
            break
        if _run_unit(
            project_root,
            unit,
            context,
            capability,
            executable,
            config,
            explicit_config,
            outcome,
            runner,
            min(_TIMEOUT_SECONDS, remaining),
        ):
            checked_sources.add(unit.source)
    outcome.sources_checked = len(checked_sources)


def run_clang_tidy(
    project_root: Path,
    cpp_files: list[Path],
    context: AnalysisContext | None,
    config: Mapping[str, Any],
    *,
    runner: Callable[..., ProcessResult],
) -> ClangTidyOutcome:
    """Run clang-tidy only with an approved tool and exact sanitized unit context."""

    mode = config.get("clang_tidy", "auto")
    outcome = ClangTidyOutcome(mode=str(mode))
    if mode == "off":
        return outcome
    if mode not in {"auto", "required"}:
        _fail(
            outcome,
            "ClangTidyConfigError",
            "clang-tidy mode must be auto, required, or off",
        )
        return outcome
    resolved_root = _resolved_project_root(project_root, outcome)
    if resolved_root is None:
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
    capability, executable = ready
    explicit_config, config_error = _explicit_config(
        resolved_root,
        config.get("clang_tidy_config"),
    )
    if config_error:
        _fail(outcome, "ClangTidyConfigError", config_error)
        return outcome
    selected = _selected_context_units(resolved_root, normalized_files, context, outcome)
    if selected is None:
        return outcome
    _execute_selected_units(
        resolved_root,
        selected,
        context,
        capability,
        executable,
        config,
        explicit_config,
        outcome,
        runner,
    )
    outcome.mode = "exact" if not outcome.errors else "error"
    return outcome
