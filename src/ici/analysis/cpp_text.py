"""Shared C++ source-text helpers.

Blanking comments and quoted literals before scanning is something several
engines need: build counts ``int main`` definitions, exception looks for throws
inside destructors, and complexity locates function boundaries. All three were
about to carry a copy of the same routine, so it lives here once.

Regions are blanked in place rather than removed, so every line and column stays
aligned with the original file — engines report line numbers, and those offsets
have to survive the masking.
"""

import re
from pathlib import Path

CPP_RAW_START_RE = re.compile(r'(?:u8|u|U|L)?R"(?P<delimiter>[^\s()\\]{0,16})\(')

MAIN_DEFINITION_RE = re.compile(r"\bint\s+main\s*\([^{};]*\)\s*(?:noexcept\s*)?\{")
_REQUIRES_KEYWORD_RE = re.compile(r"\brequires\b")
_CPP_NON_FUNCTION_HEADS = frozenset(
    {
        "alignas",
        "catch",
        "decltype",
        "do",
        "else",
        "for",
        "if",
        "noexcept",
        "operator",
        "requires",
        "return",
        "sizeof",
        "static_assert",
        "switch",
        "typeid",
        "while",
    }
)
_CPP_NAME_AT_END_RE = re.compile(
    r"(?P<name>~?[A-Za-z_][A-Za-z0-9_]*(?:::\~?[A-Za-z_][A-Za-z0-9_]*)*)\s*$"
)
_CPP_CONDITIONAL_DIRECTIVE_RE = re.compile(
    r"^[ \t]*\#[ \t]*(?:if|ifdef|ifndef|elif|else|endif)\b", re.MULTILINE
)
_CPP_FUNCTION_LIKE_MACRO_RE = re.compile(
    r"^[ \t]*\#[ \t]*define[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\(",
    re.MULTILINE,
)
_MAX_CPP_LAMBDA_BODIES = 100_000
_MAX_CPP_SCOPE_DELIMITER_PAIRS = 200_000


