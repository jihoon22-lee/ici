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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core.context import AnalysisContext, CompilationUnit
from ici.core.cpp_replay import ReplayCommandError, build_replay_command, replay_environment
from ici.core.models import ToolEvidence
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_diagnostics import CppDiagnostic, parse_clang_tidy_diagnostics
from ici.engines._cpp_tooling import (
    GccStdlibProjectionCache,
    gcc_standard_library_for_replay,
    regular_executable,
    selected_units,
    tooling_arguments,
)
from ici.engines.cpp_text import cpp_requires_expression_before_brace, mask_cpp_literals

_CHECK = "readability-function-size"
_CHECKS = f"-*,{_CHECK}"
_MAX_SELECTED_UNITS = 2_048
_MAX_BOUNDARIES = 100_000
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_SOURCE_CACHE_BYTES = 16 * 1024 * 1024
_MAX_RUN_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_OUTPUT_CHARS = 1_000_000
_MAX_FUNCTION_NAME_CHARS = 2_048
_UNIT_TIMEOUT_SECONDS = 120.0
_GLOBAL_TIMEOUT_SECONDS = 600.0
_PARSER_TIMEOUT_SECONDS = 10.0

_PARENT_RE = re.compile(
    r"^warning: function '(?P<name>.+)' exceeds recommended size/complexity thresholds$"
)
_LINES_RE = re.compile(
    r"^note: (?P<value>[0-9]+) lines including whitespace and comments \(threshold 0\)$"
)
_STATEMENTS_RE = re.compile(r"^note: (?P<value>[0-9]+) statements \(threshold 0\)$")
_PARAMETERS_RE = re.compile(r"^note: (?P<value>[0-9]+) parameters \(threshold 0\)$")
_SUPPRESSED_RE = re.compile(r"^Suppressed [1-9][0-9]* warnings? \(")
_EXTERNAL_ONLY_SUPPRESSED_RE = re.compile(
    r"^Suppressed (?P<total>[1-9][0-9]*) warnings? "
    r"\((?P<external>[1-9][0-9]*) in non-user code\)\.$"
)


@dataclass(frozen=True)
class CppFunctionBoundary:
    """One AST-confirmed function definition mapped back to project source."""

    file_path: str
    start_line: int
    end_line: int
    start_column: int | None
    end_column: int | None
    body_start_line: int
    body_start_column: int
    name: str
    lines: int = 0
    statements: int = 0
    parameters: int = 0
    configurations: tuple[str, ...] = ()


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


@dataclass
class _PendingBoundary:
    diagnostic: CppDiagnostic
    name: str
    lines: int = 0
    statements: int = 0
    parameters: int = 0
    seen_notes: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _MappedSource:
    text: str
    masked: str
    offsets: tuple[int, ...]


@dataclass(frozen=True)
class _BoundaryRun:
    root: Path
    context: AnalysisContext
    capability: ToolCapability
    executable: Path
    executable_identity: tuple[int, int, int, int, int, int]
    units: tuple[CompilationUnit, ...]
    sources: dict[str, _MappedSource]


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


def read_cpp_source_text(project_root: Path, file_path: str) -> str:
    """Read one bounded, stable, project-contained C++ source as UTF-8."""

    if file_path == "[external]":
        raise ValueError("function boundary resolved outside the project")
    try:
        root = project_root.resolve(strict=True)
        path = (root / file_path).resolve(strict=True)
        path.relative_to(root)
        payload = _read_bounded_regular(path, _MAX_SOURCE_BYTES, containment_root=root)
    except (OSError, RuntimeError, ValueError, _ReadError) as err:
        raise ValueError("function boundary source is unavailable") from err
    try:
        return payload.decode("utf-8")
    except UnicodeError as err:
        raise ValueError("function boundary source is not valid UTF-8") from err


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    offsets.extend(index + 1 for index, char in enumerate(text) if char == "\n")
    return offsets


