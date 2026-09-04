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
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||::|->|[{}(),;?:!]")
_CONTROL_WORDS = frozenset({"if", "for", "while", "switch", "do"})
_JUMP_WORDS = frozenset({"break", "continue", "goto"})
_LOGICAL_WORDS = {"&&": "and", "and": "and", "||": "or", "or": "or"}
_MAX_TOKENS_PER_FUNCTION = 1_000_000
_MAX_CONTROL_NESTING = 128


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
    functions_analyzed: int = 0


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
    start_width = len(lines[start_line - 1]) + 1
    end_width = len(lines[end_line - 1]) + 1
    if start_column > start_width or end_column > end_width:
        raise ValueError("function body column is outside its source line")
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
    directive_free = mask_cpp_preprocessor_directives(without_lambdas)
    # Alternative brace tokens are part of the C++ grammar. They are replaced
    # only after literal/comment masking so text inside a string cannot become
    # structure.
    tokens = _TOKEN_RE.findall(directive_free.replace("<%", "{").replace("%>", "}"))
    if len(tokens) > _MAX_TOKENS_PER_FUNCTION:
        raise ValueError("function token count exceeds the bounded limit")

    expression_cognitive = 0
    logical_operator: str | None = None
    logical_sequences = 0
    for token in tokens:
        normalized_logical = _LOGICAL_WORDS.get(token)
        if normalized_logical is not None:
            if logical_operator != normalized_logical:
                expression_cognitive += 1
                logical_sequences += 1
                logical_operator = normalized_logical
            continue
        if token in _JUMP_WORDS:
            expression_cognitive += 1
            continue
        if token == "?":
            expression_cognitive += 1
            logical_operator = None
            continue
        # Each expression/statement boundary starts a new logical sequence.
        # Commas and for-header semicolons count even inside parentheses.
        if token in {",", ";", "{", "}", ":"} or token in _CONTROL_WORDS | {
            "catch",
            "else",
        }:
            logical_operator = None

    parser = _CppControlParser(tokens)
    parser.parse()
    return CppCognitiveMetric(
        cognitive=parser.cognitive + expression_cognitive,
        max_nesting=parser.max_nesting,
        unbraced_controls=parser.unbraced_controls,
        logical_sequences=logical_sequences,
        excluded_lambdas=len(lambda_ranges),
        preprocessor_conditional=conditional,
    )


