"""Bounded C++ cognitive-complexity metric over one function body.

The metric is deliberately lexical — function regions arrive pre-sliced,
so the score here is a property of the token stream, reported by the
caller as estimated evidence. Nested lambda bodies and preprocessor
directive text are masked so they cannot be charged to their enclosing
function.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ici.analysis.cpp_text import (
    cpp_has_conditional_directive,
    mask_cpp_lambda_bodies,
    mask_cpp_literals,
    mask_cpp_preprocessor_directives,
)

_MAX_SOURCES = 2_048
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|&&|\|\||::|->|\[\[|\]\]|[{}\[\](),;?:!]")
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


def cpp_cognitive_metric(body: str) -> CppCognitiveMetric:
    """Calculate a bounded Sonar-inspired metric from one function body.

    The parser intentionally does not claim full C++ grammar semantics. Braced
    control flow has exact lexical nesting; an unbraced control is counted but
    flagged so consumers can see the lower-confidence part of the metric.
    """

    masked = mask_cpp_literals(body)
    # Preserve byte offsets while normalizing brace digraphs so the shared
    # lambda masker recognizes both standard spellings.  The added spaces are
    # discarded by tokenization and keep newlines/source geometry unchanged.
    structural = masked.replace("<%", "{ ").replace("%>", "} ")
    without_lambdas, lambda_ranges = mask_cpp_lambda_bodies(structural)
    conditional = cpp_has_conditional_directive(without_lambdas)
    directive_free = mask_cpp_preprocessor_directives(without_lambdas)
    # Alternative brace tokens are part of the C++ grammar. They are replaced
    # only after literal/comment masking so text inside a string cannot become
    # structure.
    tokens = _TOKEN_RE.findall(directive_free)
    if len(tokens) > _MAX_TOKENS_PER_FUNCTION:
        raise ValueError("function token count exceeds the bounded limit")
    _validate_delimiters(tokens)

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


def _validate_delimiters(tokens: list[str]) -> None:
    """Reject mismatched structural delimiters before estimating flow."""

    pairs = {")": "(", "]": "[", "}": "{", "]]": "[["}
    opening = frozenset(pairs.values())
    stack: list[str] = []
    for token in tokens:
        if token in opening:
            stack.append(token)
        elif token in pairs and (not stack or stack.pop() != pairs[token]):
            raise ValueError("mismatched delimiter in function body")
    if stack:
        raise ValueError("unclosed delimiter in function body")


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
        self.loop_depth = 0
        self.switch_depth = 0

    def parse(self) -> None:
        # A compiler/source-scanner function range starts at its body brace. A
        # function-try-block then appends handlers outside that first compound,
        # so those top-level catches are valid even though the leading `try`
        # keyword lies before the measured body slice.
        if self._peek() == "{":
            self._compound(0)
            while self._peek() == "catch":
                self._catch(0)
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

    def _attributes(self) -> None:
        while self._peek() == "[[":
            self._balanced("[[", "]]")

    def _if(self, nesting: int, *, score: bool = True) -> None:
        score_if = score
        chain_length = 0
        while True:
            chain_length += 1
            if chain_length > _MAX_CONTROL_NESTING:
                raise ValueError("else-if chain exceeds the bounded limit")
            self._take("if")
            if score_if:
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
            self._attributes()
            if is_consteval and self._peek() != "{":
                raise ValueError("if consteval requires a compound statement")
            self._controlled_body(nesting + 1)
            if self._peek() != "else":
                return
            self._take("else")
            self.cognitive += 1 + nesting
            self._attributes()
            if self._peek() != "if":
                if is_consteval and self._peek() != "{":
                    raise ValueError("if consteval else requires a compound statement")
                self._controlled_body(nesting + 1)
                return
            # An else-if extends the current decision chain; the else branch is
            # the increment, rather than an additional nested `if` increment.
            score_if = False

    def _do(self, nesting: int) -> None:
        self._take("do")
        self.cognitive += 1 + nesting
        self._attributes()
        self.loop_depth += 1
        try:
            self._controlled_body(nesting + 1)
        finally:
            self.loop_depth -= 1
        self._take("while")
        self._header()
        self._take(";")

    def _catch(self, nesting: int) -> None:
        self._take("catch")
        self.cognitive += 1 + nesting
        self._header()
        self._attributes()
        self._controlled_body(nesting + 1)

    def _try(self, nesting: int) -> None:
        self._take("try")
        if self._peek() != "{":
            raise ValueError("try must be followed by a compound statement")
        self._compound(nesting)
        catches = 0
        while self._peek() == "catch":
            catches += 1
            self._catch(nesting)
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
        control = self._take()
        self.cognitive += 1 + nesting
        self._header()
        self._attributes()
        if control in {"for", "while"}:
            self.loop_depth += 1
        elif control == "switch":
            self.switch_depth += 1
        try:
            self._controlled_body(nesting + 1)
        finally:
            if control in {"for", "while"}:
                self.loop_depth -= 1
            elif control == "switch":
                self.switch_depth -= 1

    def _validate_simple_start(self, first: str | None) -> None:
        if first in {"case", "default"} and self.switch_depth == 0:
            raise ValueError(f"{first} label is outside a switch statement")
        if first == "break" and self.loop_depth == 0 and self.switch_depth == 0:
            raise ValueError("break statement is outside a loop or switch")
        if first == "continue" and self.loop_depth == 0:
            raise ValueError("continue statement is outside a loop")

    def _starts_statement_after_label(
        self,
        start: int,
        token: str | None,
        parentheses: int,
        initializer_braces: int,
    ) -> bool:
        return (
            self.position > start
            and parentheses == 0
            and initializer_braces == 0
            and (token in _CONTROL_WORDS or token in {"catch", "else", "try"})
        )

    def _consume_simple_token(
        self,
        token: str,
        parentheses: int,
        initializer_braces: int,
    ) -> tuple[int, int, str | None, bool]:
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
                return parentheses, initializer_braces, None, True

        self.position += 1
        if token == ":" and parentheses == 0 and initializer_braces == 0:
            return parentheses, initializer_braces, "label", False
        if token == ";" and parentheses == 0 and initializer_braces == 0:
            return parentheses, initializer_braces, "terminated", False
        return parentheses, initializer_braces, None, False

    def _simple(self) -> None:
        start = self.position
        self._validate_simple_start(self._peek())
        parentheses = 0
        initializer_braces = 0
        terminal: str | None = None
        while self.position < len(self.tokens):
            token = self._peek()
            if self._starts_statement_after_label(start, token, parentheses, initializer_braces):
                # A label (`case value:`, `default:`, or a user label) may be
                # followed immediately by a controlled statement. Do not let
                # the label's otherwise-simple token run swallow that flow.
                break
            if token is None:
                break
            parentheses, initializer_braces, terminal, stop = self._consume_simple_token(
                token, parentheses, initializer_braces
            )
            if terminal is not None or stop:
                break
        if parentheses or initializer_braces:
            raise ValueError("unclosed expression delimiter in function body")
        if terminal is None:
            raise ValueError("simple statement is missing its terminating semicolon")

    def _statement(self, nesting: int) -> None:
        self.recursion_depth += 1
        try:
            if self.recursion_depth > _MAX_CONTROL_NESTING:
                raise ValueError("control nesting exceeds the bounded limit")
            # An attribute-specifier-seq may prefix any C++ statement, not only
            # a controlled body.  Consume it before dispatch so e.g.
            # ``[[likely]] if (...)`` retains the following statement shape.
            self._attributes()
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
