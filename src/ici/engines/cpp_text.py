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
        if text[index] in "\"'":
            end = cpp_quoted_end(text, index)
            blank_cpp_region(chars, index, end)
            index = end
            continue
        index += 1
    return "".join(chars)


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