class _CppControlParser:
    """Small statement parser used only for nesting weights.

    It intentionally ignores types and expressions, but unlike a pending-token
    counter it understands the recursive shape of controlled statements. This
    is enough to distinguish nested unbraced flow, do/while tails, and
    initializer-list braces without claiming compiler-grade semantics.
    """

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.position = 0
        self.cognitive = 0
        self.max_nesting = 0
        self.unbraced_controls = 0
        self.recursion_depth = 0

    def parse(self) -> None:
        while self.position < len(self.tokens):
            self._statement(0)

    def _peek(self) -> str | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _take(self, expected: str | None = None) -> str:
        token = self._peek()
        if token is None:
            raise ValueError("function token stream ended unexpectedly")
        if expected is not None and token != expected:
            raise ValueError(f"expected {expected!r}, found {token!r}")
        self.position += 1
        return token

    def _balanced(self, opening: str, closing: str) -> None:
        self._take(opening)
        depth = 1
        while depth:
            token = self._take()
            if token == opening:
                depth += 1
            elif token == closing:
                depth -= 1

    def _compound(self, nesting: int) -> None:
        self._take("{")
        while self._peek() not in {None, "}"}:
            self._statement(nesting)
        self._take("}")

    def _controlled_body(self, nesting: int) -> None:
        if self._peek() == "{":
            self.max_nesting = max(self.max_nesting, nesting)
            self._compound(nesting)
            return
        self.unbraced_controls += 1
        self._statement(nesting)

    def _header(self) -> None:
        if self._peek() != "(":
            raise ValueError("control statement is missing its parenthesized header")
        self._balanced("(", ")")

    def _if(self, nesting: int, *, score: bool = True) -> None:
        self._take("if")
        if score:
            self.cognitive += 1 + nesting
        if self._peek() == "constexpr":
            self._take("constexpr")
        is_consteval = False
        if self._peek() == "!":
            self._take("!")
            if self._peek() != "consteval":
                raise ValueError("if ! must be followed by consteval")
        if self._peek() == "consteval":
            self._take("consteval")
            is_consteval = True
        else:
            self._header()
        if is_consteval and self._peek() != "{":
            raise ValueError("if consteval requires a compound statement")
        self._controlled_body(nesting + 1)
        if self._peek() != "else":
            return
        self._take("else")
        self.cognitive += 1 + nesting
        if self._peek() == "if":
            # An else-if extends the current decision chain; the else branch is
            # the increment, rather than an additional nested `if` increment.
            self._if(nesting, score=False)
        else:
            if is_consteval and self._peek() != "{":
                raise ValueError("if consteval else requires a compound statement")
            self._controlled_body(nesting + 1)

    def _do(self, nesting: int) -> None:
        self._take("do")
        self.cognitive += 1 + nesting
        self._controlled_body(nesting + 1)
        self._take("while")
        self._header()
        if self._peek() == ";":
            self._take(";")

    def _try(self, nesting: int) -> None:
        self._take("try")
        if self._peek() != "{":
            raise ValueError("try must be followed by a compound statement")
        self._compound(nesting)
        catches = 0
        while self._peek() == "catch":
            catches += 1
            self._take("catch")
            self.cognitive += 1 + nesting
            self._header()
            self._controlled_body(nesting + 1)
        if catches == 0:
            raise ValueError("try statement is missing a catch handler")

    def _control(self, nesting: int) -> None:
        token = self._peek()
        if token == "if":
            self._if(nesting)
            return
        if token == "do":
            self._do(nesting)
            return
        if token not in _CONTROL_WORDS:
            raise ValueError("internal control parser mismatch")
        self._take()
        self.cognitive += 1 + nesting
        self._header()
        self._controlled_body(nesting + 1)

    def _simple(self) -> None:
        start = self.position
        parentheses = 0
        initializer_braces = 0
        while self.position < len(self.tokens):
            token = self._peek()
            if (
                self.position > start
                and parentheses == 0
                and initializer_braces == 0
                and (token in _CONTROL_WORDS or token in {"catch", "else", "try"})
            ):
                # A label (`case value:`, `default:`, or a user label) may be
                # followed immediately by a controlled statement. Do not let
                # the label's otherwise-simple token run swallow that flow.
                break
            if token == "(":
                parentheses += 1
            elif token == ")":
                if parentheses == 0:
                    raise ValueError("unmatched closing parenthesis in function body")
                parentheses -= 1
            elif token == "{":
                initializer_braces += 1
            elif token == "}":
                if initializer_braces:
                    initializer_braces -= 1
                elif parentheses == 0:
                    break
            self.position += 1
            if token == ":" and parentheses == 0 and initializer_braces == 0:
                break
            if token == ";" and parentheses == 0 and initializer_braces == 0:
                break
        if parentheses or initializer_braces:
            raise ValueError("unclosed expression delimiter in function body")

    def _statement(self, nesting: int) -> None:
        self.recursion_depth += 1
        try:
            if self.recursion_depth > _MAX_CONTROL_NESTING:
                raise ValueError("control nesting exceeds the bounded limit")
            token = self._peek()
            if token is None:
                return
            if token == "{":
                self._compound(nesting)
            elif token == "try":
                self._try(nesting)
            elif token in _CONTROL_WORDS:
                self._control(nesting)
            elif token == "catch":
                raise ValueError("catch handler is missing its matching try")
            elif token == "else":
                raise ValueError("else statement is missing its matching if")
            elif token == "}":
                raise ValueError("unmatched closing brace in function body")
            else:
                self._simple()
        finally:
            self.recursion_depth -= 1


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
        except (OSError, UnicodeError, ValueError) as err:
            try:
                relative = source.relative_to(project_root).as_posix()
            except ValueError:
                relative = source.name
            message = (
                f"C++ cognitive source is not a bounded project file: {relative} "
                f"({type(err).__name__})"
            )
            outcome.errors.append(message)
            outcome.targets.append(
                InspectionTarget(
                    file_path=relative,
                    start_line=1,
                    target_name="CppCognitiveSourceError",
                    status=EngineStatus.ERROR,
                    message=message,
                    metrics={"boundary_source": "source-intake"},
                )
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
            message = f"C++ cognitive function range is invalid: {boundary.file_path}: {err}"
            outcome.errors.append(message)
            outcome.targets.append(
                InspectionTarget(
                    file_path=boundary.file_path,
                    start_line=boundary.start_line,
                    end_line=boundary.end_line,
                    start_column=boundary.start_column,
                    end_column=boundary.end_column,
                    target_name=boundary.name,
                    status=EngineStatus.ERROR,
                    message=message,
                    metrics={"boundary_source": "clang-tidy-ast"},
                )
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
        outcome.functions_analyzed += 1
        outcome.max_cognitive = max(outcome.max_cognitive, metric.cognitive)
        index = _matching_span(boundary, spans)
        if index is not None:
            matched[boundary.file_path].add(index)

    for path, (text, spans) in source_rows.items():
        for index, span in enumerate(spans):
            if index in matched[path]:
                continue
            if span.end_column is None:
                message = f"C++ cognitive function range is unterminated: {path}:{span.start_line}"
                outcome.errors.append(message)
                outcome.targets.append(
                    InspectionTarget(
                        file_path=path,
                        start_line=span.start_line,
                        end_line=span.end_line,
                        start_column=span.start_column,
                        target_name=span.name,
                        status=EngineStatus.ERROR,
                        message=message,
                        metrics={"boundary_source": "source-scanner"},
                    )
                )
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
                message = f"C++ cognitive function range is invalid: {path}: {err}"
                outcome.errors.append(message)
                outcome.targets.append(
                    InspectionTarget(
                        file_path=path,
                        start_line=span.start_line,
                        end_line=span.end_line,
                        start_column=span.start_column,
                        end_column=span.end_column,
                        target_name=span.name,
                        status=EngineStatus.ERROR,
                        message=message,
                        metrics={"boundary_source": "source-scanner"},
                    )
                )
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
            outcome.functions_analyzed += 1
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
