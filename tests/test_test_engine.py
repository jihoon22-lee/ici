"""Tests for Test Execution Engine, Coverage & TEM 5.0 Scoring."""

import json
import sys
from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines.test import TestEngine


def test_test_engine_execution_and_tem_score(tmp_python_project: Path):
    engine = TestEngine(
        tmp_python_project,
        config={
            "engines": {
                "test": {
                    "mode": "pass_fail",
                    # A non-perfect TEM score is a warning that strict mode must fail.
                    "min_tem_score": 5.0,
                }
            }
        },
    )
    res = engine.run()

    assert res.extra["passed_tests"] == res.extra["total_tests"] >= 1
    assert res.extra["tem_score"] < 5.0
    assert res.status == EngineStatus.FAIL
    assert res.score is not None
    # TEM score must be between 0 and 5.0
    assert 0.0 <= res.score <= 5.0
    assert res.extra["passed_tests"] >= 1


def test_tem_formula_direct_calculation():
    """Enterprise TEM 5.0: min(LineCov,80)/80 * FuncCov/100 * PassRate * 5"""
    # Case 1: Line 90 (cap 80), Func 100, PassRate 1.0 -> 5.0
    assert round(min(80.0, 90.0) / 80.0 * 1.0 * 1.0 * 5.0, 2) == 5.0

    # Case 2: Line 40, Func 100, PassRate 1.0 -> 2.5
    assert round(min(80.0, 40.0) / 80.0 * 1.0 * 1.0 * 5.0, 2) == 2.5

    # Case 3: Branch-only 60 (->75 via *5/4), Func 80, PassRate 0.9 -> 3.38
    assert round(min(80.0, 60.0 * 1.25) / 80.0 * 0.8 * 0.9 * 5.0, 2) == 3.38


def test_tem_uses_line_coverage_and_pass_rate(tmp_path: Path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    engine._coverage_totals = {
        "stmts": 100,
        "miss": 30,
        "cover": 70.0,
        "branch_cover": 50.0,
    }
    monkeypatch.setattr(engine, "_measure_coverage", lambda pt, hf: (50.0, 95.0, []))
    monkeypatch.setattr(engine, "_run_python_tests", lambda t: (9, 10, False))
    monkeypatch.setattr(engine, "_run_cpp_tests", lambda t: (0, 0, False))

    res = engine.run()
    # Line 70 -> 70/80 * 0.95 * 0.9 * 5 = 3.74
    assert res.extra["tem_score"] == 3.74
    assert res.extra["line_coverage"] == 70.0
    assert res.extra["pass_rate"] == 0.9
    assert "Line: 70.0%" in res.summary


def test_tem_branch_only_scaling(tmp_path: Path, monkeypatch):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    engine._coverage_totals = {
        "stmts": 100,
        "miss": 0,
        "cover": None,
        "branch_cover": 60.0,
    }
    monkeypatch.setattr(engine, "_measure_coverage", lambda pt, hf: (60.0, 95.0, []))
    monkeypatch.setattr(engine, "_run_python_tests", lambda t: (10, 10, False))
    monkeypatch.setattr(engine, "_run_cpp_tests", lambda t: (0, 0, False))

    res = engine.run()
    # Branch 60 * 1.25 = 75 -> 75/80 * 0.95 * 1.0 * 5 = 4.45
    assert res.extra["tem_score"] == 4.45
    assert "Branch: 60.0%" in res.summary


def test_parse_coverage_json(tmp_path: Path):
    engine = TestEngine(tmp_path)
    sample = {
        "files": {
            "src/pkg/core.py": {
                "executed_lines": [1, 2],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 4,
                    "missing_lines": 2,
                    "num_branches": 4,
                    "covered_branches": 2,
                },
                "missing_lines": [5, 6],
            },
            "src/pkg/util.py": {
                "executed_lines": [1, 2, 3],
                "summary": {
                    "covered_lines": 3,
                    "num_statements": 3,
                    "missing_lines": 0,
                    "num_branches": 2,
                    "covered_branches": 2,
                },
                "missing_lines": [],
            },
        },
        "totals": {
            "covered_lines": 5,
            "num_statements": 7,
            "missing_lines": 2,
            "num_branches": 6,
            "covered_branches": 4,
        },
    }
    json_path = tmp_path / "coverage.json"
    json_path.write_text(json.dumps(sample), encoding="utf-8")

    parsed = engine._parse_coverage_json(json_path)
    assert parsed is not None
    assert parsed["branch_cov"] == 66.7
    assert parsed["line_cov"] == 71.4
    files = parsed["files"]
    assert set(files) == {"src/pkg/core.py", "src/pkg/util.py"}
    assert files["src/pkg/core.py"]["executed_lines"] == [1, 2]
    assert files["src/pkg/core.py"]["missing_lines"] == [5, 6]


