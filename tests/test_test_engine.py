"""Tests for Test Execution Engine, Coverage & TEM 5.0 Scoring."""

import json
from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.test import TestEngine


def test_test_engine_execution_and_tem_score(tmp_python_project: Path):
    engine = TestEngine(tmp_python_project)
    res = engine.run()

    assert res.status == EngineStatus.PASS
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
    rows = parsed["files"]
    assert len(rows) == 2
    worst = rows[0]
    assert worst["file"] == "src/pkg/core.py"
    assert worst["stmts"] == 4
    assert worst["cover"] == 50.0
    assert worst["branch_cover"] == 50.0
    assert worst["nb"] == 4
    assert worst["missing_lines"] == [5, 6]


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


def test_find_coverage_cmd_uses_venv_module_probe(tmp_path: Path, monkeypatch):
    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr("ici.engines.test.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda cmd, cwd=None, env=None: (0, "Coverage.py, version 7.x", "", 0.0),
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
            return (0, "Coverage.py, version 7.1.2", "", 0.0)
        return (1, "", "No module named coverage", 0.0)

    monkeypatch.setattr("ici.engines.test.run_process", fake_run)
    monkeypatch.setattr("ici.engines.test.find_uv", lambda: None)
    cmd = engine._find_coverage_cmd(["/proj/.venvx/bin/pytest"])
    assert cmd == ["/proj/.venvx/bin/python", "-m", "coverage"]


def test_coverage_run_uses_source_dirs_flag(tmp_path: Path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    (lib / "x.py").write_text("x = 1\n", encoding="utf-8")
    engine = TestEngine(tmp_path)
    monkeypatch.setattr(engine, "_find_coverage_cmd", lambda pc: ["cov"])
    monkeypatch.setattr(engine, "_parse_pytest_stdout", lambda out, targets: (2, 2, False))
    monkeypatch.setattr(engine, "_parse_coverage_json", lambda p: None)
    captured: list[list[str]] = []
    monkeypatch.setattr(
        "ici.engines.test.run_process",
        lambda cmd, cwd=None, env=None: captured.append(cmd) or (0, "", "", 0.0),
    )
    engine._run_python_tests([])
    cov_run = next(c for c in captured if "run" in c and "--branch" in c)
    assert "--source=lib" in cov_run
