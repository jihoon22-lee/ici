"""C++ complexity scanning — function spans, decision counts, metric details.

Extracted from the stable ``ici.engines.complexity`` shell. ``languages.metrics``
and the moved ``analysis._cpp_cognitive``/``analysis._dup_regions`` cores share
these primitives; the remaining ``ComplexityEngine`` imports them back.
"""

import re
from dataclasses import dataclass, field

from ici.analysis.cpp_text import (
    cpp_definition_name,
    cpp_function_like_macro_names,
    cpp_has_conditional_directive,
    cpp_is_operator_name,
    cpp_requires_expression_before_brace,
    mask_cpp_lambda_bodies,
    mask_cpp_literals,
    mask_cpp_preprocessor_directives,
)
from ici.core.models import InspectionTarget, ToolEvidence

# --- C++ function scanning -------------------------------------------------
#
# The previous implementation guessed at function boundaries by looking for a
# line that contained "(", ")", "{" and one of a handful of return-type
# keywords. That misread C++ in three separate ways, each of which put the
# reported complexity on the wrong function:
#
#   * A definition written on one line — `void Stats::add(const T& r) { v_.push_back(r); }`
#     — never closed, because the scanner skipped the signature line when
#     counting braces. It kept accumulating and absorbed the functions after it.
#   * `for (int i = 0; i < n; ++i) {` matched the heuristic (it has parentheses,
#     a brace, and "int "), so a loop was reported as a function named "for" and
#     the real function was truncated at that line.
#   * A signature split across lines was never detected at all, so its body was
#     attributed to whatever came before it.
#
# Tracking brace depth and accumulating the signature until its opening brace
# removes all three. Names are rejected when the token before "(" is a control
# keyword, which is what keeps loops and conditionals out of the results.

_CPP_DECISION_TOKENS = ("&&", "||", "?")

_CPP_DECISION_HEAD_RE = re.compile(
    r"\b(?:for|while|catch)\s*\(|\bif\s+(?:constexpr\s*\(|!?consteval\b)|\bif\s*\("
)
_CPP_CASE_RE = re.compile(r"\bcase\b[^:\n]*:")
_CPP_LAMBDA_INITIALIZER_RE = re.compile(r"(?<![=!<>])=\s*(?:\+\s*)?\[")
_MAX_CPP_COMPLEXITY_SOURCES = 2_048
_MAX_CPP_COMPLEXITY_SOURCE_BYTES = 64 * 1024 * 1024


class _CppFunctionSpan:
    """One C++ function definition and its measured complexity."""

    __slots__ = (
        "body_start_column",
        "body_start_line",
        "complexity",
        "end_column",
        "end_line",
        "excluded_lambdas",
        "function_kind",
        "is_template",
        "max_nesting",
        "name",
        "preprocessor_conditional",
        "start_column",
        "start_line",
    )

    def __init__(
        self,
        name: str,
        start_line: int,
        start_column: int,
        body_start_line: int,
        body_start_column: int,
        *,
        function_kind: str,
        is_template: bool,
    ) -> None:
        self.name = name
        self.start_line = start_line
        self.start_column = start_column
        self.body_start_line = body_start_line
        self.body_start_column = body_start_column
        self.end_line = start_line
        self.end_column: int | None = None
        self.complexity = 1
        self.excluded_lambdas = 0
        self.function_kind = function_kind
        self.is_template = is_template
        self.preprocessor_conditional = False
        # Counted the way the Python side counts it: how many blocks deep the
        # code goes *inside* the function, so a body with no branches is 0.
        self.max_nesting = 0


def _cpp_decision_count(text: str) -> int:
    """Count decision points on one line, the usual cyclomatic contributors."""
    total = sum(text.count(token) for token in _CPP_DECISION_TOKENS)
    return total + len(_CPP_DECISION_HEAD_RE.findall(text)) + len(_CPP_CASE_RE.findall(text))


def _cpp_function_spans(lines: list[str]) -> list[_CppFunctionSpan]:
    """Scan a translation unit and measure every function definition in it."""
    spans, _metric_lines = _cpp_function_inventory(lines)
    return spans