def _line_number(offsets: Sequence[int], position: int) -> int:
    # ``bisect`` is avoided here because this loop only walks bounded brace
    # inventories and retaining the small helper keeps 3.10 compatibility
    # obvious.  Binary search still makes large generated sources inexpensive.
    low = 0
    high = len(offsets)
    while low < high:
        middle = (low + high) // 2
        if offsets[middle] <= position:
            low = middle + 1
        else:
            high = middle
    return max(1, low)


def _mapped_source(text: str) -> _MappedSource:
    masked = mask_cpp_literals(text).replace("<%", "{ ").replace("%>", "} ")
    return _MappedSource(text=text, masked=masked, offsets=tuple(_line_offsets(masked)))


def _top_level_brace_pairs(
    source: _MappedSource,
    start_offset: int,
) -> Iterator[tuple[int, int, int, int]]:
    stack: list[tuple[int, int, int, int]] = []
    parentheses = 0
    brackets = 0
    for position in range(start_offset, len(source.masked)):
        char = source.masked[position]
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif char == "{":
            stack.append((position, parentheses, brackets, len(stack)))
        elif char == "}" and stack:
            opened, open_parentheses, open_brackets, depth = stack.pop()
            if depth == 0:
                yield opened, position, open_parentheses, open_brackets


def _has_top_level_initializer_colon(prefix: str) -> bool:
    parentheses = 0
    brackets = 0
    found = False
    for index, char in enumerate(prefix):
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif (
            char == ":"
            and parentheses == 0
            and brackets == 0
            and (index == 0 or prefix[index - 1] != ":")
            and (index + 1 == len(prefix) or prefix[index + 1] != ":")
        ):
            found = True
    return found


def _constructor_initializer_brace(prefix: str) -> bool:
    if not _has_top_level_initializer_colon(prefix):
        return False
    stripped = prefix.rstrip()
    if not stripped:
        return False
    # A braced member/delegating initializer is introduced by an identifier.
    # Once that initializer is complete, the real body is preceded by its
    # closing '}' (or ')' for a parenthesized initializer) instead.
    return bool(re.search(r"(?:[A-Za-z_][A-Za-z0-9_]*|[>\]])$", stripped))


def _catch_extended_close(source: _MappedSource, close_position: int) -> int:
    final_close = close_position
    cursor = close_position + 1
    while True:
        match = re.match(r"\s*catch\b", source.masked[cursor:])
        if match is None:
            return final_close
        catch_start = cursor + match.end()
        pair = next(_top_level_brace_pairs(source, catch_start), None)
        if pair is None:
            return final_close
        _opened, closed, parentheses, brackets = pair
        if parentheses or brackets:
            return final_close
        final_close = closed
        cursor = final_close + 1


def _locate_body(
    source: _MappedSource,
    *,
    start_line: int,
    start_column: int | None,
    line_count: int | None,
) -> tuple[int, int, int, int]:
    """Return function geometry using Clang's location and line-count note."""

    offsets = source.offsets
    if start_line > len(offsets):
        raise ValueError("function boundary starts beyond the source")
    start_offset = offsets[start_line - 1] + max(0, (start_column or 1) - 1)
    for opened, closed, parentheses, brackets in _top_level_brace_pairs(source, start_offset):
        if parentheses or brackets:
            continue
        open_line = _line_number(offsets, opened)
        prefix = source.masked[start_offset:opened]
        if cpp_requires_expression_before_brace(prefix):
            continue
        if _constructor_initializer_brace(prefix):
            continue
        final_close = _catch_extended_close(source, closed)
        close_line = _line_number(offsets, final_close)
        expected_lines = 0 if line_count is None else line_count
        if close_line - open_line != expected_lines:
            if line_count is None:
                raise ValueError("function-size lines note is required for a multi-line body")
            raise ValueError("function-size lines note does not match the mapped body")
        open_column = opened - offsets[open_line - 1] + 1
        close_column = final_close - offsets[close_line - 1] + 1
        return open_line, open_column, close_line, close_column
    raise ValueError("function body could not be mapped from clang-tidy evidence")