def test_parse_coverage_json_rejects_incomplete_file_summary(tmp_path: Path):
    engine = TestEngine(tmp_path)
    json_path = tmp_path / "coverage.json"
    json_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/pkg/core.py": {
                        "executed_lines": [1],
                        "missing_lines": [],
                        "summary": {},
                    }
                },
                "totals": {
                    "covered_lines": 1,
                    "num_statements": 1,
                    "missing_lines": 0,
                    "num_branches": 0,
                    "covered_branches": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    assert engine._parse_coverage_json(json_path) is None


def test_parse_coverage_json_rejects_inconsistent_measurement_counts(tmp_path: Path):
    engine = TestEngine(tmp_path)
    json_path = tmp_path / "coverage.json"
    json_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/core.py": {
                        "executed_lines": [1, 2],
                        "missing_lines": [3],
                        "summary": {
                            "covered_lines": 2,
                            "num_statements": 4,
                            "missing_lines": 1,
                            "num_branches": 2,
                            "covered_branches": 3,
                        },
                    }
                },
                "totals": {
                    "covered_lines": 2,
                    "num_statements": 4,
                    "missing_lines": 1,
                    "num_branches": 2,
                    "covered_branches": 3,
                },
            }
        ),
        encoding="utf-8",
    )

    assert engine._parse_coverage_json(json_path) is None


