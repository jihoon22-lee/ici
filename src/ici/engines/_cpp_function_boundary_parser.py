"""Compiler-backed C++ function boundaries from a bounded clang-tidy probe.

The regular complexity fallback deliberately remains a lightweight source
scanner.  When an exact compilation database and an approved ``clang-tidy``
are available, this adapter asks Clang's AST-backed
``readability-function-size`` check to identify real function definitions.
Only diagnostic locations and size notes are consumed; project configuration,
fixes, shell execution, and output files are never enabled.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.engines._cpp_diagnostics import CppDiagnostic, parse_clang_tidy_diagnostics
from ici.engines.cpp_text import (
    cpp_definition_name,
    cpp_function_like_macro_names,
    cpp_has_conditional_directive,
    cpp_is_operator_name,
    cpp_requires_expression_before_brace,
    mask_cpp_lambda_bodies,
    mask_cpp_literals,
)

_CHECK = "readability-function-size"
_MAX_BOUNDARIES = 100_000
_MAX_SOURCE_BYTES = 8 * 1024 * 1024
_MAX_SOURCE_CACHE_BYTES = 16 * 1024 * 1024
_MAX_FUNCTION_NAME_CHARS = 2_048
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
class CppFunctionConfigurationMetric:
    """Function-size notes observed in one immutable compile configuration."""

    configuration: str
    lines: int
    statements: int
    parameters: int


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
    configuration_metrics: tuple[CppFunctionConfigurationMetric, ...] = ()
    function_kind: str = "function"
    is_template: bool = False
    origin: str = "source-spelled"
    metric_variant: bool = False
    preprocessor_conditional: bool = False


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
    macro_names: frozenset[str]
    lambda_count: int


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
    _lambda_masked, lambda_ranges = mask_cpp_lambda_bodies(masked)
    macro_names = cpp_function_like_macro_names(masked)
    return _MappedSource(
        text=text,
        masked=masked,
        offsets=tuple(_line_offsets(masked)),
        macro_names=macro_names,
        lambda_count=len(lambda_ranges),
    )


def _closing_parenthesis(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _macro_expansion_location(
    source: _MappedSource,
    start_line: int,
    start_column: int | None,
) -> bool:
    """Recognize a diagnostic located inside a function-like macro call.

    Clang 21 still emits ``readability-function-size`` at the expansion
    argument for a macro-generated function even with ``IgnoreMacros=true``.
    There is no source-spelled body to map at that location, so treating the
    next brace in the file as this function would be a false exact boundary.
    """

    if start_line < 1 or start_line > len(source.offsets):
        return False
    if not source.macro_names:
        return False
    diagnostic_offset = source.offsets[start_line - 1] + max(0, (start_column or 1) - 1)
    # A generated name is commonly the macro argument and may be on a later
    # line than the invocation name. Search a bounded prefix, then prove that
    # the diagnostic lies inside a call to a source-defined function macro.
    search_start = max(0, diagnostic_offset - 65_536)
    search_end = min(len(source.masked), diagnostic_offset + 4_096)
    window = source.masked[search_start:search_end]
    for match in re.finditer(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*\(", window):
        if match.group("name") not in source.macro_names:
            continue
        invocation_start = search_start + match.start()
        if diagnostic_offset < invocation_start:
            break
        opening = search_start + window.find("(", match.start())
        closing = _closing_parenthesis(source.masked, opening)
        if closing is not None and diagnostic_offset <= closing:
            return True
    return False


def _same_function_name(observed: str | None, expected: str) -> bool:
    if observed is None:
        return False
    observed_name = " ".join(observed.split())
    expected_name = " ".join(expected.split())
    if cpp_is_operator_name(observed_name) or cpp_is_operator_name(expected_name):

        def operator_tail(name: str) -> str:
            qualified = name.rfind("::operator")
            return name[qualified + 2 :] if qualified >= 0 else name

        return "".join(operator_tail(observed_name).split()) == "".join(
            operator_tail(expected_name).split()
        )
    return observed_name.rsplit("::", 1)[-1] == expected_name.rsplit("::", 1)[-1]


def _function_template_prefix(
    source: _MappedSource,
    start_line: int,
    start_column: int | None,
) -> bool:
    start = source.offsets[start_line - 1] + max(0, (start_column or 1) - 1)
    prefix = source.masked[max(0, start - 4_096) : start]
    boundary = max(prefix.rfind(";"), prefix.rfind("{"), prefix.rfind("}"))
    return re.search(r"\btemplate\s*<", prefix[boundary + 1 :]) is not None


def _source_region(
    source: _MappedSource,
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> str:
    start = source.offsets[start_line - 1] + start_column - 1
    end = source.offsets[end_line - 1] + end_column
    return source.text[start:end]


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
    function_name: str,
) -> tuple[int, int, int, int]:
    """Return function geometry using Clang's location and line-count note."""

    offsets = source.offsets
    if start_line > len(offsets):
        raise ValueError("function boundary starts beyond the source")
    line_start = offsets[start_line - 1]
    diagnostic_offset = line_start + max(0, (start_column or 1) - 1)
    line_prefix = source.masked[line_start:diagnostic_offset]
    separator = max(line_prefix.rfind(";"), line_prefix.rfind("}"), line_prefix.rfind("{"))
    start_offset = line_start + separator + 1
    for opened, closed, parentheses, brackets in _top_level_brace_pairs(source, start_offset):
        if parentheses or brackets:
            continue
        open_line = _line_number(offsets, opened)
        prefix = source.masked[start_offset:opened]
        if cpp_requires_expression_before_brace(prefix):
            continue
        if _constructor_initializer_brace(prefix):
            continue
        if not _same_function_name(cpp_definition_name(prefix), function_name):
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
    excluded_scopes: list[tuple[str, int, int | None, str]],
    source_reader: Callable[[Path, str], str],
) -> CppFunctionBoundary | None:
    target = pending.diagnostic.target
    source = sources.get(target.file_path)
    if source is None:
        text = source_reader(project_root, target.file_path)
        cached_bytes[0] += len(text.encode("utf-8"))
        if cached_bytes[0] > _MAX_SOURCE_CACHE_BYTES:
            raise ValueError("function boundary source cache exceeds the bounded limit")
        source = _mapped_source(text)
        sources[target.file_path] = source
    if _macro_expansion_location(source, target.start_line, target.start_column):
        excluded_scopes.append(
            (target.file_path, target.start_line, target.start_column, pending.name)
        )
        return None
    body_start, body_column, end_line, end_column = _locate_body(
        source,
        start_line=target.start_line,
        start_column=target.start_column,
        line_count=pending.lines if "lines" in pending.seen_notes else None,
        function_name=pending.name,
    )
    body_region = _source_region(
        source,
        body_start,
        body_column,
        end_line,
        end_column,
    )
    metric_region, _lambda_ranges = mask_cpp_lambda_bodies(mask_cpp_literals(body_region))
    preprocessor_conditional = cpp_has_conditional_directive(metric_region)
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
        configuration_metrics=(
            CppFunctionConfigurationMetric(
                configuration=configuration,
                lines=pending.lines,
                statements=pending.statements,
                parameters=pending.parameters,
            ),
        ),
        function_kind="operator" if cpp_is_operator_name(pending.name) else "function",
        is_template=_function_template_prefix(source, target.start_line, target.start_column),
        preprocessor_conditional=preprocessor_conditional,
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
    excluded_scopes: list[tuple[str, int, int | None, str]],
    source_reader: Callable[[Path, str], str],
) -> _PendingBoundary:
    if diagnostic.tool_rule_id != _CHECK:
        raise ValueError("function boundary output contains an unexpected check")
    message = diagnostic.target.message
    parent_match = _PARENT_RE.fullmatch(message)
    if parent_match is not None:
        if pending is not None:
            finished = _finish_pending(
                project_root,
                configuration,
                pending,
                sources,
                cached_bytes,
                excluded_scopes,
                source_reader,
            )
            if finished is not None:
                boundaries.append(finished)
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
    excluded_scopes: list[tuple[str, int, int | None, str]] | None = None,
    source_reader: Callable[[Path, str], str] | None = None,
) -> tuple[CppFunctionBoundary, ...]:
    """Strictly parse one dedicated clang-tidy function-size invocation."""

    combined = stdout + "\n" + stderr
    external_suppression = False
    for line in combined.splitlines():
        summary = line.strip()
        if _SUPPRESSED_RE.match(summary) is None:
            continue
        external_only = _EXTERNAL_ONLY_SUPPRESSED_RE.fullmatch(summary)
        if external_only is None or external_only.group("total") != external_only.group("external"):
            raise ValueError("function boundary diagnostics were suppressed")
        external_suppression = True
    parsed = parse_clang_tidy_diagnostics(project_root, cwd, stdout, stderr)
    if parsed.error:
        raise ValueError(f"function boundary output is not parseable: {parsed.error}")

    parse_deadline = (
        deadline if deadline is not None else time.monotonic() + _PARSER_TIMEOUT_SECONDS
    )
    source_reader = read_cpp_source_text if source_reader is None else source_reader
    source_cache = {} if sources is None else sources
    cached_bytes = [sum(len(item.text.encode("utf-8")) for item in source_cache.values())]
    if cached_bytes[0] > _MAX_SOURCE_CACHE_BYTES:
        raise ValueError("function boundary source cache exceeds the bounded limit")
    primaries = (
        parsed.diagnostics
        if source_file is None
        else tuple(item for item in parsed.diagnostics if item.target.file_path == source_file)
    )
    # The shared clang-tidy parser groups explanatory notes under their
    # actionable primary so the lint engine does not count notes as separate
    # findings.  This dedicated structural parser still consumes the ordered
    # function-size metric notes as evidence for that primary.
    diagnostics = tuple(
        item for primary in primaries for item in (primary, *primary.related_diagnostics)
    )
    if external_suppression and not diagnostics:
        raise ValueError("function boundary suppression had no visible project diagnostics")
    if len(diagnostics) > _MAX_BOUNDARIES * 4:
        raise ValueError("function boundary diagnostic count exceeds the bounded limit")
    boundaries: list[CppFunctionBoundary] = []
    exclusions = [] if excluded_scopes is None else excluded_scopes
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
            exclusions,
            source_reader,
        )
    if pending is not None:
        finished = _finish_pending(
            project_root,
            configuration,
            pending,
            source_cache,
            cached_bytes,
            exclusions,
            source_reader,
        )
        if finished is not None:
            boundaries.append(finished)
    if len(boundaries) > _MAX_BOUNDARIES:
        raise ValueError("function boundary count exceeds the bounded limit")
    return tuple(boundaries)