def _note_value(pending: _PendingBoundary, diagnostic: CppDiagnostic) -> None:
    target = diagnostic.target
    parent = pending.diagnostic.target
    if (
        target.file_path,
        target.start_line,
        target.start_column,
    ) != (
        parent.file_path,
        parent.start_line,
        parent.start_column,
    ):
        raise ValueError("function-size note does not match its parent location")
    patterns = (
        ("lines", _LINES_RE),
        ("statements", _STATEMENTS_RE),
        ("parameters", _PARAMETERS_RE),
    )
    for field_name, pattern in patterns:
        match = pattern.fullmatch(target.message)
        if match is None:
            continue
        if field_name in pending.seen_notes:
            raise ValueError(f"function-size emitted duplicate {field_name} notes")
        value = int(match.group("value"))
        if value <= 0:
            raise ValueError("function-size note must contain a positive metric")
        setattr(pending, field_name, value)
        pending.seen_notes.add(field_name)
        return
    raise ValueError(f"unexpected function-size note: {target.message!r}")


def _finish_pending(
    project_root: Path,
    configuration: str,
    pending: _PendingBoundary,
    sources: dict[str, _MappedSource],
    cached_bytes: list[int],
) -> CppFunctionBoundary:
    target = pending.diagnostic.target
    source = sources.get(target.file_path)
    if source is None:
        text = read_cpp_source_text(project_root, target.file_path)
        cached_bytes[0] += len(text.encode("utf-8"))
        if cached_bytes[0] > _MAX_SOURCE_CACHE_BYTES:
            raise ValueError("function boundary source cache exceeds the bounded limit")
        source = _mapped_source(text)
        sources[target.file_path] = source
    body_start, body_column, end_line, end_column = _locate_body(
        source,
        start_line=target.start_line,
        start_column=target.start_column,
        line_count=pending.lines if "lines" in pending.seen_notes else None,
    )
    return CppFunctionBoundary(
        file_path=target.file_path,
        start_line=target.start_line,
        end_line=end_line,
        start_column=target.start_column,
        end_column=end_column,
        body_start_line=body_start,
        body_start_column=body_column,
        name=pending.name,
        lines=pending.lines,
        statements=pending.statements,
        parameters=pending.parameters,
        configurations=(configuration,),
    )


def _pending_name(match: re.Match[str]) -> str:
    name = match.group("name")
    if not name or len(name) > _MAX_FUNCTION_NAME_CHARS or "\x00" in name:
        raise ValueError("function boundary name is outside the bounded shape")
    return name


def _consume_boundary_diagnostic(
    project_root: Path,
    configuration: str,
    diagnostic: CppDiagnostic,
    pending: _PendingBoundary | None,
    boundaries: list[CppFunctionBoundary],
    sources: dict[str, _MappedSource],
    cached_bytes: list[int],
) -> _PendingBoundary:
    if diagnostic.tool_rule_id != _CHECK:
        raise ValueError("function boundary output contains an unexpected check")
    message = diagnostic.target.message
    parent_match = _PARENT_RE.fullmatch(message)
    if parent_match is not None:
        if pending is not None:
            boundaries.append(
                _finish_pending(
                    project_root,
                    configuration,
                    pending,
                    sources,
                    cached_bytes,
                )
            )
        return _PendingBoundary(diagnostic=diagnostic, name=_pending_name(parent_match))
    if pending is None or not message.startswith("note:"):
        raise ValueError("function-size note appeared without a function warning")
    _note_value(pending, diagnostic)
    return pending


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
) -> tuple[CppFunctionBoundary, ...]:
    """Strictly parse one dedicated clang-tidy function-size invocation."""

    combined = stdout + "\n" + stderr
    for line in combined.splitlines():
        summary = line.strip()
        if _SUPPRESSED_RE.match(summary) is None:
            continue
        external_only = _EXTERNAL_ONLY_SUPPRESSED_RE.fullmatch(summary)
        if external_only is None or external_only.group("total") != external_only.group("external"):
            raise ValueError("function boundary diagnostics were suppressed")
    parsed = parse_clang_tidy_diagnostics(project_root, cwd, stdout, stderr)
    if parsed.error:
        raise ValueError(f"function boundary output is not parseable: {parsed.error}")

    parse_deadline = (
        deadline if deadline is not None else time.monotonic() + _PARSER_TIMEOUT_SECONDS
    )
    source_cache = {} if sources is None else sources
    cached_bytes = [sum(len(item.text.encode("utf-8")) for item in source_cache.values())]
    diagnostics = (
        parsed.diagnostics
        if source_file is None
        else tuple(item for item in parsed.diagnostics if item.target.file_path == source_file)
    )
    if len(diagnostics) > _MAX_BOUNDARIES * 4:
        raise ValueError("function boundary diagnostic count exceeds the bounded limit")
    boundaries: list[CppFunctionBoundary] = []
    pending: _PendingBoundary | None = None
    for diagnostic in diagnostics:
        if time.monotonic() > parse_deadline:
            raise ValueError("function boundary parser budget expired")
        pending = _consume_boundary_diagnostic(
            project_root,
            configuration,
            diagnostic,
            pending,
            boundaries,
            source_cache,
            cached_bytes,
        )
    if pending is not None:
        boundaries.append(
            _finish_pending(
                project_root,
                configuration,
                pending,
                source_cache,
                cached_bytes,
            )
        )
    if len(boundaries) > _MAX_BOUNDARIES:
        raise ValueError("function boundary count exceeds the bounded limit")
    return tuple(boundaries)