def test_parse_coverage_json_rejects_invalid_or_overlapping_line_arrays(tmp_path: Path):
    engine = TestEngine(tmp_path)
    json_path = tmp_path / "coverage.json"
    json_path.write_text(
        json.dumps(
            {
                "files": {
                    "src/core.py": {
                        "executed_lines": [0, 2],
                        "missing_lines": [2],
                        "summary": {
                            "covered_lines": 2,
                            "num_statements": 3,
                            "missing_lines": 1,
                            "num_branches": 0,
                            "covered_branches": 0,
                        },
                    }
                },
                "totals": {
                    "covered_lines": 2,
                    "num_statements": 3,
                    "missing_lines": 1,
                    "num_branches": 0,
                    "covered_branches": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    assert engine._parse_coverage_json(json_path) is None


def test_required_zero_statement_coverage_is_not_measured(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_zero.py").write_text("def test_zero():\n    assert True\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {"engines": {"test": {"coverage_required": True}}},
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: ["coverage"])

    def fake_run(cmd, cwd=None, env=None):
        if "run" in cmd:
            return ProcessResult(0, "tests/test_zero.py::test_zero PASSED\n", "", 0.01)
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "files": {
                        "src/app.py": {
                            "executed_lines": [],
                            "missing_lines": [],
                            "summary": {
                                "covered_lines": 0,
                                "num_statements": 0,
                                "missing_lines": 0,
                                "num_branches": 0,
                                "covered_branches": 0,
                            },
                        }
                    },
                    "totals": {
                        "covered_lines": 0,
                        "num_statements": 0,
                        "missing_lines": 0,
                        "num_branches": 0,
                        "covered_branches": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_required_python_coverage_rejects_unrelated_source_report(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "coverage_required": True,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: ["coverage"])

    def fake_run(cmd, cwd=None, env=None):
        if "run" in cmd:
            return ProcessResult(0, "tests/test_app.py::test_app PASSED\n", "", 0.01)
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "files": {
                        "vendor.py": {
                            "executed_lines": [1],
                            "missing_lines": [],
                            "summary": {
                                "covered_lines": 1,
                                "num_statements": 1,
                                "missing_lines": 0,
                                "num_branches": 0,
                                "covered_branches": 0,
                            },
                        }
                    },
                    "totals": {
                        "covered_lines": 1,
                        "num_statements": 1,
                        "missing_lines": 0,
                        "num_branches": 0,
                        "covered_branches": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_compute_python_function_coverage(tmp_path: Path):
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        """def covered_func():
    a = 1
    return a


def partial_func():
    b = 2
    c = 3
    return b


def never_called():
    z = 99
    return z
""",
        encoding="utf-8",
    )
    engine = TestEngine(tmp_path)
    cov_data = {
        "files": {
            "src/pkg/core.py": {
                "executed_lines": [1, 2, 3, 7],
                "missing_lines": [8, 13, 14],
                "summary": {},
            }
        }
    }
    rows = engine._compute_python_function_coverage(cov_data)
    by_name = {r["name"]: r for r in rows}
    assert by_name["covered_func"]["covered"] is True
    assert by_name["partial_func"]["covered"] is True
    assert by_name["never_called"]["covered"] is False
    assert by_name["never_called"]["missing_lines"] == [13, 14]


def test_parse_gcov_functions(tmp_path: Path):
    engine = TestEngine(tmp_path)
    cov_dir = tmp_path / "build" / "tests"
    cov_dir.mkdir(parents=True)
    (cov_dir / "src#calc.cpp.gcov").write_text(
        """function _Z3addii called 1 returned 100% blocks executed 75%
function _Z3subii called 0 returned 0% blocks executed 0%
""",
        encoding="utf-8",
    )
    rows = engine._parse_gcov_functions(cov_dir, {"src/calc.cpp"})
    assert len(rows) == 2
    by_name = {r["name"]: r for r in rows}
    assert by_name["_Z3addii"]["covered"] is True
    assert by_name["_Z3subii"]["covered"] is False


def test_parse_gcov_dir(tmp_path: Path):
    engine = TestEngine(tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "calc.cpp").write_text("", encoding="utf-8")
    cov_dir = tmp_path / "build" / "tests"
    cov_dir.mkdir(parents=True)
    abs_encoded = "#" + str(tmp_path).lstrip("/").replace("/", "#") + "#src#calc.cpp"
    gcov_file = cov_dir / f"{abs_encoded}.gcov"
    gcov_file.write_text(
        """        -:    0:Source:/tmp/x/src/calc.cpp
        -:    1:#include "calc.h"
function _Z3addii called 1 returned 100% blocks executed 75%
        1:    2:int add(int a, int b) {
        1:    3:    if (a > 0) { return a + b; }
branch  0 taken 100% (fallthrough)
branch  1 taken 0%
    #####:    4:    return b;
        -:    5:}
""",
        encoding="utf-8",
    )

    rows = engine._parse_gcov_dir(cov_dir, {"src/calc.cpp"})
    assert len(rows) == 1
    row = rows[0]
    assert row["file"] == "src/calc.cpp"
    assert row["stmts"] == 3
    assert row["covered"] == 2
    assert row["miss"] == 1
    assert row["cover"] == 66.7
    assert row["branch_cover"] == 50.0
    assert row["missing_lines"] == [4]


def test_parse_gcov_dir_skips_test_files(tmp_path: Path):
    engine = TestEngine(tmp_path)
    cov_dir = tmp_path / "build" / "tests"
    cov_dir.mkdir(parents=True)
    (cov_dir / "#tmp#x#tests#test_calc.cpp.gcov").write_text(
        "        1:    2:int main() {\n", encoding="utf-8"
    )

    rows = engine._parse_gcov_dir(cov_dir, {"src/calc.cpp"})
    assert rows == []


def test_parse_gcov_dir_skips_files_without_statements(tmp_path: Path):
    engine = TestEngine(tmp_path)
    cov_dir = tmp_path / "build" / "tests"
    cov_dir.mkdir(parents=True)
    (cov_dir / "src#calc.cpp.gcov").write_text(
        '        -:    0:Source:src/calc.cpp\n        -:    1:#include "calc.h"\n',
        encoding="utf-8",
    )

    assert engine._parse_gcov_dir(cov_dir, {"src/calc.cpp"}) == []


def test_find_coverage_cmd_uses_venv_module_probe(tmp_path: Path, monkeypatch):
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda cmd, cwd=None, env=None: ProcessResult(0, "Coverage.py, version 7.x", "", 0.0),
    )
    monkeypatch.setattr("ici.engines.test.find_uv", lambda: None)
    cmd = engine._find_coverage_cmd(None)
    assert cmd is not None
    assert cmd[0].endswith(".venv/bin/python")
    assert cmd[1:] == ["-m", "coverage"]


def test_find_coverage_cmd_uses_pytest_interpreter(tmp_path: Path, monkeypatch):
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.shutil.which", lambda name: None)

    def fake_run(cmd, cwd=None, env=None):
        if "--version" in cmd and cmd[0] == "/proj/.venvx/bin/python":
            return ProcessResult(0, "Coverage.py, version 7.1.2", "", 0.0)
        return ProcessResult(1, "", "No module named coverage", 0.0)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)
    monkeypatch.setattr("ici.engines.test.find_uv", lambda: None)
    cmd = engine._find_coverage_cmd(["/proj/.venvx/bin/pytest"])
    assert cmd == ["/proj/.venvx/bin/python", "-m", "coverage"]


def test_find_coverage_cmd_rejects_truncated_probe(tmp_path: Path, monkeypatch):
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda *args, **kwargs: ProcessResult(
            0, "Coverage.py, version 7.1.2", "", 0.01, truncated=True
        ),
    )
    monkeypatch.setattr("ici.engines.test.find_uv", lambda: None)

    assert engine._find_coverage_cmd(["/proj/.venvx/bin/pytest"]) is None


def test_coverage_run_uses_source_dirs_flag(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    (lib / "x.py").write_text("x = 1\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda pc: ["cov"])
    monkeypatch.setattr(engine, "_parse_pytest_stdout", lambda out, targets: (2, 2, False))
    monkeypatch.setattr(engine, "_parse_coverage_json", lambda p, *_args: None)
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda cmd, cwd=None, env=None: captured.append(cmd) or ProcessResult(0, "", "", 0.0),
    )
    engine._run_python_tests([])
    cov_run = next(c for c in captured if "run" in c and "--branch" in c)
    assert "--source=lib" in cov_run


def test_coverage_missing_json_is_error_after_attempt(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_coverage.py").write_text("def test_coverage():\n    pass\n", encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {"engines": {"test": {"mode": "pass_fail", "coverage_required": True}}},
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _pytest_cmd: ["coverage"])

    def fake_run(cmd, cwd=None, env=None):
        if "run" in cmd:
            return ProcessResult(
                0,
                "tests/test_coverage.py::test_coverage PASSED\n",
                "",
                0.01,
            )
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)
    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_coverage_malformed_json_is_error_after_attempt(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_coverage.py").write_text("def test_coverage():\n    pass\n", encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {"engines": {"test": {"mode": "pass_fail", "coverage_required": True}}},
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _pytest_cmd: ["coverage"])

    def fake_run(cmd, cwd=None, env=None):
        if "run" in cmd:
            return ProcessResult(
                0,
                "tests/test_coverage.py::test_coverage PASSED\n",
                "",
                0.01,
            )
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.write_text("not json", encoding="utf-8")
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)
    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_python_test_timeout_cannot_report_pass(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_timeout.py").write_text("def test_timeout():\n    pass\n", encoding="utf-8")
    engine = TestEngine(tmp_path, {"engines": {"test": {"mode": "pass_fail"}}})
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _pytest_cmd: None)
    monkeypatch.setattr(
        "ici.engines.test.shutil.which",
        lambda name: "/usr/bin/pytest" if name == "pytest" else None,
    )
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda *args, **kwargs: ProcessResult(
            124, "tests/test_timeout.py::test_timeout PASSED", "", 0.05, timed_out=True
        ),
    )
    monkeypatch.setattr(
        engine, "_measure_coverage", lambda _proj_type, _has_failure: (100.0, 100.0, [])
    )

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_python_test_truncated_output_cannot_report_pass(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_truncated.py").write_text("def test_truncated():\n    pass\n", encoding="utf-8")
    engine = TestEngine(tmp_path, {"engines": {"test": {"mode": "pass_fail"}}})
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _pytest_cmd: None)
    monkeypatch.setattr(
        "ici.engines.test.shutil.which",
        lambda name: "/usr/bin/pytest" if name == "pytest" else None,
    )
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda *args, **kwargs: ProcessResult(
            0, "tests/test_truncated.py::test_truncated PASSED", "", 0.05, truncated=True
        ),
    )
    monkeypatch.setattr(
        engine, "_measure_coverage", lambda _proj_type, _has_failure: (100.0, 100.0, [])
    )

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_resolve_python_prefers_configured_interpreter(tmp_path: Path):
    configured = tmp_path / "tools" / "python"
    engine = TestEngine(
        tmp_path,
        {"engines": {"test": {"python": str(configured)}}},
    )

    assert engine._resolve_python() == [str(configured)]


