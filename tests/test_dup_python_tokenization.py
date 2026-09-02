"""Focused contract tests for the Python duplicate-tokenization helper.

The duplicate engine consumes one canonical token string per physical source
line.  These tests keep the lexical contract independent from Python's parser
while covering the source-shape information needed for useful Type-2 matches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines._python_dup_tokenization import tokenize_python_lines
from ici.engines.dup import DuplicateEngine

_TOKEN_SEPARATOR = "\x1f"


def _records(source: str) -> tuple[tuple[int, str], ...]:
    """Return the helper result while checking its public tuple contract."""

    result = tokenize_python_lines(source)
    assert isinstance(result, tuple)
    assert all(isinstance(line, int) and isinstance(tokens, str) for line, tokens in result)
    assert [line for line, _ in result] == sorted(line for line, _ in result)
    return result


def _line_tokens(source: str) -> dict[int, tuple[str, ...]]:
    return {line: tuple(tokens.split(_TOKEN_SEPARATOR)) for line, tokens in _records(source)}


def _one_line(source: str) -> tuple[str, ...]:
    records = _records(source)
    assert len(records) == 1, (source, records)
    return tuple(records[0][1].split(_TOKEN_SEPARATOR))


def test_comments_are_removed_without_consuming_following_code_or_string_markers():
    source = (
        "# a whole-line comment\n"
        "value = 1  # the rest of this line is a comment\n"
        'text = "# this marker is string data, not a comment"\n'
    )

    lines = _line_tokens(source)

    assert set(lines) == {2, 3}
    assert lines[2] == ("ID", "=", "INT_LIT")
    assert lines[3] == ("ID", "=", "STRING_LIT")


def test_multiline_string_is_attached_to_its_start_line_and_preserves_following_code():
    source = 'payload = """first line\n# still string payload\nlast line""" + suffix\nafter = 2\n'

    lines = _line_tokens(source)

    assert lines[1] == ("ID", "=", "STRING_LIT")
    assert 2 not in lines
    assert lines[3] == ("+", "ID")
    assert lines[4] == ("ID", "=", "INT_LIT")


def test_identifier_names_and_literal_values_are_type_2_normalized():
    left = "result = first_value + 10\n"
    right = "answer = second_value + 99\n"

    assert _records(left) == _records(right)


def test_called_apis_and_attribute_names_remain_semantic_anchors():
    json_call = _one_line("import json\nresult = json.dumps(value)\n")
    yaml_call = _one_line("import yaml\nanswer = yaml.safe_dump(other)\n")

    assert json_call != yaml_call
    assert "NAME(json)" in json_call
    assert "NAME(dumps)" in json_call


def test_local_identifiers_still_normalize_around_the_same_called_api():
    left = _one_line("from tools import formatter\nresult = formatter(first, second)\n")
    right = _one_line("from tools import formatter\nanswer = formatter(alpha, beta)\n")

    assert left == right
    assert "NAME(formatter)" in left


@pytest.mark.parametrize("builtin", ["aiter", "anext", "breakpoint"])
def test_python_310_builtin_calls_remain_semantic_anchors(builtin: str):
    tokens = _one_line(f"result = {builtin}(value)\n")

    assert f"NAME({builtin})" in tokens


def test_literal_categories_are_distinct_but_values_within_a_category_match():
    integer = _one_line("value = 42\n")
    other_integer = _one_line("renamed = 0x2a\n")
    floating = _one_line("value = 1.25e2\n")
    other_floating = _one_line("renamed = 6.02e23\n")
    complex_number = _one_line("value = 1j\n")
    other_complex = _one_line("renamed = 2.5j\n")
    string = _one_line('value = "text"\n')
    other_string = _one_line("renamed = 'different text'\n")
    bytes_value = _one_line("value = b'bytes'\n")
    other_bytes = _one_line('renamed = b"other bytes"\n')
    f_string = _one_line('value = f"{name}: {number}"\n')
    other_f_string = _one_line('renamed = f"{other}: {count}"\n')

    assert integer == other_integer
    assert floating == other_floating
    assert complex_number == other_complex
    assert string == other_string
    assert bytes_value == other_bytes
    assert f_string == other_f_string
    assert f_string == ("ID", "=", "FSTRING_LIT")
    assert (
        len(
            {
                integer,
                floating,
                complex_number,
                string,
                bytes_value,
                f_string,
            }
        )
        == 6
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("value + +other\n", "value += other\n"),
        ("value * *other\n", "value ** other\n"),
        ("value / /other\n", "value // other\n"),
        ("value < = other\n", "value <= other\n"),
        ("value := other\n", "value = other\n"),
    ],
)
def test_adjacent_operator_boundaries_remain_distinct(left: str, right: str):
    assert _one_line(left) != _one_line(right)


