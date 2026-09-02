"""Focused contract tests for the C++ duplicate-tokenization helper.

These tests intentionally exercise the lexical boundary that the duplicate
engine consumes, rather than requiring a compiler or a Qt installation.  The
helper returns one canonical token string per physical source line; the small
adapters below also tolerate empty records so the tests stay about token
semantics and source locations.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from ici.engines._cpp_dup_tokenization import tokenize_cpp_lines


def _records(source: str) -> tuple[tuple[int, str], ...]:
    """Return the helper result as the public tuple contract promises."""

    result = tokenize_cpp_lines(source)
    assert isinstance(result, tuple)
    assert all(isinstance(line_no, int) and isinstance(tokens, str) for line_no, tokens in result)
    return result


def _nonempty_tokens(source: str) -> tuple[str, ...]:
    return tuple(tokens for _, tokens in _records(source) if tokens)


def _line_tokens(source: str) -> dict[int, str]:
    """Group tokens by physical line without imposing an empty-line policy."""

    grouped: defaultdict[int, list[str]] = defaultdict(list)
    for line_no, tokens in _records(source):
        if tokens:
            grouped[line_no].append(tokens)
    return {line_no: "\x1f".join(tokens) for line_no, tokens in grouped.items()}


def _one_line(source: str) -> str:
    tokens = _nonempty_tokens(source)
    assert len(tokens) == 1, (source, tokens)
    return tokens[0]


def test_type_2_identifier_renames_and_literal_values_still_match():
    left = "int add_left(int first, int second) { return first + second + 7; }\n"
    right = "int add_right(int alpha, int beta) { return alpha + beta + 99; }\n"

    assert _nonempty_tokens(left) == _nonempty_tokens(right)


def test_numeric_and_string_literals_are_not_the_same_class():
    numeric = _one_line("auto value = 42;\n")
    string = _one_line('auto value = "42";\n')

    assert numeric != string


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("left & &right;\n", "left && right;\n"),
        ("left + +right;\n", "left++ + right;\n"),
        ("left < = right;\n", "left <= right;\n"),
        ("left > > right;\n", "left >> right;\n"),
    ],
)
def test_maximal_munch_keeps_adjacent_operator_tokens_distinct(left: str, right: str):
    assert _one_line(left) != _one_line(right)


def test_inline_comments_are_removed_without_losing_following_code():
    tokens = _one_line("int value = 1; /* discard this */ value += 2; // and this tail\n")

    assert "discard" not in tokens
    assert "tail" not in tokens
    assert "int" in tokens
    assert "+=" in tokens


def test_comments_between_a_callee_and_parenthesis_do_not_hide_the_api_anchor():
    tokens = _one_line("result = renderValue /* retained call identity */ (input);\n")

    assert "NAME(renderValue)" in tokens


def test_multiline_block_comments_keep_line_mapping_and_code_after_close():
    source = "int left = 1;\n/* hidden comment\n * still hidden */\nint right = left + 2;\n"

    lines = _line_tokens(source)

    assert 1 in lines
    assert 4 in lines
    assert "int" in lines[4]
    assert "hidden" not in " ".join(lines.values())


def test_comment_markers_inside_ordinary_and_raw_strings_are_literal_payload():
    source = (
        'const char *ordinary = "// not a comment /* still text */";\n'
        'const char *raw = R"TAG(// not a comment /* still raw */)TAG";\n'
    )

    lines = _line_tokens(source)
    assert set(lines) == {1, 2}
    flattened = " ".join(lines.values())
    assert "not" not in flattened
    assert "comment" not in flattened
    assert "/*" not in flattened
    assert "//" not in flattened


def test_multiline_raw_string_uses_its_delimiter_and_preserves_following_line():
    source = (
        'const char *payload = R"TAG(\n'
        "// raw line; not a comment\n"
        ')NOT_THE_END"\n'
        "/* raw block marker */\n"
        ')TAG";\n'
        "int after = 1;\n"
    )

    lines = _line_tokens(source)
    assert 6 in lines
    assert "int" in lines[6]
    flattened = " ".join(lines.values())
    assert "NOT_THE_END" not in flattened
    assert "raw line" not in flattened
    assert "raw block" not in flattened


def test_literal_classes_include_integer_float_char_string_and_raw_string():
    samples = (
        "auto value = 42;\n",
        "auto value = 42.0;\n",
        "auto value = 'x';\n",
        'auto value = "x";\n',
        'auto value = R"TAG(x)TAG";\n',
    )

    normalized = {_one_line(sample) for sample in samples}
    assert len(normalized) == len(samples)


def test_function_like_preprocessor_continuations_are_excluded_as_one_directive():
    source = (
        "#define DECLARE_VALUE(name) \\\n    int name = 1; \\\n    name += 1;\nint retained = 2;\n"
    )

    lines = _line_tokens(source)
    assert set(lines) == {4}
    assert "int" in lines[4]
    assert "DECLARE_VALUE" not in " ".join(lines.values())


def test_qt_meta_object_tokens_are_not_collapsed_to_generic_identifiers():
    q_object = _one_line("Q_OBJECT\n")
    q_gadget = _one_line("Q_GADGET\n")
    signals = _one_line("signals:\n")
    slots = _one_line("slots:\n")

    assert q_object != q_gadget
    assert signals != slots
    assert "Q_OBJECT" in q_object
    assert "Q_GADGET" in q_gadget
    assert "signals" in signals
    assert "slots" in slots


def test_qt_keyword_style_iteration_macros_remain_semantic_tokens():
    foreach_tokens = _one_line("foreach (const auto &item, values) { consume(item); }\n")
    forever_tokens = _one_line("forever { processEvents(); }\n")

    assert "foreach" in foreach_tokens
    assert "forever" in forever_tokens


def test_qualified_names_and_called_apis_remain_semantic_anchors():
    issue_switch = _one_line("case diskmap::NodeIssue::DepthLimitReached: renderIssue();\n")
    kind_switch = _one_line("case diskmap::FsKind::RegularFile: renderKind();\n")

    assert issue_switch != kind_switch
    assert "NAME(NodeIssue)" in issue_switch
    assert "NAME(renderIssue)" in issue_switch


def test_local_identifiers_still_support_type_2_renaming_around_anchored_calls():
    left = _one_line("result = formatter(first, second);\n")
    right = _one_line("answer = formatter(alpha, beta);\n")

    assert left == right
    assert "NAME(formatter)" in left


@pytest.mark.parametrize(
    ("source", "keywords"),
    [
        (
            "consteval int make_value() noexcept { co_return 1; }\n",
            ("consteval", "noexcept", "co_return"),
        ),
        (
            "template <typename T> concept Numeric = requires(T value) { value + 1; };\n",
            ("template", "typename", "concept", "requires"),
        ),
    ],
)
def test_modern_cpp_keywords_remain_exact_tokens(source: str, keywords: tuple[str, ...]):
    tokens = _one_line(source)
    assert all(keyword in tokens for keyword in keywords)


def test_crlf_and_lf_have_identical_tokens_and_stable_physical_lines():
    lf = "int first = 1;\n/* removed */\nint second = 2;\n"
    crlf = lf.replace("\n", "\r\n")
    cr = lf.replace("\n", "\r")

    assert _records(lf) == _records(crlf)
    assert _records(lf) == _records(cr)
    assert _records(lf) == _records(lf)
    assert set(_line_tokens(lf)) == {1, 3}


def test_escaped_crlf_inside_an_ordinary_literal_preserves_the_next_source_line():
    lf = 'const char *text = "first\\\nsecond";\nint after = 1;\n'
    crlf = lf.replace("\n", "\r\n")

    assert _records(crlf) == _records(lf)
    lines = _line_tokens(crlf)
    assert set(lines) == {1, 2, 3}
    assert "STRING_LIT" in lines[1]
    assert "int" in lines[3]


@pytest.mark.parametrize(
    ("source", "opaque_kind"),
    [
        (
            "int value = 1; /* unterminated comment\nint after = 2;\n",
            "UNTERMINATED_BLOCK_COMMENT",
        ),
        (
            'const char *value = "unterminated string\nint after = 2;\n',
            "UNTERMINATED_STRING_LIT",
        ),
    ],
)
def test_unterminated_comments_and_strings_are_total_and_bounded(source: str, opaque_kind: str):
    """Malformed input must not crash or create records past the source."""

    records = _records(source)
    line_count = source.count("\n") + 1

    assert len(records) <= line_count
    assert all(1 <= line_no <= line_count for line_no, _ in records)
    assert opaque_kind in " ".join(tokens for _, tokens in records)