def test_resolve_python_uses_project_venv_before_sys_executable(tmp_path: Path, monkeypatch):
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(sys, "executable", "/fallback/python")

    assert engine._resolve_python() == [str(venv_python)]


def test_resolve_python_falls_back_to_sys_executable(tmp_path: Path, monkeypatch):
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(sys, "executable", "/fallback/python")

    assert engine._resolve_python() == ["/fallback/python"]


def test_python_tools_use_one_interpreter_with_module_invocation(tmp_path: Path):
    engine = TestEngine(tmp_path)
    python_cmd = ["/project/.venv/bin/python"]

    coverage_cmd = engine._build_coverage_run_cmd([*python_cmd, "-m", "coverage"])
    assert coverage_cmd[0:5] == [
        *python_cmd,
        "-m",
        "coverage",
        "run",
        "--branch",
    ]
    assert coverage_cmd[-6:] == [
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-v",
        "tests",
    ]


def test_zero_collected_tests_is_failure_with_zero_total(tmp_path: Path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_empty.py").write_text("# no tests\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)
    monkeypatch.setattr(engine, "_resolve_python", lambda: ["/project/python"])
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda cmd, **kwargs: ProcessResult(5, "collected 0 items\n", "", 0.01),
    )

    result = engine.run()

    assert result.status == EngineStatus.FAIL
    assert result.extra["total_tests"] == 0