def test_match_and_case_are_soft_keywords_only_in_structural_positions():
    structural_left = "match subject:\n    case 1:\n        result = subject\n"
    structural_right = "match candidate:\n        case 99:\n                answer = candidate\n"

    left_lines = _line_tokens(structural_left)
    right_lines = _line_tokens(structural_right)
    assert left_lines == right_lines
    assert left_lines[1][:2] == ("match", "ID")
    assert left_lines[2][1] == "case"

    ordinary_left = "match = value\ncase = value\nresult = match + case\n"
    ordinary_right = "candidate = value\nbranch = value\nresult = candidate + branch\n"
    assert _records(ordinary_left) == _records(ordinary_right)


def test_indent_and_dedent_tokens_ignore_column_width_but_preserve_block_shape():
    same_shape_left = (
        "if ready:\n    result = input_value + 1\n    return result\nreturn input_value\n"
    )
    same_shape_right = (
        "if condition:\n"
        "        answer = source_value + 99\n"
        "        return answer\n"
        "return source_value\n"
    )
    different_shape = (
        "if condition:\n        answer = source_value + 99\nreturn answer\nreturn source_value\n"
    )

    lines = _line_tokens(same_shape_left)
    assert "INDENT" in lines[2]
    assert "DEDENT" in lines[4]
    assert _records(same_shape_left) == _records(same_shape_right)
    assert _records(same_shape_left) != _records(different_shape)


def test_multiline_imports_are_excluded_as_one_logical_statement():
    source = "from package import (\n    first,\n    second,\n)\nvalue = first + second\n"

    lines = _line_tokens(source)

    assert set(lines) == {5}
    assert lines[5] == ("ID", "=", "ID", "+", "ID")


def test_crlf_and_lf_have_identical_tokens_and_physical_line_numbers():
    lf = "value = 1\n# ignored\nvalue = 2\n"
    crlf = lf.replace("\n", "\r\n")
    cr = lf.replace("\n", "\r")

    assert _records(lf) == _records(crlf)
    assert _records(lf) == _records(cr)
    assert set(_line_tokens(lf)) == {1, 3}


@pytest.mark.parametrize(
    ("source", "marker"),
    [
        ('value = "unterminated\nnext = 1\n', "UNTERMINATED_STRING_LIT"),
        ("values = [1,\n", "UNTERMINATED_STATEMENT"),
        ("if ready:\n    value = 1\n  broken = 2\n", "INVALID_INDENTATION"),
    ],
)
def test_malformed_input_is_deterministic_bounded_and_marked(source: str, marker: str):
    first = _records(source)
    second = _records(source)
    line_count = source.count("\n") + 1

    assert first == second
    assert first
    assert all(1 <= line <= line_count for line, _ in first)
    assert marker in " ".join(tokens for _, tokens in first)


def test_tokenizer_rejects_non_text_and_null_input():
    with pytest.raises(TypeError, match="text"):
        tokenize_python_lines(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="null"):
        tokenize_python_lines("value = 1\x00\n")


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_duplicate_engine(root: Path, min_window: int):
    return DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {
                "dup": {
                    "min_window": min_window,
                    "warn_pct": 0.0,
                    "mode": "pass_warn",
                }
            },
        },
    ).run()


def test_duplicate_engine_detects_python_type_2_clone(tmp_path: Path):
    _write(
        tmp_path,
        "src/left.py",
        """def alpha(value):
    if value:
        total = value + 1
        total = total + 2
        total = total + 3
        return total
    return value
""",
    )
    _write(
        tmp_path,
        "src/right.py",
        """def beta(amount):
        if amount:
                result = amount + 9
                result = result + 8
                result = result + 7
                return result
        return amount
""",
    )

    result = _run_duplicate_engine(tmp_path, min_window=6)

    assert result.status == EngineStatus.WARN
    assert result.extra["clone_groups_count"] == 1
    assert result.extra["clone_groups"][0]["language"] == "python"


def test_duplicate_engine_rejects_python_operator_and_block_shape_changes(tmp_path: Path):
    _write(
        tmp_path,
        "src/left.py",
        """if ready:
    result = input_value + 1
    return result
return input_value
""",
    )
    _write(
        tmp_path,
        "src/right.py",
        """if condition:
    answer = source_value - 99
    return answer
return source_value
""",
    )

    result = _run_duplicate_engine(tmp_path, min_window=3)

    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] == 0
    assert {target.file_path for target in result.targets} == {
        "src/left.py",
        "src/right.py",
    }