def _cpp_function_inventory(
    lines: list[str],
) -> tuple[list[_CppFunctionSpan], list[str]]:
    # Mask the complete file in one pass so block comments, raw strings, and
    # line-spliced comments cannot leak fake braces across line boundaries.
    masked_source = mask_cpp_literals("\n".join(lines)).replace("<%", "{ ").replace("%>", "} ")
    scanner = _CppScanner(cpp_function_like_macro_names(masked_source))
    metric_lines = masked_source.splitlines()
    scanner_lines = mask_cpp_preprocessor_directives(masked_source).splitlines()
    for number, text in enumerate(scanner_lines, 1):
        scanner.feed(number, text)
    scanner.finish(len(lines))
    for span in scanner.spans:
        if span.end_column is None:
            continue
        details = _cpp_metric_details_from_lines(
            metric_lines,
            span.body_start_line,
            span.body_start_column,
            span.end_line,
            span.end_column,
        )
        span.complexity = details[0]
        span.max_nesting = details[1]
        span.excluded_lambdas = details[2]
        span.preprocessor_conditional = details[3]
    return scanner.spans, metric_lines


def _cpp_metric_details_from_lines(
    lines: list[str],
    body_start_line: int,
    body_start_column: int,
    end_line: int,
    end_column: int,
) -> tuple[int, int, int, bool]:
    selected = lines[body_start_line - 1 : end_line]
    if not selected:
        return 1, 0, 0, False
    if body_start_line == end_line:
        selected[0] = selected[0][body_start_column - 1 : end_column]
    else:
        selected[0] = selected[0][body_start_column - 1 :]
        selected[-1] = selected[-1][:end_column]
    body = "\n".join(selected)
    metric_body, lambda_ranges = mask_cpp_lambda_bodies(body)
    complexity = 1 + sum(_cpp_decision_count(line) for line in metric_body.splitlines())
    depth = 0
    max_nesting = 0
    for char in metric_body:
        if char == "{":
            depth += 1
            max_nesting = max(max_nesting, max(0, depth - 1))
        elif char == "}":
            depth = max(0, depth - 1)
    return complexity, max_nesting, len(lambda_ranges), cpp_has_conditional_directive(metric_body)


@dataclass
class _CppComplexityAnalysis:
    max_complexity: int = 0
    targets: list[InspectionTarget] = field(default_factory=list)
    tool_evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    boundary_mode: str = "not_applicable"
    exact_boundaries: int = 0
    estimated_boundaries: int = 0
    configurations_checked: int = 0
    sources_checked: int = 0
    lambdas_excluded: int = 0
    macro_functions_excluded: int = 0


_CppSourceRows = dict[str, tuple[str, list[_CppFunctionSpan], list[str]]]