def test_optional_coverage_is_estimated_warning_not_threshold_pass(
    tmp_python_project: Path, monkeypatch
):
    engine = TestEngine(
        tmp_python_project,
        {
            "engines": {
                "test": {
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                    "coverage_required": False,
                }
            }
        },
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)
    result = engine.run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["coverage_source"] == "estimated"


def test_required_coverage_unavailable_is_error_and_not_run(tmp_python_project: Path, monkeypatch):
    engine = TestEngine(
        tmp_python_project,
        {"engines": {"test": {"coverage_required": True}}},
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_required_cpp_coverage_without_gcov_is_error_and_not_run(tmp_path: Path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.cpp").write_text(
        "int add(int a, int b) { return a + b; }\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.cpp").write_text(
        "int main() { return 0; }\n", encoding="utf-8"
    )
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "coverage_required": True,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )
    monkeypatch.setattr("ici.engines.test.detect_project_type", lambda _root: "cpp")
    monkeypatch.setattr(
        "ici.engines.test.shutil.which", lambda name: "/usr/bin/g++" if name == "g++" else None
    )
    monkeypatch.setattr(engine, "_run_cpp_tests", lambda targets: (1, 1, False))

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


@pytest.mark.parametrize("returncode", [-1, -9])
def test_cpp_spawn_or_signal_failure_is_error_not_assertion_failure(
    tmp_path: Path, monkeypatch, returncode: int
):
    src = tmp_path / "src"
    src.mkdir()
    (src / "calc.cpp").write_text("int add(int a, int b) { return a + b; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_calc.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text('type = "cpp"\n', encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.detect_project_type", lambda _root: "cpp")
    monkeypatch.setattr(
        "ici.engines.test.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda *args, **kwargs: ProcessResult(returncode, "", "spawn failed", 0.01),
    )

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert not any(target.status == EngineStatus.FAIL for target in result.targets)