def _merge_boundaries(
    boundaries: list[CppFunctionBoundary],
    successful_configurations: dict[str, set[str]],
) -> tuple[list[CppFunctionBoundary], list[str]]:
    grouped: dict[tuple[str, int, int | None], CppFunctionBoundary] = {}
    rejected: set[tuple[str, int, int | None]] = set()
    warnings: list[str] = []
    warning_keys: set[tuple[str, str, int]] = set()

    def warn(kind: str, boundary: CppFunctionBoundary, message: str) -> None:
        key = (kind, boundary.file_path, boundary.start_line)
        if key not in warning_keys:
            warning_keys.add(key)
            warnings.append(message)

    for boundary in boundaries:
        key = (
            boundary.file_path,
            boundary.start_line,
            boundary.start_column,
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
            existing.name,
            existing.function_kind,
            existing.is_template,
            existing.origin,
        ) != (
            boundary.end_line,
            boundary.end_column,
            boundary.body_start_line,
            boundary.body_start_column,
            boundary.name,
            boundary.function_kind,
            boundary.is_template,
            boundary.origin,
        ):
            warn(
                "scope",
                boundary,
                f"configuration-dependent function boundary was not promoted: "
                f"{boundary.file_path}:{boundary.start_line}",
            )
            grouped.pop(key, None)
            rejected.add(key)
            continue
        configuration_metrics = tuple(
            sorted(
                {*existing.configuration_metrics, *boundary.configuration_metrics},
                key=lambda item: (
                    item.configuration,
                    item.lines,
                    item.statements,
                    item.parameters,
                ),
            )
        )
        metric_values = {
            (item.lines, item.statements, item.parameters) for item in configuration_metrics
        }
        metric_variant = (
            existing.metric_variant or boundary.metric_variant or len(metric_values) > 1
        )
        if metric_variant:
            warn(
                "metric",
                boundary,
                f"configuration-dependent function metrics remain estimated: "
                f"{boundary.file_path}:{boundary.start_line}",
            )
        grouped[key] = replace(
            existing,
            lines=max(existing.lines, boundary.lines),
            statements=max(existing.statements, boundary.statements),
            parameters=max(existing.parameters, boundary.parameters),
            configurations=tuple(sorted({*existing.configurations, *boundary.configurations})),
            configuration_metrics=configuration_metrics,
            metric_variant=metric_variant,
            preprocessor_conditional=(
                existing.preprocessor_conditional or boundary.preprocessor_conditional
            ),
        )
    for key, boundary in tuple(grouped.items()):
        expected = successful_configurations.get(boundary.file_path)
        if expected is None or set(boundary.configurations) == expected:
            continue
        warn(
            "coverage",
            boundary,
            f"configuration-dependent function boundary was not promoted: "
            f"{boundary.file_path}:{boundary.start_line}",
        )
        grouped.pop(key, None)
    for boundary in grouped.values():
        if boundary.preprocessor_conditional:
            warn(
                "preprocessor",
                boundary,
                f"preprocessor-dependent function metrics remain estimated: "
                f"{boundary.file_path}:{boundary.start_line}",
            )
    ordered = sorted(
        grouped.values(),
        key=lambda item: (item.file_path, item.start_line, item.start_column or 0, item.name),
    )
    return ordered, warnings
