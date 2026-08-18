"""Tests for Line Count Engine and 500/1000 line threshold."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.line import LineCountEngine


def test_line_count_pass(tmp_python_project: Path):
    engine = LineCountEngine(tmp_python_project)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert res.extra["code"] > 0
    assert len(res.targets) > 0


def test_line_count_500_warning(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    big_file = src / "big.py"
    # Write 600 lines of code
    big_file.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.WARN
    target = next(t for t in res.targets if t.file_path == "src/big.py")
    assert target.status == EngineStatus.WARN


def test_line_count_1000_error(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    giant_file = src / "giant.py"
    # Write 1100 lines of code
    giant_file.write_text("\n".join(f"x_{i} = {i}" for i in range(1100)), encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    target = next(t for t in res.targets if t.file_path == "src/giant.py")
    assert target.status == EngineStatus.FAIL


def test_line_gate_excludes_test_code(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    big_test = tests / "big_test.py"
    big_test.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    target = next(t for t in res.targets if t.file_path == "tests/big_test.py")
    assert target.status == EngineStatus.PASS
    assert res.extra["code"] > 0


def test_line_gate_dirs_configurable(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    big_test = tests / "big_test.py"
    big_test.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")

    engine = LineCountEngine(tmp_path, config={"engines": {"line": {"gate_dirs": ["tests"]}}})
    res = engine.run()
    assert res.status == EngineStatus.WARN
    target = next(t for t in res.targets if t.file_path == "tests/big_test.py")
    assert target.status == EngineStatus.WARN


def test_line_exclude_dirs(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "big.py").write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")
    engine = LineCountEngine(tmp_path, config={"engines": {"line": {"exclude_dirs": ["src"]}}})
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert not any(t.file_path.startswith("src/") for t in res.targets)