def test_cpp_positive_test_exit_remains_failure(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "calc.cpp").write_text("int add(int a, int b) { return a + b; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_calc.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text('type = "cpp"\n', encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.detect_project_type", lambda _root: "cpp")
    monkeypatch.setattr(
        "ici.engines.test.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    calls = 0

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ProcessResult(0, "", "", 0.01)
        return ProcessResult(1, "assertion failed", "", 0.01)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    result = engine.run()

    assert result.status == EngineStatus.FAIL
    assert any(target.status == EngineStatus.FAIL for target in result.targets)


def test_unittest_fallback_runs_when_pytest_module_is_unavailable(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_legacy.py").write_text(
        "import unittest\n\nclass Legacy(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    engine = TestEngine(tmp_path)
    python = ["/project/.venv/bin/python"]
    monkeypatch.setattr(engine, "_resolve_python", lambda: python)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "pytest" in cmd:
            return ProcessResult(1, "", "No module named pytest", 0.01)
        return ProcessResult(
            0,
            "test_ok (test_legacy.Legacy) ... ok\n\nRan 1 test\n",
            "",
            0.01,
        )

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    parsed = engine._run_python_tests([])

    assert parsed == (1, 1, False)
    assert commands[0][:2] == [*python, "-m"]
    assert commands[0][2] == "pytest"
    assert commands[1][:2] == [*python, "-m"]
    assert commands[1][2] == "unittest"


def test_unittest_fallback_also_reachable_from_coverage_first_path(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_legacy.py").write_text(
        "import unittest\n\nclass Legacy(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
        encoding="utf-8",
    )
    engine = TestEngine(tmp_path)
    python = ["/project/.venv/bin/python"]
    monkeypatch.setattr(engine, "_resolve_python", lambda: python)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: [*python, "-m", "coverage"])
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "run" in cmd or "pytest" in cmd:
            return ProcessResult(1, "", "No module named pytest", 0.01)
        return ProcessResult(
            0,
            "test_ok (test_legacy.Legacy) ... ok\n\nRan 1 test\n",
            "",
            0.01,
        )

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    parsed = engine._run_python_tests([])

    assert parsed == (1, 1, False)
    assert any("coverage" in cmd and "run" in cmd for cmd in commands)
    assert commands[-1][2] == "unittest"


def test_pytest_assertion_text_does_not_trigger_unittest_fallback(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_failure.py").write_text(
        "def test_failure():\n    assert False\n", encoding="utf-8"
    )
    engine = TestEngine(tmp_path)
    python = ["/project/.venv/bin/python"]
    monkeypatch.setattr(engine, "_resolve_python", lambda: python)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return ProcessResult(
            1,
            "tests/test_failure.py::test_failure FAILED\n",
            "E AssertionError: No module named pytest\n",
            0.01,
        )

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    assert engine._run_python_tests([]) == (0, 1, True)
    assert len(commands) == 1


def test_pytest_zero_collection_does_not_fall_back_to_unittest(tmp_path: Path, monkeypatch):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_empty.py").write_text("# no tests\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda *args, **kwargs: ProcessResult(5, "collected 0 items\n", "", 0.01),
    )
    monkeypatch.setattr(
        engine,
        "_run_unittest",
        lambda *args, **kwargs: pytest.fail(
            "unittest fallback must not run after pytest collection"
        ),
    )

    assert engine._run_python_tests([]) == (0, 0, True)


def test_optional_zero_tests_remain_fail_with_estimated_evidence(tmp_path: Path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_empty.py").write_text("# no tests\n", encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "coverage_required": False,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda _python: None)
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda cmd, **kwargs: ProcessResult(5, "collected 0 items\n", "", 0.01),
    )

    result = engine.run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["coverage_source"] == "estimated"


def test_hybrid_zero_cpp_tests_downgrade_python_coverage_evidence(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    (src / "calc.cpp").write_text("int calc() { return 1; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    assert app() == 1\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text('type = "hybrid"\n', encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {
            "engines": {
                "test": {
                    "coverage_required": False,
                    "min_tem_score": 0.0,
                    "min_branch_cov": 0.0,
                    "min_func_cov": 0.0,
                }
            }
        },
    )
    engine._coverage_data = {
        "files": {
            "src/app.py": {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 2,
                    "missing_lines": 0,
                    "num_branches": 0,
                    "covered_branches": 0,
                },
            }
        },
        "line_cov": 100.0,
        "branch_cov": None,
        "totals": {"stmts": 2, "miss": 0, "cover": 100.0, "branch_cover": None},
    }
    monkeypatch.setattr(engine, "_run_python_tests", lambda _targets: (1, 1, False))

    result = engine.run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["coverage_source"] == "coverage.py (partial)"
    assert "ESTIMATED" in result.summary


@pytest.mark.parametrize(
    "probe",
    [
        ProcessResult(124, "", "timeout", 0.01, timed_out=True),
        ProcessResult(0, "", "", 0.01, truncated=True),
        ProcessResult(-1, "", "spawn failed", 0.01),
        ProcessResult(-9, "", "killed", 0.01),
        ProcessResult(2, "", "coverage configuration failed", 0.01),
    ],
)
def test_coverage_probe_failures_record_tool_error(tmp_path: Path, monkeypatch, probe):
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.run_process", lambda *args, **kwargs: probe)

    assert engine._find_coverage_cmd(None) is None
    assert engine._tool_errors


def test_coverage_probe_clear_module_absence_is_optional(tmp_path: Path, monkeypatch):
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", "No module named coverage", 0.01),
    )

    assert engine._find_coverage_cmd(None) is None
    assert engine._tool_errors == []