def blank_cpp_region(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def cpp_line_comment_end(text: str, start: int) -> int:
    index = start + 2
    while index < len(text):
        if text[index] == "\n" and (index == start or text[index - 1] != "\\"):
            return index
        index += 1
    return len(text)


def cpp_quoted_end(text: str, start: int) -> int:
    quote = text[start]
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        if text[index] in "\r\n":
            return index + 1
        index += 1
    return len(text)


def _literal_operator_quotes(text: str, start: int) -> bool:
    if not text.startswith('""', start):
        return False
    prefix = text[max(0, start - 64) : start]
    return re.search(r"\boperator\s*$", prefix) is not None


def mask_cpp_literals(text: str) -> str:
    """Blank comments and quoted C++ literals while preserving source lines."""

    chars = list(text)
    index = 0
    while index < len(text):
        raw_start = CPP_RAW_START_RE.match(text, index)
        if raw_start:
            closing = ")" + raw_start.group("delimiter") + '"'
            closing_index = text.find(closing, raw_start.end())
            end = len(text) if closing_index < 0 else closing_index + len(closing)
            blank_cpp_region(chars, index, end)
            index = end
            continue
        if text.startswith("//", index):
            end = cpp_line_comment_end(text, index)
            blank_cpp_region(chars, index, end)
            index = end
            continue
        if text.startswith("/*", index):
            closing_index = text.find("*/", index + 2)
            end = len(text) if closing_index < 0 else closing_index + 2
            blank_cpp_region(chars, index, end)
            index = end
            continue
        if text[index] == '"' and _literal_operator_quotes(text, index):
            index += 2
            continue
        if text[index] in "\"'":
            end = cpp_quoted_end(text, index)
            blank_cpp_region(chars, index, end)
            index = end
            continue
        index += 1
    return "".join(chars)


def cpp_is_operator_name(name: str) -> bool:
    """Whether *name* spells the C++ ``operator`` keyword, not a prefix."""

    qualified = name.rfind("::operator")
    candidate = name[qualified + 2 :] if qualified >= 0 else name
    if not candidate.startswith("operator") or len(candidate) == len("operator"):
        return False
    following = candidate[len("operator")]
    return not (following.isalnum() or following == "_")


def _cpp_operator_name_at_end(prefix: str) -> str | None:
    matches = tuple(re.finditer(r"\boperator\b", prefix))
    if not matches:
        return None
    spelling = prefix[matches[-1].end() :].strip()
    if not spelling:
        # The first pair in ``operator()`` belongs to the name.  Its following
        # parameter pair will be considered by the outer declarator scan.
        return None
    if any(char in spelling for char in "(){};") and spelling != "()":
        # An ``operator`` token inside an earlier return/default/noexcept
        # expression is not the declarator name for the parameter pair now
        # being considered.
        return None
    if spelling == "()":
        return "operator()"
    if re.fullmatch(r"\[\s*\]", spelling):
        return "operator[]"
    literal = re.fullmatch(r'""\s*(?P<suffix>[A-Za-z_][A-Za-z0-9_]*)', spelling)
    if literal is not None:
        return f'operator""{literal.group("suffix")}'
    array_allocation = re.fullmatch(r"(?P<kind>new|delete)\s*\[\s*\]", spelling)
    if array_allocation is not None:
        return f"operator {array_allocation.group('kind')}[]"
    symbolic = re.fullmatch(r"[^A-Za-z0-9_\s]+", "".join(spelling.split()))
    if symbolic is not None:
        return f"operator{symbolic.group(0)}"
    return f"operator {' '.join(spelling.split())}"


def _top_level_parameter_openings(signature: str) -> tuple[int, ...]:
    """Return candidate declarator parentheses outside other delimiters."""

    parentheses = 0
    brackets = 0
    braces = 0
    openings: list[int] = []
    for index, char in enumerate(signature):
        if char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif char == "{":
            braces += 1
        elif char == "}":
            braces = max(0, braces - 1)
        elif char == "(":
            if parentheses == 0 and brackets == 0 and braces == 0:
                openings.append(index)
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
    return tuple(openings)


def cpp_definition_name(signature: str) -> str | None:
    """Return a source-spelled C++ definition name from a header fragment.

    This is intentionally a fallback helper, not a C++ parser.  It does,
    however, preserve the operator spelling that the old ``split("(")``
    heuristic lost: call/subscript operators contain a pair of parentheses or
    brackets *inside* their name, and conversion operators may contain spaces.
    """

    for opening in _top_level_parameter_openings(signature):
        prefix = signature[:opening].rstrip()
        operator_name = _cpp_operator_name_at_end(prefix)
        if operator_name is not None:
            return operator_name
        match = _CPP_NAME_AT_END_RE.search(prefix)
        if match is None:
            continue
        name = match.group("name")
        unqualified = name.rsplit("::", 1)[-1]
        if unqualified.removeprefix("~") in _CPP_NON_FUNCTION_HEADS:
            continue
        return name
    return None


def cpp_has_conditional_directive(text: str) -> bool:
    """Whether a source region contains a conditional preprocessing branch."""

    return _CPP_CONDITIONAL_DIRECTIVE_RE.search(text) is not None


def cpp_function_like_macro_names(masked_text: str) -> frozenset[str]:
    """Return source-defined function-like macro names from masked text."""

    return frozenset(
        match.group("name") for match in _CPP_FUNCTION_LIKE_MACRO_RE.finditer(masked_text)
    )


def mask_cpp_preprocessor_directives(masked_text: str) -> str:
    """Blank preprocessing directives, including backslash continuations."""

    chars = list(masked_text)
    offset = 0
    continued = False
    for line in masked_text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        directive = continued or body.lstrip().startswith("#")
        if directive:
            blank_cpp_region(chars, offset, offset + len(line))
            continued = body.rstrip().endswith("\\")
        else:
            continued = False
        offset += len(line)
    return "".join(chars)


def _matching_cpp_delimiter(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _previous_cpp_token(text: str, start: int) -> tuple[str, str]:
    index = start - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    if index < 0:
        return "", ""
    previous = text[index]
    if not (previous.isalnum() or previous == "_"):
        return previous, ""
    end = index + 1
    while index >= 0 and (text[index].isalnum() or text[index] == "_"):
        index -= 1
    return previous, text[index + 1 : end]


def _can_start_lambda_capture(text: str, start: int) -> bool:
    if text.startswith("[[", start) or (start > 0 and text[start - 1] == "["):
        return False
    previous, token = _previous_cpp_token(text, start)
    if token:
        return token in {"return", "co_return", "throw"}
    return previous in {"", "=", "(", "{", ",", ";", ":", "?", "!", "+", "-", "*", "&", "|"}


def _lambda_body_open(text: str, capture_end: int, stop: int) -> tuple[int | None, int]:
    parentheses = 0
    brackets = 0
    angles = 0
    index = capture_end + 1
    while index < stop:
        char = text[index]
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses = max(0, parentheses - 1)
        elif char == "[":
            brackets += 1
        elif char == "]":
            brackets = max(0, brackets - 1)
        elif char == "<" and parentheses == 0 and brackets == 0:
            angles += 1
        elif char == ">" and parentheses == 0 and brackets == 0:
            angles = max(0, angles - 1)
        elif char == ";" and parentheses == 0 and brackets == 0 and angles == 0:
            return None, index
        elif char == "{" and parentheses == 0 and brackets == 0 and angles == 0:
            prefix = text[capture_end + 1 : index]
            if cpp_requires_expression_before_brace(prefix):
                closing = _matching_cpp_delimiter(text, index, "{", "}")
                if closing is None:
                    return None, index
                index = closing
            else:
                return index, index
        index += 1
    return None, stop


def _cpp_delimiter_closings(text: str, opening: str, closing: str) -> dict[int, int]:
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for index, char in enumerate(text):
        if char == opening:
            stack.append(index)
            if len(stack) > _MAX_CPP_SCOPE_DELIMITER_PAIRS:
                raise ValueError("C++ scope delimiter depth exceeds the bounded limit")
        elif char == closing and stack:
            pairs[stack.pop()] = index
            if len(pairs) > _MAX_CPP_SCOPE_DELIMITER_PAIRS:
                raise ValueError("C++ scope delimiter count exceeds the bounded limit")
    return pairs


def mask_cpp_lambda_bodies(masked_text: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Blank nested lambda bodies in already literal/comment-masked C++ text.

    Returned offsets are half-open and refer to the input.  Newlines are
    preserved, so callers can remove lambda decision points without changing
    any surrounding source location.
    """

    chars = list(masked_text)
    square_closings = _cpp_delimiter_closings(masked_text, "[", "]")
    brace_closings = _cpp_delimiter_closings(masked_text, "{", "}")
    ranges: list[tuple[int, int]] = []
    intervals = [(0, len(masked_text))]
    while intervals:
        interval_start, interval_end = intervals.pop()
        index = interval_start
        while index < interval_end:
            if masked_text[index] != "[" or not _can_start_lambda_capture(masked_text, index):
                index += 1
                continue
            capture_end = square_closings.get(index)
            if capture_end is None or capture_end >= interval_end:
                index += 1
                continue
            body_open, _scanned_until = _lambda_body_open(masked_text, capture_end, interval_end)
            body_close = brace_closings.get(body_open) if body_open is not None else None
            if body_open is None or body_close is None or body_close >= interval_end:
                index += 1
                continue
            ranges.append((body_open, body_close + 1))
            if len(ranges) > _MAX_CPP_LAMBDA_BODIES:
                raise ValueError("C++ lambda body count exceeds the bounded limit")
            # The parent scan skips the recognized lambda. Nested lambdas are
            # visited exactly once in the disjoint capture/header/body ranges.
            if index + 1 < capture_end:
                intervals.append((index + 1, capture_end))
            if capture_end + 1 < body_open:
                intervals.append((capture_end + 1, body_open))
            if body_open + 1 < body_close:
                intervals.append((body_open + 1, body_close))
            index = body_close + 1

    # Nested lambda ranges overlap their parents. Blank only their union so a
    # deeply nested source cannot turn the masking pass quadratic.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    for start, end in merged:
        blank_cpp_region(chars, start, end)
    return "".join(chars), tuple(ranges)


def cpp_requires_expression_before_brace(prefix: str) -> bool:
    """Whether the next brace starts a trailing C++20 requires-expression.

    From a function-name diagnostic onward, a trailing requires-expression has
    two ``requires`` keywords: one introduces the requires-clause and the last
    introduces the expression, optionally with a parameter list.  A single
    ``requires (constraint)`` is only a parenthesized constraint followed by the
    real function body and must not be skipped.
    """

    matches = tuple(_REQUIRES_KEYWORD_RE.finditer(prefix))
    if len(matches) < 2:
        return False
    suffix = prefix[matches[-1].end() :].strip()
    if not suffix:
        return True
    if not suffix.startswith("("):
        return False
    depth = 0
    for index, char in enumerate(suffix):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
            if depth == 0:
                return not suffix[index + 1 :].strip()
    return False


def defines_main(path: Path) -> bool:
    """Whether a translation unit defines an entry point.

    Coverage scope uses this. A ``main()`` is not unit-testable by construction,
    and the generic g++ path has always kept entry points out of the test link,
    so they never reached gcov. Counting them on the adapter path would drop a
    project's coverage the moment it moved to CMake, for code that did not
    change.

    An unreadable file is reported as *not* an entry point, so it stays inside
    coverage scope. Dropping a file from measurement because it could not be
    read would be the silent kind of gap this gate exists to catch.
    """

    try:
        text = mask_cpp_literals(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return False
    return MAIN_DEFINITION_RE.search(text) is not None
