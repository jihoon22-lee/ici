"""Signal-quality contracts for the duplicate-code engine.

Large constant tables contain many repeated lexical shapes but are data, not
copy-pasted executable logic.  Control-flow functions remain actionable Type-2
clones even when their local names and literal values change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines.dup import DuplicateEngine


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_duplicate_engine(root: Path) -> object:
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


def _python_constant_table(name: str, prefix: str, start: int) -> str:
    rows = "".join(f'    ("{prefix}-label-{index}", {start + index}),\n' for index in range(10))
    return f"{name} = [\n{rows}]\n"


def test_python_string_number_tables_with_different_values_are_not_clones(tmp_path: Path):
    _write(tmp_path, "src/left.py", _python_constant_table("LEFT_TABLE", "left", 100))
    _write(tmp_path, "src/right.py", _python_constant_table("RIGHT_TABLE", "right", 900))

    result = _run_duplicate_engine(tmp_path)

    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] == 0
    assert result.extra["clone_groups"] == []


def _cpp_array_table(name: str, prefix: str, start: int) -> str:
    rows = "".join(f'    {{"{prefix}-label-{index}", {start + index}}},\n' for index in range(10))
    return f"static const Entry {name}[] = {{\n{rows}}};\n"


def _cpp_enum_table(name: str, prefix: str, start: int) -> str:
    rows = "".join(f"    {prefix}Entry{index} = {start + index},\n" for index in range(10))
    return f"enum class {name} {{\n{rows}}};\n"


@pytest.mark.parametrize(
    "builder",
    [_cpp_array_table, _cpp_enum_table],
    ids=["array", "enum"],
)
def test_cpp_data_tables_with_different_values_are_not_clones(tmp_path: Path, builder):
    _write(tmp_path, "src/left.cpp", builder("LeftTable", "left", 100))
    _write(tmp_path, "src/right.cpp", builder("RightTable", "right", 900))

    result = _run_duplicate_engine(tmp_path)

    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] == 0
    assert result.extra["clone_groups"] == []


@pytest.mark.parametrize(
    "builder",
    [_cpp_array_table, _cpp_enum_table],
    ids=["array", "enum"],
)
def test_repeated_low_information_cpp_table_rows_do_not_create_clone_groups(
    tmp_path: Path, builder
):
    def repeated_table(name: str, prefix: str) -> str:
        if builder is _cpp_array_table:
            rows = "".join(f'    {{"{prefix}", 0}},\n' for _ in range(24))
            return f"static const Entry {name}[] = {{\n{rows}}};\n"
        rows = "".join(f"    {prefix}Entry{index} = 0,\n" for index in range(24))
        return f"enum class {name} {{\n{rows}}};\n"

    _write(tmp_path, "src/left.cpp", repeated_table("LeftTable", "left"))
    _write(tmp_path, "src/right.cpp", repeated_table("RightTable", "right"))

    result = _run_duplicate_engine(tmp_path)

    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] == 0


@pytest.mark.parametrize("extension", ["py", "cpp"], ids=["python", "cpp"])
def test_literal_only_assignment_blocks_remain_low_information(tmp_path: Path, extension: str):
    terminator = "" if extension == "py" else ";"
    left = "".join(f"left_{index} = {index}{terminator}\n" for index in range(8))
    right = "".join(f"right_{index} = {index + 100}{terminator}\n" for index in range(8))
    _write(tmp_path, f"src/left.{extension}", left)
    _write(tmp_path, f"src/right.{extension}", right)

    result = _run_duplicate_engine(tmp_path)

    assert result.status == EngineStatus.PASS
    assert result.extra["clone_groups_count"] == 0


@pytest.mark.parametrize("extension", ["py", "cpp"], ids=["python", "cpp"])
def test_identifier_flow_assignment_blocks_remain_actionable(tmp_path: Path, extension: str):
    terminator = "" if extension == "py" else ";"
    left = "".join(f"target_{index} = source_{index}{terminator}\n" for index in range(8))
    right = "".join(f"output_{index} = input_{index}{terminator}\n" for index in range(8))
    _write(tmp_path, f"src/left.{extension}", left)
    _write(tmp_path, f"src/right.{extension}", right)

    result = _run_duplicate_engine(tmp_path)

    assert result.status == EngineStatus.WARN
    assert result.extra["clone_groups_count"] == 1


@pytest.mark.parametrize(
    ("extension", "left_source", "right_source", "language"),
    [
        (
            "py",
            """def classify(value):
    if value > 0:
        total = value + 1
        total += 2
        total += 3
        return total
    return value
""",
            """def decide(amount):
    if amount > 0:
        result = amount + 9
        result += 8
        result += 7
        return result
    return amount
""",
            "python",
        ),
        (
            "cpp",
            """int classify(int value) {
    if (value > 0) {
        int total = value + 1;
        total += 2;
        total += 3;
        return total;
    }
    return value;
}
""",
            """int decide(int amount) {
    if (amount > 0) {
        int result = amount + 9;
        result += 8;
        result += 7;
        return result;
    }
    return amount;
}
""",
            "cpp",
        ),
    ],
    ids=["python-control-flow", "cpp-control-flow"],
)
def test_real_control_flow_functions_remain_detectable(
    tmp_path: Path,
    extension: str,
    left_source: str,
    right_source: str,
    language: str,
):
    _write(tmp_path, f"src/left.{extension}", left_source)
    _write(tmp_path, f"src/right.{extension}", right_source)

    result = _run_duplicate_engine(tmp_path)

    assert result.status == EngineStatus.WARN
    assert result.extra["clone_groups_count"] == 1
    group = result.extra["clone_groups"][0]
    assert group["language"] == language
    assert group["lines_count"] >= 6
