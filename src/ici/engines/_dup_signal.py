"""Semantic-signal policy for normalized duplicate-code regions."""

from __future__ import annotations

_TOKEN_SEPARATOR = "\x1f"
_LOW_INFORMATION_TOKENS = frozenset(
    {
        "ID",
        "INT_LIT",
        "FLOAT_LIT",
        "COMPLEX_LIT",
        "CHAR_LIT",
        "STRING_LIT",
        "RAW_STRING_LIT",
        "BYTES_LIT",
        "RAW_BYTES_LIT",
        "FSTRING_LIT",
        "RAW_FSTRING_LIT",
        "False",
        "None",
        "True",
        "false",
        "nullptr",
        "true",
        "INDENT",
        "DEDENT",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        ",",
        ";",
        ":",
        ".",
        "::",
        "=",
        "@",
        "*",
        "&",
        "&&",
    }
)
_LITERAL_PREFIXES = (
    "INT_LIT:",
    "FLOAT_LIT:",
    "CHAR_LIT:",
    "STRING_LIT:",
    "RAW_STRING_LIT:",
)
_LITERAL_TOKENS = frozenset(
    {
        "INT_LIT",
        "FLOAT_LIT",
        "COMPLEX_LIT",
        "CHAR_LIT",
        "STRING_LIT",
        "RAW_STRING_LIT",
        "BYTES_LIT",
        "RAW_BYTES_LIT",
        "FSTRING_LIT",
        "RAW_FSTRING_LIT",
        "False",
        "None",
        "True",
        "false",
        "nullptr",
        "true",
    }
)


def _is_literal_token(value: str) -> bool:
    return value in _LITERAL_TOKENS or value.startswith(_LITERAL_PREFIXES)


def _line_has_semantic_signal(normalized: str) -> bool:
    tokens = normalized.split(_TOKEN_SEPARATOR)
    if sum(item == "ID" for item in tokens) >= 2 and not any(
        _is_literal_token(item) for item in tokens
    ):
        return True
    return any(
        item not in _LOW_INFORMATION_TOKENS
        and not item.startswith(_LITERAL_PREFIXES)
        and not item.startswith(("ERROR_TOKEN(", "TOKEN_TYPE(", "UNTERMINATED_"))
        for item in tokens
    )


def duplicate_signal_prefix(indexed: list[tuple[int, str]]) -> tuple[int, ...]:
    """Return prefix sums for semantically informative normalized lines."""

    prefix = [0]
    for _line, normalized in indexed:
        prefix.append(prefix[-1] + int(_line_has_semantic_signal(normalized)))
    return tuple(prefix)


def has_duplicate_signal(prefix: tuple[int, ...], start: int, size: int) -> bool:
    """Require enough code signal to reject literal/data-table clone shapes."""

    if size <= 0 or start < 0 or start + size >= len(prefix):
        return False
    signal_lines = prefix[start + size] - prefix[start]
    minimum = max(1, (size + 3) // 4)
    if size >= 6:
        minimum = max(2, minimum)
    return signal_lines >= minimum
