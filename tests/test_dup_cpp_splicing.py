"""C++ translation-phase line-splicing contracts for duplicate tokenization."""

from __future__ import annotations

import pytest

from ici.engines._cpp_dup_tokenization import tokenize_cpp_lines

_TOKEN_SEPARATOR = "\x1f"
_LINE_ENDINGS = ("\n", "\r\n", "\r")


def _line_tokens(source: str) -> dict[int, tuple[str, ...]]:
    result = tokenize_cpp_lines(source)
    assert isinstance(result, tuple)
    assert all(isinstance(line, int) and isinstance(tokens, str) for line, tokens in result)
    return {line: tuple(tokens.split(_TOKEN_SEPARATOR)) for line, tokens in result}


def _expression_splice_source(line_ending: str) -> str:
    return (
        "int result = first + "
        + "\\"
        + line_ending
        + "    second;"
        + line_ending
        + "int after = result + 1;"
        + line_ending
    )


@pytest.mark.parametrize("line_ending", _LINE_ENDINGS, ids=["lf", "crlf", "cr"])
def test_expression_splice_is_line_ending_invariant_and_preserves_physical_lines(
    line_ending: str,
):
    lines = _line_tokens(_expression_splice_source(line_ending))

    assert set(lines) == {1, 2, 3}
    assert lines[1] == ("int", "ID", "=", "ID", "+")
    assert lines[2] == ("ID", ";")
    assert lines[3] == ("int", "ID", "=", "ID", "+", "INT_LIT", ";")
    assert "\\" not in " ".join(" ".join(tokens) for tokens in lines.values())


@pytest.mark.parametrize("line_ending", _LINE_ENDINGS, ids=["lf", "crlf", "cr"])
def test_line_comment_splice_hides_the_next_physical_line(line_ending: str):
    source = (
        "int visible = 1; // hidden comment "
        + "\\"
        + line_ending
        + "int hidden = 2;"
        + line_ending
        + "int after = visible + 1;"
        + line_ending
    )

    lines = _line_tokens(source)

    assert set(lines) == {1, 3}
    assert lines[1] == ("int", "ID", "=", "INT_LIT", ";")
    assert lines[3] == ("int", "ID", "=", "ID", "+", "INT_LIT", ";")
    assert all("hidden" not in tokens for tokens in lines.values())


def _ordinary_string_splice_source(backslashes: int, line_ending: str) -> str:
    slash_run = "\\" * backslashes
    prefix = 'const char *value = "left' + slash_run + line_ending
    if backslashes % 2:
        return prefix + '";' + line_ending + "int after = 1;" + line_ending
    # The first quote on line 2 is escaped by the one remaining backslash.  A
    # second splice keeps the literal open until line 3, where it is closed.
    return prefix + '";\\' + line_ending + '";' + line_ending + "int after = 1;" + line_ending


@pytest.mark.parametrize("line_ending", _LINE_ENDINGS, ids=["lf", "crlf", "cr"])
def test_ordinary_string_splice_follows_one_removed_backslash_translation_phase(
    line_ending: str,
):
    one = _line_tokens(_ordinary_string_splice_source(1, line_ending))
    two = _line_tokens(_ordinary_string_splice_source(2, line_ending))
    three = _line_tokens(_ordinary_string_splice_source(3, line_ending))

    expected_prefix = ("const", "char", "*", "ID", "=", "STRING_LIT")
    expected_after = ("int", "ID", "=", "INT_LIT", ";")
    assert one[1] == expected_prefix
    assert three[1] == expected_prefix
    assert one[2] == (";",)
    assert three[2] == (";",)
    assert one[3] == expected_after
    assert three[3] == expected_after
    assert one == three

    # With two backslashes, phase 2 removes only the final one.  The remaining
    # backslash escapes the quote on line 2, so that line's semicolon stays in
    # the string and no standalone line-2 token is emitted.  The second splice
    # on that line moves the closing quote and statement semicolon to line 3.
    assert two[1] == expected_prefix
    assert 2 not in two
    assert two[3] == (";",)
    assert two[4] == expected_after


@pytest.mark.parametrize("line_ending", _LINE_ENDINGS, ids=["lf", "crlf", "cr"])
def test_directive_backslash_with_trailing_whitespace_does_not_continue(
    line_ending: str,
):
    source = (
        "#define VALUE 1 "
        + "\\"
        + "   "
        + line_ending
        + "int retained = 2;"
        + line_ending
        + "int after = retained + 1;"
        + line_ending
    )

    lines = _line_tokens(source)

    assert set(lines) == {2, 3}
    assert lines[2] == ("int", "ID", "=", "INT_LIT", ";")
    assert lines[3] == ("int", "ID", "=", "ID", "+", "INT_LIT", ";")
