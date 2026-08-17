"""Tests for Complexity and Exception Safety Engines."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.complexity import ComplexityEngine
from ici.engines.exception import ExceptionSafetyEngine


def test_complexity_engine(tmp_python_project: Path):
    engine = ComplexityEngine(tmp_python_project)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert res.score is not None
    assert len(res.targets) > 0


def test_exception_safety_detects_swallowed_error(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    bad_py = src / "bad_error.py"
    bad_py.write_text(
        """def dangerous():
    try:
        x = 1 / 0
    except Exception:
        pass
""",
        encoding="utf-8",
    )

    engine = ExceptionSafetyEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert any("ErrorSwallowing" in t.target_name for t in res.targets)


def test_exception_safety_detects_bare_except(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    bad_py = src / "bare.py"
    bad_py.write_text(
        """def bad_bare():
    try:
        x = 1
    except:
        print("error")
""",
        encoding="utf-8",
    )

    engine = ExceptionSafetyEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert any("BareExcept" in t.target_name for t in res.targets)
