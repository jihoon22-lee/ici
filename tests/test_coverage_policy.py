"""TDD contract tests for changed-line and coverage threshold policy helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines.coverage_policy import evaluate_coverage_policy, parse_changed_lines


def _project(tmp_path: Path) -> Path:
    (tmp_path / "project").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "project" / "relative.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "src" / "a.cpp").write_text("int a;\n", encoding="utf-8")
    (tmp_path / "src" / "z.py").write_text("value = 1\n", encoding="utf-8")
    return tmp_path


def _coverage_files() -> list[dict]:
    return [
        {
            "file": "src/z.py",
            "stmts": 10,
            "covered": 9,
            "miss": 1,
            "cover": 90.0,
            "missing_lines": [20],
        },
        {
            "file": "src/a.cpp",
            "stmts": 4,
            "covered": 4,
            "miss": 0,
            "cover": 100.0,
            "missing_lines": [],
        },
    ]


def _function_rows() -> list[dict]:
    return [
        {
            "file": "src/z.py",
            "name": "uncovered",
            "symbol": "uncovered",
            "start_line": 20,
            "start_column": 3,
            "end_line": 25,
            "end_column": 10,
            "covered": False,
            "missing_lines": [20, 21],
        },
        {
            "file": "src/a.cpp",
            "name": "covered",
            "symbol": "_Z7coveredv",
            "start_line": 3,
            "start_column": 1,
            "end_line": 5,
            "end_column": 2,
            "covered": True,
            "missing_lines": [],
        },
    ]


def _changed_status() -> dict[str, dict[int, bool]]:
    return {
        "src/z.py": {
            20: False,
            21: True,
        },
        "src/a.cpp": {
            10: True,
        },
    }


def _policy(**overrides: float | int) -> dict[str, float | int]:
    policy: dict[str, float | int] = {
        "min_line_cov": 80.0,
        "min_file_cov": 80.0,
        "min_file_statements": 2,
        "min_func_cov": 80.0,
        "min_changed_line_cov": 80.0,
    }
    policy.update(overrides)
    return policy


def _target_signature(target) -> tuple:
    return (
        target.file_path,
        target.start_line,
        target.end_line,
        target.start_column,
        target.end_column,
        target.target_name,
        target.status,
        target.message,
        tuple(sorted(target.metrics.items())),
    )


def test_parse_changed_lines_canonicalizes_ranges_and_orders_paths(tmp_path: Path):
    root = _project(tmp_path)

    parsed = parse_changed_lines(
        root,
        ["src/a.cpp:14-15", "project/relative.py:10", "src/a.cpp:12"],
    )

    assert list(parsed) == ["project/relative.py", "src/a.cpp"]
    assert parsed == {"project/relative.py": {10}, "src/a.cpp": {12, 14, 15}}


@pytest.mark.parametrize(
    "spec",
    [
        "/absolute/src/a.cpp:1",
        "../outside.cpp:1",
        ":1",
        "src/a.cpp:0",
        "src/a.cpp:15-14",
        "src/a.cpp:1-10001",
        "src/a.cpp:not-a-line",
    ],
)
def test_parse_changed_lines_rejects_unsafe_or_malformed_specs(tmp_path: Path, spec: str):
    root = _project(tmp_path)

    with pytest.raises(ValueError):
        parse_changed_lines(root, [spec])


def test_parse_changed_lines_rejects_duplicates_non_files_and_bounds(tmp_path: Path):
    root = _project(tmp_path)

    with pytest.raises(ValueError):
        parse_changed_lines(root, ["src/a.cpp:10", "src/a.cpp:10"])
    with pytest.raises(ValueError):
        parse_changed_lines(root, ["src/a.cpp:10-11", "src/a.cpp:11"])
    with pytest.raises(ValueError):
        parse_changed_lines(root, ["project:1"])
    with pytest.raises(ValueError):
        parse_changed_lines(root, ["src/a.cpp:1-3"], max_lines=2)
    with pytest.raises(ValueError):
        parse_changed_lines(root, ["src/a.cpp:1"], max_lines=0)


def test_evaluate_coverage_policy_leaves_a_pass_target_for_each_scope():
    targets = evaluate_coverage_policy(
        _policy(),
        _coverage_files(),
        [
            {**_function_rows()[1]},
            {
                **_function_rows()[0],
                "covered": True,
                "missing_lines": [],
            },
        ],
        {
            "src/z.py": {
                20: True,
                21: True,
            },
            "src/a.cpp": {10: True},
        },
        changed_lines={"src/a.cpp": {10}, "src/z.py": {20, 21}},
    )

    assert targets
    assert all(target.status == EngineStatus.PASS for target in targets)
    names = " ".join(target.target_name.lower() for target in targets)
    assert "overall" in names
    assert "file" in names
    assert "function" in names
    assert "changed" in names
    assert {target.file_path for target in targets if "file" in target.target_name.lower()} >= {
        "src/a.cpp",
        "src/z.py",
    }
    assert all(target.start_line >= 1 for target in targets)


def test_evaluate_coverage_policy_reports_each_breached_scope_and_exact_function_geometry():
    targets = evaluate_coverage_policy(
        _policy(),
        [
            *_coverage_files(),
            {
                "file": "src/low.py",
                "stmts": 5,
                "covered": 1,
                "miss": 4,
                "cover": 20.0,
                "missing_lines": [30, 31, 32, 33],
            },
        ],
        _function_rows(),
        _changed_status(),
        changed_lines={"src/a.cpp": {10}, "src/z.py": {20, 21, 22}},
    )

    warnings = [target for target in targets if target.status == EngineStatus.WARN]
    assert warnings
    names = " ".join(target.target_name.lower() for target in warnings)
    assert "overall" in names
    assert "file" in names
    assert "function" in names
    assert "changed" in names

    function_target = next(
        target
        for target in warnings
        if target.file_path == "src/z.py" and target.target_name.endswith(":uncovered")
    )
    assert function_target.start_line == 20
    assert function_target.end_line == 25
    assert function_target.start_column == 3
    assert function_target.end_column == 10
    assert function_target.metrics["test_scope"] == "aggregate-project-suite"


def test_configured_changed_line_gate_is_error_when_no_changed_line_is_executable():
    targets = evaluate_coverage_policy(
        {"min_changed_line_cov": 80.0},
        [],
        [],
        {"src/a.cpp": {}},
        changed_lines={"src/a.cpp": {10}},
    )

    changed_errors = [
        target
        for target in targets
        if "changed" in target.target_name.lower() and target.status == EngineStatus.ERROR
    ]
    assert len(changed_errors) == 1
    assert changed_errors[0].metrics["threshold"] == 80.0
    assert "executable" in changed_errors[0].message.lower()
    assert changed_errors[0].file_path == "src/a.cpp"
    assert changed_errors[0].start_line == 10


def test_evaluate_coverage_policy_rejects_unbounded_inputs_and_is_deterministic():
    oversized = _coverage_files() * 50001
    with pytest.raises(ValueError):
        evaluate_coverage_policy(_policy(), oversized, [], {})

    first = evaluate_coverage_policy(
        _policy(),
        _coverage_files(),
        _function_rows(),
        _changed_status(),
        changed_lines={"src/a.cpp": {10}, "src/z.py": {20, 21, 22}},
    )
    second = evaluate_coverage_policy(
        _policy(),
        list(reversed(_coverage_files())),
        list(reversed(_function_rows())),
        {
            path: dict(reversed(list(lines.items())))
            for path, lines in reversed(list(_changed_status().items()))
        },
        changed_lines={"src/a.cpp": {10}, "src/z.py": {20, 21, 22}},
    )
    assert [_target_signature(target) for target in first] == [
        _target_signature(target) for target in second
    ]
