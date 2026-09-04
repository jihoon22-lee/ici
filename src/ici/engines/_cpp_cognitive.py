"""Bounded C++ cognitive-complexity metrics over verified function regions.

Clang-backed function boundaries identify which source ranges are functions;
the metric itself is deliberately lexical and is therefore reported as
estimated evidence. Nested lambda bodies and preprocessor directive text are
excluded so they cannot be charged to their enclosing function.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ici.core.context import AnalysisContext
from ici.core.models import EngineStatus, InspectionTarget, ToolEvidence
from ici.core.runner import ProcessResult
from ici.engines._cpp_function_boundaries import (
    CppFunctionBoundary,
    read_cpp_source_text,
    run_cpp_function_boundaries,
)
from ici.engines.complexity import _cpp_function_inventory, _CppFunctionSpan
from ici.engines.cpp_text import (
    cpp_has_conditional_directive,
    mask_cpp_lambda_bodies,
    mask_cpp_literals,
    mask_cpp_preprocessor_directives,
)

_MAX_SOURCES = 2_048
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||::|->|[{}();?:]")
_CONTROL_WORDS = frozenset({"if", "for", "while", "switch", "catch", "do"})
_JUMP_WORDS = frozenset({"break", "continue", "goto"})


@dataclass(frozen=True)
class CppCognitiveMetric:
    """One lexical metric associated with one source-spelled function."""

    cognitive: int
    max_nesting: int
    unbraced_controls: int
    logical_sequences: int
    excluded_lambdas: int
    preprocessor_conditional: bool


@dataclass
class CppCognitiveOutcome:
    """C++ cognitive analysis result before engine policy projection."""

    targets: list[InspectionTarget] = field(default_factory=list)
    evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    boundary_mode: str = "unavailable"
    exact_boundaries: int = 0
    estimated_boundaries: int = 0
    configurations_checked: int = 0
    sources_checked: int = 0
    lambdas_excluded: int = 0
    macro_functions_excluded: int = 0
    max_cognitive: int = 0


def _source_slice(
    text: str,
    start_line: int,
    start_column: int,
    end_line: int,
    end_column: int,
) -> str:
    lines = text.splitlines()
    if (
        start_line < 1
        or end_line < start_line
        or start_line > len(lines)
        or end_line > len(lines)
        or start_column < 1
        or end_column < 1
    ):
        raise ValueError("function body range is outside its source")
    selected = lines[start_line - 1 : end_line]
    if start_line == end_line:
        if end_column < start_column:
            raise ValueError("function body columns are reversed")
        selected[0] = selected[0][start_column - 1 : end_column]
    else:
        selected[0] = selected[0][start_column - 1 :]
        selected[-1] = selected[-1][:end_column]
    return "\n".join(selected)


def cpp_cognitive_metric(body: str) -> CppCognitiveMetric:
    """Calculate a bounded Sonar-inspired metric from one function body.

    The parser intentionally does not claim full C++ grammar semantics. Braced
    control flow has exact lexical nesting; an unbraced control is counted but
    flagged so consumers can see the lower-confidence part of the metric.
    """

    masked = mask_cpp_literals(body)
    without_lambdas, lambda_ranges = mask_cpp_lambda_bodies(masked)
    conditional = cpp_has_conditional_directive(without_lambdas)
    tokens = _TOKEN_RE.findall(mask_cpp_preprocessor_directives(without_lambdas))

    cognitive = 0
    max_nesting = 0
    control_depth = 0
    block_controls: list[bool] = []
    pending_control: str | None = None
    logical_operator: str | None = None
    logical_sequences = 0
    unbraced_controls = 0
    skip_if_after_else = False
    parenthesis_depth = 0

    for index, token in enumerate(tokens):
        if skip_if_after_else and token == "if":
            skip_if_after_else = False
            continue
        skip_if_after_else = False

        if token == "else":
            cognitive += 1 + control_depth
            pending_control = "else"
            if index + 1 < len(tokens) and tokens[index + 1] == "if":
                pending_control = "if"
                skip_if_after_else = True
            logical_operator = None
            continue
        if token in _CONTROL_WORDS:
            cognitive += 1 + control_depth
            pending_control = token
            logical_operator = None
            continue
        if token in _JUMP_WORDS:
            cognitive += 1
            continue
        if token == "?":
            cognitive += 1
            continue
        if token in {"&&", "||"}:
            if logical_operator != token:
                cognitive += 1
                logical_sequences += 1
                logical_operator = token
            continue
        if token == "(":
            parenthesis_depth += 1
            continue
        if token == ")":
            parenthesis_depth = max(0, parenthesis_depth - 1)
            continue
        if token == "{":
            is_control = pending_control is not None
            block_controls.append(is_control)
            if is_control:
                control_depth += 1
                max_nesting = max(max_nesting, control_depth)
            pending_control = None
            logical_operator = None
            continue
        if token == "}":
            if block_controls and block_controls.pop():
                control_depth = max(0, control_depth - 1)
            pending_control = None
            logical_operator = None
            continue
        if token == ";":
            if parenthesis_depth == 0 and pending_control is not None:
                unbraced_controls += 1
                pending_control = None
            if parenthesis_depth == 0:
                logical_operator = None

    if pending_control is not None:
        unbraced_controls += 1
    return CppCognitiveMetric(
        cognitive=cognitive,
        max_nesting=max_nesting,
        unbraced_controls=unbraced_controls,
        logical_sequences=logical_sequences,
        excluded_lambdas=len(lambda_ranges),
        preprocessor_conditional=conditional,
    )


def _matching_span(boundary: CppFunctionBoundary, spans: list[_CppFunctionSpan]) -> int | None:
    expected_name = boundary.name.removesuffix("()").rsplit("::", 1)[-1]
    candidates = [
        (index, span)
        for index, span in enumerate(spans)
        if span.body_start_line == boundary.body_start_line
        and span.body_start_column == boundary.body_start_column
        and span.end_line == boundary.end_line
        and span.end_column == boundary.end_column
        and span.name.removesuffix("()").rsplit("::", 1)[-1] == expected_name
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[1].end_line - item[1].start_line, item[0]))[0]


def _target(
    path: str,
    name: str,
    start_line: int,
    end_line: int,
    start_column: int | None,
    end_column: int | None,
    metric: CppCognitiveMetric,
    *,
    warn: int,
    fail: int,
    warn_nesting: int,
    boundary_source: str,
    configurations: tuple[str, ...] = (),
) -> InspectionTarget:
    status = EngineStatus.PASS
    if metric.cognitive >= fail:
        status = EngineStatus.FAIL
    elif metric.cognitive >= warn or metric.max_nesting >= warn_nesting:
        status = EngineStatus.WARN
    confidence = "low" if metric.preprocessor_conditional or metric.unbraced_controls else "medium"
    return InspectionTarget(
        file_path=path,
        start_line=start_line,
        end_line=end_line,
        start_column=start_column,
        end_column=end_column,
        target_name=name,
        status=status,
        message=(
            f"Cognitive {metric.cognitive} (control nesting {metric.max_nesting}, "
            f"estimated C++ metric)"
        ),
        metrics={
            "cognitive": metric.cognitive,
            "nesting": metric.max_nesting,
            "metric_source": "bounded-cpp-tokens",
            "metric_confidence": confidence,
            "boundary_source": boundary_source,
            "configurations": list(configurations),
            "logical_sequences": metric.logical_sequences,
            "unbraced_controls": metric.unbraced_controls,
            "excluded_nested_lambdas": metric.excluded_lambdas,
            "preprocessor_conditional": metric.preprocessor_conditional,
        },
    )


def analyze_cpp_cognitive(
    project_root: Path,
    cpp_files: list[Path],
    compilable_files: list[Path],
    context: AnalysisContext | None,
    *,
    warn: int,
    fail: int,
    warn_nesting: int,
    boundary_policy: str,
    runner: Callable[..., ProcessResult],
) -> CppCognitiveOutcome:
    """Analyze C++ functions with exact boundaries when available."""

    outcome = CppCognitiveOutcome(boundary_mode="heuristic")
    if len(cpp_files) > _MAX_SOURCES:
        outcome.errors.append("C++ cognitive source count exceeds the bounded limit")
        outcome.boundary_mode = "error"
        return outcome

    source_rows: dict[str, tuple[str, list[_CppFunctionSpan]]] = {}
    source_bytes = 0
    for source in cpp_files:
        try:
            relative = source.relative_to(project_root).as_posix()
            text = read_cpp_source_text(project_root, relative)
            spans, _metric_lines = _cpp_function_inventory(text.splitlines())
        except (OSError, UnicodeError, ValueError):
            outcome.errors.append(
                f"C++ cognitive source is not a bounded project file: {source.name}"
            )
            continue
        source_bytes += len(text.encode("utf-8"))
        if source_bytes > _MAX_SOURCE_BYTES:
            outcome.errors.append("C++ cognitive source inventory exceeds the bounded limit")
            source_rows.clear()
            break
        source_rows[relative] = (text, spans)
    if outcome.errors:
        outcome.boundary_mode = "error"
        return outcome

    boundaries: list[CppFunctionBoundary] = []
    if boundary_policy != "off":
        boundary_result = run_cpp_function_boundaries(
            project_root,
            compilable_files,
            context,
            runner=runner,
            source_texts={path: text for path, (text, _spans) in source_rows.items()},
        )
        outcome.evidence.extend(boundary_result.evidence)
        outcome.warnings.extend(boundary_result.warnings)
        outcome.configurations_checked = boundary_result.configurations_checked
        outcome.sources_checked = boundary_result.sources_checked
        outcome.lambdas_excluded = boundary_result.lambdas_excluded
        outcome.macro_functions_excluded = boundary_result.macro_functions_excluded
        if boundary_result.mode == "error":
            outcome.errors.extend(boundary_result.errors)
            outcome.boundary_mode = "error"
            return outcome
        if boundary_result.mode in {"exact", "partial"}:
            outcome.boundary_mode = boundary_result.mode
            boundaries = [
                item for item in boundary_result.boundaries if item.file_path in source_rows
            ]
        elif boundary_policy == "required":
            outcome.errors.append(
                "compiler-backed C++ cognitive boundaries require an exact compilation "
                "database and approved clang-tidy"
            )
            outcome.boundary_mode = "error"
            return outcome

    matched: dict[str, set[int]] = {path: set() for path in source_rows}
    for boundary in boundaries:
        text, spans = source_rows[boundary.file_path]
        try:
            body = _source_slice(
                text,
                boundary.body_start_line,
                boundary.body_start_column,
                boundary.end_line,
                boundary.end_column or 1,
            )
            metric = cpp_cognitive_metric(body)
        except ValueError as err:
            outcome.errors.append(
                f"C++ cognitive function range is invalid: {boundary.file_path}: {err}"
            )
            continue
        outcome.targets.append(
            _target(
                boundary.file_path,
                boundary.name,
                boundary.start_line,
                boundary.end_line,
                boundary.start_column,
                boundary.end_column,
                metric,
                warn=warn,
                fail=fail,
                warn_nesting=warn_nesting,
                boundary_source="clang-tidy-ast",
                configurations=boundary.configurations,
            )
        )
        outcome.exact_boundaries += 1
        outcome.max_cognitive = max(outcome.max_cognitive, metric.cognitive)
        index = _matching_span(boundary, spans)
        if index is not None:
            matched[boundary.file_path].add(index)

    for path, (text, spans) in source_rows.items():
        for index, span in enumerate(spans):
            if index in matched[path] or span.end_column is None:
                continue
            try:
                body = _source_slice(
                    text,
                    span.body_start_line,
                    span.body_start_column,
                    span.end_line,
                    span.end_column,
                )
                metric = cpp_cognitive_metric(body)
            except ValueError as err:
                outcome.errors.append(f"C++ cognitive function range is invalid: {path}: {err}")
                continue
            outcome.targets.append(
                _target(
                    path,
                    span.name,
                    span.start_line,
                    span.end_line,
                    span.start_column,
                    span.end_column,
                    metric,
                    warn=warn,
                    fail=fail,
                    warn_nesting=warn_nesting,
                    boundary_source="source-scanner",
                )
            )
            outcome.estimated_boundaries += 1
            outcome.max_cognitive = max(outcome.max_cognitive, metric.cognitive)

    if outcome.errors:
        outcome.boundary_mode = "error"
    elif boundary_policy == "required" and (
        outcome.boundary_mode == "partial" or outcome.estimated_boundaries
    ):
        outcome.errors.append(
            "compiler-backed C++ cognitive boundaries were required but remained partial"
        )
        outcome.boundary_mode = "error"
    elif outcome.exact_boundaries and outcome.estimated_boundaries:
        outcome.boundary_mode = "mixed"
    elif outcome.exact_boundaries and outcome.boundary_mode != "partial":
        outcome.boundary_mode = "exact"
    elif not outcome.exact_boundaries:
        outcome.boundary_mode = "heuristic"
    return outcome
