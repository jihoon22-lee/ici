"""C++ duplicate-region barriers must follow preprocessing line semantics."""

from __future__ import annotations

from ici.engines._dup_regions import cpp_duplicate_regions


def _regions(source: str):
    return cpp_duplicate_regions(source, range(1, len(source.splitlines()) + 1))


def test_comment_prefixed_directive_starts_a_new_region_segment() -> None:
    source = "int before = 0;\n/*c*/ #define VALUE 1\nint after = 1;\n"

    assert _regions(source) == ((0, 0), (0, 1), (0, 1))


def test_true_backslash_newline_continuation_barriers_every_physical_line() -> None:
    source = (
        "int before = 0;\n#define VALUE one \\\ncontinued_one \\\ncontinued_two\nint after = 1;\n"
    )

    assert _regions(source) == (
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (0, 3),
    )


def test_backslash_followed_by_trailing_spaces_is_not_a_continuation() -> None:
    source = "int before = 0;\n#define VALUE one \\   \ncontinued_one\nint after = 1;\n"

    assert _regions(source) == (
        (0, 0),
        (0, 1),
        (0, 1),
        (0, 1),
    )


def test_hashes_inside_strings_and_comments_are_not_directive_barriers() -> None:
    source = (
        "int before = 0;\n"
        'const char *text = "# not a directive";\n'
        "// # also not a directive\n"
        "/* # also not a directive */\n"
        "int after = 1;\n"
    )

    assert _regions(source) == ((0, 0),) * 5


def test_spliced_literal_and_line_comment_hashes_are_not_directives() -> None:
    source = (
        'const char *text = "left\\\n'
        '# still string";\n'
        "// continued comment \\\n"
        "# still comment\n"
        "int after = 1;\n"
    )

    assert _regions(source) == ((0, 0),) * 5
