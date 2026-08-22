"""Tests for python_compat engine."""

import sys
from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.python_compat import PythonCompatEngine


def test_no_python_sources_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("int main(){}\n", encoding="utf-8")
    result = PythonCompatEngine(tmp_path, {"engines": {"python_compat": {}}}).run()
    assert result.status == EngineStatus.PASS
    assert "not applicable" in result.summary


def test_valid_sources_compile_pass(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = PythonCompatEngine(
        tmp_path, {"engines": {"python_compat": {"targets": [sys.executable]}}}
    ).run()
    assert result.status == EngineStatus.PASS
    assert result.tool_evidence


def test_syntax_error_fails_target(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    cfg = {
        "engines": {
            "python_compat": {"mode": "pass_warn", "required": False, "targets": [sys.executable]}
        }
    }
    result = PythonCompatEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.WARN  # pass_warn downgrades FAIL
    assert any(t.status == EngineStatus.FAIL for t in result.targets)


def test_multiple_targets_reported_individually(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("y = 2\n", encoding="utf-8")
    cfg = {
        "engines": {
            "python_compat": {
                "targets": [sys.executable, sys.executable],
            }
        }
    }
    result = PythonCompatEngine(tmp_path, cfg).run()
    passed = [t for t in result.targets if t.status == EngineStatus.PASS]
    assert len(passed) == 2


def test_invalid_interpreter_errors(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("z = 3\n", encoding="utf-8")
    cfg = {
        "engines": {
            "python_compat": {
                "mode": "pass_warn",
                "required": False,
                "targets": ["/nonexistent/python-interpreter"],
            }
        }
    }
    result = PythonCompatEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.ERROR
