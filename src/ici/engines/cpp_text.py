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

CPP_RAW_START_RE = re.compile(r'(?:u8|u|U|L)?R"(?P<delimiter>[^\s()\\]{0,16})\(')


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