class _CppScanner:
    """Brace-depth state machine over one file."""

    def __init__(self, macro_names: frozenset[str] = frozenset()) -> None:
        self.spans: list[_CppFunctionSpan] = []
        self._depth = 0
        self._current: _CppFunctionSpan | None = None
        self._base_depth = 0
        self._pending = ""
        self._pending_line = 0
        self._pending_column = 0
        self._expression_depth = 0
        self._function_try = False
        self._awaiting_catch = False
        self._catch_header = False
        self._macro_names = macro_names

    def feed(self, number: int, text: str) -> None:
        remaining = text
        column = 1
        while remaining:
            if self._current is not None:
                consumed = self._feed_body(number, remaining, column)
            elif self._expression_depth:
                consumed = self._feed_expression(remaining)
            else:
                consumed = self._feed_outside(number, remaining, column)
            if consumed <= 0:
                if self._current is None:
                    continue
                return
            remaining = remaining[consumed:]
            column += consumed

    def finish(self, last_line: int) -> None:
        """Close an unterminated function so its findings are still reported."""
        if self._current is None:
            return
        self._current.end_line = last_line
        self.spans.append(self._current)
        self._current = None

    def _feed_body(self, number: int, text: str, column: int) -> int:
        current = self._current
        if current is None:
            return len(text)
        if self._awaiting_catch:
            return self._feed_catch(number, text, column)
        close_at: int | None = None
        for index, char in enumerate(text):
            if char == "{":
                self._depth += 1
                # The function's own brace is not nesting; only blocks inside
                # it contribute to the shared Python/C++ nesting policy.
                current.max_nesting = max(
                    current.max_nesting,
                    self._depth - self._base_depth - 1,
                )
            elif char == "}":
                self._depth -= 1
                if self._depth <= self._base_depth:
                    close_at = index
                    break
        body = text if close_at is None else text[: close_at + 1]
        current.complexity += _cpp_decision_count(body)
        if close_at is None:
            return len(text)
        current.end_line = number
        current.end_column = column + close_at
        self._depth = max(self._depth, self._base_depth)
        if self._function_try:
            self._awaiting_catch = True
            return close_at + 1
        self.spans.append(current)
        self._current = None
        return close_at + 1

    def _feed_catch(self, number: int, text: str, column: int) -> int:
        current = self._current
        if current is None:
            return 0
        stripped = text.lstrip()
        leading = len(text) - len(stripped)
        if not stripped:
            return len(text)
        if not self._catch_header:
            match = re.match(r"catch\b", stripped)
            if match is None:
                self.spans.append(current)
                self._current = None
                self._function_try = False
                self._awaiting_catch = False
                return 0
            current.complexity += 1
            self._catch_header = True
        opening = text.find("{")
        if opening < 0:
            return len(text)
        self._catch_header = False
        self._awaiting_catch = False
        self._depth = self._base_depth + 1
        current.max_nesting = max(current.max_nesting, 0)
        return max(opening + 1, leading + 1)

    def _append_pending(self, number: int, column: int, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        if not self._pending:
            self._pending_line = number
            self._pending_column = column + len(text) - len(text.lstrip())
        self._pending = f"{self._pending} {stripped}".strip()

    def _clear_pending(self) -> None:
        self._pending = ""
        self._pending_line = 0
        self._pending_column = 0

    def _feed_expression(self, text: str) -> int:
        for index, char in enumerate(text):
            if char == "{":
                self._expression_depth += 1
            elif char == "}":
                self._expression_depth -= 1
                if self._expression_depth == 0:
                    self._pending = f"{self._pending} {{}}".strip()
                    return index + 1
        return len(text)

    def _feed_outside(self, number: int, text: str, column: int) -> int:
        if text.lstrip().startswith("#"):
            self._clear_pending()
            return len(text)
        if not self._pending and self._standalone_macro_invocation(text):
            self._clear_pending()
            return len(text)
        delimiters = [(index, char) for index, char in enumerate(text) if char in "{};"]
        if not delimiters:
            self._append_pending(number, column, text)
            return len(text)
        index, delimiter = delimiters[0]
        self._append_pending(number, column, text[:index])
        if delimiter == ";":
            self._clear_pending()
            return index + 1
        if delimiter == "}":
            self._clear_pending()
            self._depth = max(0, self._depth - 1)
            return index + 1

        signature = self._pending
        parentheses, brackets = _cpp_delimiter_depth(signature)
        if (
            parentheses
            or brackets
            or _CPP_LAMBDA_INITIALIZER_RE.search(signature) is not None
            or cpp_requires_expression_before_brace(signature)
            or _cpp_constructor_initializer_candidate(signature)
        ):
            self._expression_depth = 1
            return index + 1
        name = cpp_definition_name(signature)
        if name is None:
            self._clear_pending()
            self._depth += 1
            return index + 1
        span = _CppFunctionSpan(
            name,
            self._pending_line,
            self._pending_column,
            number,
            column + index,
            function_kind="operator" if cpp_is_operator_name(name) else "function",
            is_template=bool(re.search(r"\btemplate\s*<", signature)),
        )
        self._clear_pending()
        self._base_depth = self._depth
        self._depth += 1
        self._current = span
        self._function_try = bool(re.search(r"\btry\b", signature))
        span.complexity += _cpp_decision_count(signature)
        return index + 1

    def _standalone_macro_invocation(self, text: str) -> bool:
        match = re.fullmatch(
            r"[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\(.*\)[ \t]*",
            text,
        )
        if match is None:
            return False
        name = match.group("name")
        return name in self._macro_names or (
            self._depth == 0 and name == name.upper() and any(char.isalpha() for char in name)
        )


def _cpp_delimiter_depth(text: str) -> tuple[int, int]:
    parentheses = 0
    brackets = 0
    for char in text:
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
    return parentheses, brackets


def _cpp_constructor_initializer_candidate(text: str) -> bool:
    parentheses = 0
    brackets = 0
    has_colon = False
    for index, char in enumerate(text):
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
            and (index == 0 or text[index - 1] != ":")
            and (index + 1 == len(text) or text[index + 1] != ":")
        ):
            has_colon = True
    return has_colon and bool(re.search(r"(?:[A-Za-z_][A-Za-z0-9_]*|[>\]])$", text.rstrip()))
