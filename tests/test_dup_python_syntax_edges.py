"""Python syntax edge contracts used by duplicate analysis."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines._python_dup_tokenization import tokenize_python_lines
from ici.engines.dup import DuplicateEngine

_TOKEN_SEPARATOR = "\x1f"


def _line_tokens(source: str) -> dict[int, tuple[str, ...]]:
    return {
        line: tuple(tokens.split(_TOKEN_SEPARATOR))
        for line, tokens in tokenize_python_lines(source)
    }


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_duplicate_engine(root: Path):
    return DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {"dup": {"min_window": 6}},
        },
    ).run()


def test_multiline_case_preserves_case_on_the_actual_case_line() -> None:
    source = """match value:
    case (
        1
    ):
        result = value
"""

    lines = _line_tokens(source)

    assert lines[2] == ("INDENT", "case", "(")


def test_soft_keyword_positions_do_not_reclassify_same_spelled_identifiers() -> None:
    source = """match match:
    case case if case > 0:
        result = case
"""

    lines = _line_tokens(source)

    assert lines[1] == ("match", "ID", ":")
    assert lines[2] == ("INDENT", "case", "ID", "if", "ID", ">", "INT_LIT", ":")
    assert lines[3] == ("INDENT", "ID", "=", "ID")


@pytest.mark.parametrize(
    "source",
    [
        "import x; value = 1\n",
        "from x import y; value = 1\n",
    ],
    ids=["import", "from-import"],
)
def test_inline_import_excludes_only_import_prefix_and_keeps_following_code(
    source: str,
) -> None:
    lines = _line_tokens(source)

    assert 1 in lines
    assert lines[1][-3:] == ("ID", "=", "INT_LIT")
    assert set(lines[1]).issubset({";", "ID", "=", "INT_LIT"})
    assert "import" not in lines[1]
    assert "from" not in lines[1]


def test_multiline_imports_keep_the_existing_exclusion_contract() -> None:
    source = """from package import (
    first,
    second,
)
value = first + second
"""

    lines = _line_tokens(source)

    assert lines == {5: ("ID", "=", "ID", "+", "ID")}


def test_imported_api_anchor_survives_parenthesized_multiline_qualification() -> None:
    source = """import package
result = (
    package
    .service
    .render(value)
)
"""

    flattened = tuple(token for tokens in _line_tokens(source).values() for token in tokens)

    assert "NAME(package)" in flattened
    assert "NAME(service)" in flattened
    assert "NAME(render)" in flattened


@pytest.mark.parametrize(
    "source",
    [
        """def broken(value):
    if value
        return value
""",
        """values = [
    1,
""",
    ],
    ids=["syntax-error", "unterminated-statement"],
)
def test_malformed_python_fails_closed_deterministically(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "src/broken.py", source)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    assert first.status == EngineStatus.ERROR
    assert first.evidence == EvidenceState.NOT_RUN
    assert second.status == EngineStatus.ERROR
    assert second.evidence == EvidenceState.NOT_RUN

    first_targets = [
        (
            target.file_path,
            target.start_line,
            target.end_line,
            target.target_name,
            target.status,
            target.message,
        )
        for target in first.targets
    ]
    second_targets = [
        (
            target.file_path,
            target.start_line,
            target.end_line,
            target.target_name,
            target.status,
            target.message,
        )
        for target in second.targets
    ]
    assert first_targets == second_targets
    assert len(first.targets) == 1
    target = first.targets[0]
    assert target.file_path == "src/broken.py"
    assert target.target_name == "SourceTokenizationError"
    assert target.status == EngineStatus.ERROR
