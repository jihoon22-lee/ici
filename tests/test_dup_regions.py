"""Duplicate-engine tests for source-scope and barrier-bounded token windows."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines.dup import DuplicateEngine


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_duplicate_engine(root: Path):
    return DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {
                "dup": {
                    "min_window": 6,
                    "warn_pct": 0.0,
                    "fail_pct": 100.0,
                    "mode": "pass_warn",
                }
            },
        },
    ).run()


def _clone_locations(result) -> set[tuple[str, int, int]]:
    return {
        (occurrence["file_path"], occurrence["start_line"], occurrence["end_line"])
        for group in result.extra["clone_groups"]
        for occurrence in group["occurrences"]
    }


def _target_locations(result) -> set[tuple[str, int, int | None]]:
    return {(target.file_path, target.start_line, target.end_line) for target in result.targets}


def _assert_clean(result, expected_files: set[str]) -> None:
    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] == 0
    assert _target_locations(result) == {(file_path, 1, None) for file_path in expected_files}
    assert all(target.status == EngineStatus.PASS for target in result.targets)


def test_cpp_windows_do_not_cross_function_boundaries(tmp_path: Path) -> None:
    source = """int first_value(int value) {
    int first = 1;
    int second = 2;
}
int next_value(int value) {
    int third = 3;
    int fourth = 4;
}
"""
    _write(tmp_path, "src/left.cpp", source)
    _write(
        tmp_path,
        "src/right.cpp",
        source.replace("first_value", "first_result").replace("next_value", "next_result"),
    )

    result = _run_duplicate_engine(tmp_path)

    _assert_clean(result, {"src/left.cpp", "src/right.cpp"})


def test_python_windows_do_not_cross_function_boundaries(tmp_path: Path) -> None:
    source = """def first_value(value):
    first = value + 1
    second = value + 2
    return second
def next_value(value):
    third = value + 3
    fourth = value + 4
    return fourth
"""
    _write(tmp_path, "src/left.py", source)
    _write(
        tmp_path,
        "src/right.py",
        source.replace("first_value", "first_result")
        .replace("next_value", "next_result")
        .replace("first", "uno")
        .replace("second", "dos")
        .replace("third", "tres")
        .replace("fourth", "cuatro"),
    )

    result = _run_duplicate_engine(tmp_path)

    _assert_clean(result, {"src/left.py", "src/right.py"})


def test_python_windows_do_not_cross_class_boundaries(tmp_path: Path) -> None:
    source = """class First:
    first = 1
    second = 2
    third = 3
class Second:
    fourth = 4
    fifth = 5
    sixth = 6
"""
    _write(tmp_path, "src/left.py", source)
    _write(
        tmp_path,
        "src/right.py",
        source.replace("First", "Alpha")
        .replace("Second", "Beta")
        .replace("first", "uno")
        .replace("second", "dos")
        .replace("third", "tres")
        .replace("fourth", "cuatro")
        .replace("fifth", "cinco")
        .replace("sixth", "seis"),
    )

    result = _run_duplicate_engine(tmp_path)

    _assert_clean(result, {"src/left.py", "src/right.py"})


def test_cpp_preprocessing_directive_is_a_window_barrier(tmp_path: Path) -> None:
    with_directive = """int compute_value(int value) {
    int first = value + 1;
    int second = value + 2;
#define EXTRA_STATEMENT 1
    int third = value + 3;
    int fourth = value + 4;
    return fourth;
}
"""
    without_directive = with_directive.replace("#define EXTRA_STATEMENT 1\n", "")
    _write(tmp_path, "src/with_directive.cpp", with_directive)
    _write(tmp_path, "src/without_directive.cpp", without_directive)

    result = _run_duplicate_engine(tmp_path)

    _assert_clean(result, {"src/with_directive.cpp", "src/without_directive.cpp"})


def test_python_import_is_a_window_barrier(tmp_path: Path) -> None:
    with_import = """def compute_value(value):
    first = value + 1
    second = value + 2
    import helper
    third = value + 3
    fourth = value + 4
    return fourth
"""
    without_import = with_import.replace("    import helper\n", "")
    _write(tmp_path, "src/with_import.py", with_import)
    _write(tmp_path, "src/without_import.py", without_import)

    result = _run_duplicate_engine(tmp_path)

    _assert_clean(result, {"src/with_import.py", "src/without_import.py"})


@pytest.mark.parametrize(
    ("extension", "left_source", "right_source", "end_line"),
    [
        (
            ".cpp",
            """int alpha(int value) {
    int total = value + 1;
    total += 2;
    total += 3;
    total += 4;
    total += 5;
    return total;
}
""",
            """int beta(int amount) {
    int result = amount + 9;
    result += 8;
    result += 7;
    result += 6;
    result += 5;
    return result;
}
""",
            8,
        ),
        (
            ".py",
            """def alpha(value):
    total = value + 1
    total = total + 2
    total = total + 3
    total = total + 4
    total = total + 5
    return total
""",
            """def beta(amount):
    result = amount + 9
    result = result + 8
    result = result + 7
    result = result + 6
    result = result + 5
    return result
""",
            7,
        ),
    ],
)
def test_true_six_line_type2_clones_within_functions_still_match(
    tmp_path: Path,
    extension: str,
    left_source: str,
    right_source: str,
    end_line: int,
) -> None:
    left = f"src/left{extension}"
    right = f"src/right{extension}"
    _write(tmp_path, left, left_source)
    _write(tmp_path, right, right_source)

    result = _run_duplicate_engine(tmp_path)

    expected = {(left, 1, end_line), (right, 1, end_line)}
    assert result.status == EngineStatus.WARN
    assert result.extra["clone_groups_count"] == 1
    assert _clone_locations(result) == expected
    assert _target_locations(result) == expected
    assert all(target.status == EngineStatus.WARN for target in result.targets)


def test_one_inserted_statement_soft_gap_still_reports_common_region(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/a.py",
        """def calc_a(values):
    result = {}
    total = 0
    for v in values:
        total += v.amount
        if v.flag:
            total += v.bonus
    return total
""",
    )
    _write(
        tmp_path,
        "src/b.py",
        """def calc_b(values):
    result = {}
    total = 0
    for v in values:
        total += v.amount
        if v.flag:
            total += v.bonus
        note = v.memo.strip()
    return total
""",
    )

    result = _run_duplicate_engine(tmp_path)

    expected = {("src/a.py", 1, 7), ("src/b.py", 1, 7)}
    assert result.status == EngineStatus.WARN
    assert result.extra["clone_groups_count"] == 1
    assert _clone_locations(result) == expected
    assert _target_locations(result) == expected
    assert all(target.status == EngineStatus.WARN for target in result.targets)
