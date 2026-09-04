"""Configuration and internal-evidence contracts for coverage policy."""

from __future__ import annotations

from copy import deepcopy

import pytest

from ici.config import DEFAULT_CONFIG
from ici.config_schema import ConfigError, validate_config
from ici.engines.coverage_policy import build_changed_line_status
from ici.engines.coverage_support import build_coverage_summary


def _config() -> dict:
    config = deepcopy(DEFAULT_CONFIG)
    config["engines"]["test"].update(
        {
            "min_line_cov": 82.5,
            "min_file_cov": 78.0,
            "min_file_statements": 3,
            "min_changed_line_cov": 90.0,
            "max_coverage_regression": 2.5,
            "changed_lines": ["src/a.cpp:10-12", "src/module.py:20"],
        }
    )
    return config


def test_validate_config_accepts_coverage_policy_values():
    validate_config(_config())


@pytest.mark.parametrize(
    "key",
    [
        "min_line_cov",
        "min_file_cov",
        "min_changed_line_cov",
        "max_coverage_regression",
    ],
)
def test_validate_config_rejects_boolean_coverage_numbers(key: str):
    config = _config()
    config["engines"]["test"][key] = True

    with pytest.raises(ConfigError, match=rf"engines\.test\.{key}"):
        validate_config(config)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("min_line_cov", -0.1),
        ("min_line_cov", 100.1),
        ("min_file_cov", -0.1),
        ("min_file_cov", 100.1),
        ("min_changed_line_cov", -0.1),
        ("min_changed_line_cov", 100.1),
        ("max_coverage_regression", -0.1),
        ("max_coverage_regression", 100.1),
        ("min_file_statements", 0),
        ("min_file_statements", True),
    ],
)
def test_validate_config_rejects_out_of_range_coverage_values(key: str, value: object):
    config = _config()
    config["engines"]["test"][key] = value

    with pytest.raises(ConfigError, match=rf"engines\.test\.{key}"):
        validate_config(config)


@pytest.mark.parametrize("changed_lines", [["src/a.cpp:1", 2], [""], [None], "src/a.cpp:1"])
def test_validate_config_rejects_invalid_changed_line_list_items(changed_lines):
    config = _config()
    config["engines"]["test"]["changed_lines"] = changed_lines

    with pytest.raises(ConfigError, match=r"engines\.test\.changed_lines"):
        validate_config(config)


def test_default_config_exposes_conservative_line_file_and_statement_defaults():
    test_config = DEFAULT_CONFIG["engines"]["test"]

    assert test_config["min_line_cov"] == 80.0
    assert test_config["min_file_cov"] == 80.0
    assert test_config["min_file_statements"] == 5


def test_build_changed_line_status_merges_python_and_cpp_internal_lines_deterministically():
    coverage_data = {
        "files": {
            "src/z.py": {
                "executed_lines": [8, 6],
                "missing_lines": [7, 9],
            },
            "src/a.py": {
                "executed_lines": [3, 1],
                "missing_lines": [2],
            },
        }
    }
    cpp_rows = [
        {
            "file": "src/b.cpp",
            "executable_lines": [14, 12, 13],
            "covered_lines": [14, 12],
        }
    ]

    status = build_changed_line_status(coverage_data, cpp_rows)

    assert list(status) == ["src/a.py", "src/b.cpp", "src/z.py"]
    assert status == {
        "src/a.py": {1: True, 2: False, 3: True},
        "src/b.cpp": {12: True, 13: False, 14: True},
        "src/z.py": {6: True, 7: False, 8: True, 9: False},
    }
    assert list(status["src/a.py"]) == [1, 2, 3]
    assert list(status["src/b.cpp"]) == [12, 13, 14]
    assert list(status["src/z.py"]) == [6, 7, 8, 9]


def test_build_coverage_summary_strips_internal_line_lists_from_public_rows():
    coverage_data = {
        "files": {
            "src/module.py": {
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 3,
                    "missing_lines": 1,
                    "num_branches": 0,
                    "covered_branches": 0,
                },
                "missing_lines": [7],
                "executed_lines": [5, 6],
            }
        },
        "totals": {"cover": 66.7},
    }
    cpp_rows = [
        {
            "file": "src/a.cpp",
            "stmts": 4,
            "covered": 3,
            "miss": 1,
            "cover": 75.0,
            "branch_cover": None,
            "nb": 0,
            "cb": 0,
            "missing_lines": [11],
            "executable_lines": [10, 11, 12, 13],
            "covered_lines": [10, 12, 13],
        }
    ]

    files, totals, source = build_coverage_summary(coverage_data, cpp_rows)

    assert [row["file"] for row in files] == ["src/module.py", "src/a.cpp"]
    assert source == "coverage.py/gcov"
    assert totals == {
        "stmts": 7,
        "miss": 2,
        "cover": 71.4,
        "branch_cover": None,
    }
    for row in files:
        assert "executable_lines" not in row
        assert "covered_lines" not in row