def test_coverage_state_does_not_leak_between_runs(tmp_path: Path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("def app():\n    return 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    coverage_available = {"enabled": True}
    monkeypatch.setattr(
        engine,
        "_find_coverage_cmd",
        lambda _python: (
            ["/project/python", "-m", "coverage"] if coverage_available["enabled"] else None
        ),
    )

    def fake_run(cmd, **kwargs):
        if "json" in cmd:
            json_path = Path(cmd[cmd.index("-o") + 1])
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(
                    {
                        "files": {
                            "src/app.py": {
                                "executed_lines": [1, 2],
                                "missing_lines": [],
                                "summary": {
                                    "covered_lines": 2,
                                    "num_statements": 2,
                                    "missing_lines": 0,
                                    "num_branches": 0,
                                    "covered_branches": 0,
                                },
                            }
                        },
                        "totals": {
                            "covered_lines": 2,
                            "num_statements": 2,
                            "missing_lines": 0,
                            "num_branches": 0,
                            "covered_branches": 0,
                        },
                    }
                ),
                encoding="utf-8",
            )
        return ProcessResult(0, "tests/test_app.py::test_app PASSED\n", "", 0.01)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)
    first = engine.run()
    coverage_available["enabled"] = False
    second = engine.run()

    assert first.extra["coverage_source"] == "coverage.py"
    assert second.extra["coverage_source"] == "estimated"
    assert second.evidence == EvidenceState.ESTIMATED


def test_pytest_commands_do_not_force_project_local_temp(tmp_path: Path):
    engine = TestEngine(tmp_path)

    coverage_command = engine._build_coverage_run_cmd(["python", "-m", "coverage"])
    pytest_command = [*engine._find_pytest_cmd(), "-o", "addopts=", "-v", "tests"]

    assert "--basetemp" not in coverage_command
    assert "-s" not in coverage_command
    assert "--basetemp" not in pytest_command
    assert "-s" not in pytest_command


def test_wsl_python_test_env_uses_system_temp_for_pytest_capture(tmp_path: Path, monkeypatch):
    engine = TestEngine(tmp_path)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("TMPDIR", "/mnt/c/Users/USER/AppData/Local/Temp")
    monkeypatch.setenv("TMP", "/mnt/c/Users/USER/AppData/Local/Temp")
    monkeypatch.setenv("TEMP", "/mnt/c/Users/USER/AppData/Local/Temp")

    env = engine._build_python_test_env()

    assert env["TMPDIR"] == "/tmp"
    assert env["TMP"] == "/tmp"
    assert env["TEMP"] == "/tmp"
    assert str(tmp_path) not in env["TMPDIR"]


def test_hybrid_sources_without_tests_are_zero_test_failures(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "src" / "calc.cpp").write_text("int value() { return 1; }\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text('type = "hybrid"\n', encoding="utf-8")

    result = TestEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert result.extra["total_tests"] == 0
    assert {target.target_name for target in result.targets} >= {
        "[Python] Tests",
        "[C++] Tests",
    }


def test_hybrid_one_language_tests_still_fails_for_missing_other_language_tests(
    tmp_path: Path, monkeypatch
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "src" / "calc.cpp").write_text("int value() { return 1; }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text(
        "def test_app():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "ici.toml").write_text('type = "hybrid"\n', encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(engine, "_run_python_tests", lambda targets: (1, 1, False))

    result = engine.run()

    assert result.status == EngineStatus.FAIL
    assert result.extra["total_tests"] == 1
    assert any(target.target_name == "[C++] Tests" for target in result.targets)


def test_required_coverage_exit_five_stays_zero_test_failure(tmp_path: Path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_empty.py").write_text("# no tests\n", encoding="utf-8")
    engine = TestEngine(
        tmp_path,
        {"engines": {"test": {"coverage_required": True}}},
    )
    monkeypatch.setattr(
        engine,
        "_find_coverage_cmd",
        lambda _python: ["/project/python", "-m", "coverage"],
    )
    commands: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        if "run" in cmd:
            return ProcessResult(5, "collected 0 items\n", "", 0.01)
        raise AssertionError("coverage JSON must not run when pytest collects zero tests")

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)

    result = engine.run()

    assert result.status == EngineStatus.ERROR
    assert result.extra["total_tests"] == 0
    assert len(commands) == 1
    assert any(
        target.target_name == "[Python] Tests" and target.status == EngineStatus.FAIL
        for target in result.targets
    )