def _merge_boundaries(
    boundaries: list[CppFunctionBoundary],
    successful_configurations: dict[str, set[str]],
) -> tuple[list[CppFunctionBoundary], list[str]]:
    grouped: dict[tuple[str, int, int | None, str], CppFunctionBoundary] = {}
    rejected: set[tuple[str, int, int | None, str]] = set()
    warnings: list[str] = []
    for boundary in boundaries:
        key = (
            boundary.file_path,
            boundary.start_line,
            boundary.start_column,
            boundary.name,
        )
        if key in rejected:
            continue
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = boundary
            continue
        if (
            existing.end_line,
            existing.end_column,
            existing.body_start_line,
            existing.body_start_column,
        ) != (
            boundary.end_line,
            boundary.end_column,
            boundary.body_start_line,
            boundary.body_start_column,
        ):
            warnings.append(
                f"configuration-dependent function boundary was not promoted: "
                f"{boundary.file_path}:{boundary.start_line}"
            )
            grouped.pop(key, None)
            rejected.add(key)
            continue
        grouped[key] = replace(
            existing,
            lines=max(existing.lines, boundary.lines),
            statements=max(existing.statements, boundary.statements),
            parameters=max(existing.parameters, boundary.parameters),
            configurations=tuple(sorted({*existing.configurations, *boundary.configurations})),
        )
    for key, boundary in tuple(grouped.items()):
        expected = successful_configurations.get(boundary.file_path)
        if expected is None or set(boundary.configurations) == expected:
            continue
        warnings.append(
            f"configuration-dependent function boundary was not promoted: "
            f"{boundary.file_path}:{boundary.start_line}"
        )
        grouped.pop(key, None)
    ordered = sorted(
        grouped.values(),
        key=lambda item: (item.file_path, item.start_line, item.start_column or 0, item.name),
    )
    return ordered, warnings


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
) -> list[CppFunctionBoundary] | None:
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
        return list(
            parse_function_boundaries(
                project_root,
                replay.cwd,
                result.stdout,
                result.stderr,
                configuration=unit.configuration,
                source_file=unit.source,
                sources={unit.source: source_snapshot},
                deadline=deadline,
            )
        )
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
        sources[unit.source] = _mapped_source(text)
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
    for index, unit in enumerate(prepared.units):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outcome.errors.append(
                f"function boundary global budget left "
                f"{len(prepared.units) - index} unit(s) unexamined"
            )
            break
        boundaries = _run_unit(
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
        if boundaries is not None:
            collected.extend(boundaries)
            checked_sources.add(unit.source)
            successful_configurations.setdefault(unit.source, set()).add(unit.configuration)
            outcome.configurations_checked += 1
        if len(collected) > _MAX_BOUNDARIES:
            outcome.errors.append("function boundary count exceeds the bounded limit")
            break
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
