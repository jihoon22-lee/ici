"""Tests for resource engine."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.resource import ResourceEngine

_CFG = {"engines": {"resource": {"mode": "pass_warn"}}}


def test_clean_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "def foo():\n    with open('x') as f:\n        return f.read()\n", encoding="utf-8"
    )
    result = ResourceEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_open_without_with_warns(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("f = open('x')\n", encoding="utf-8")
    result = ResourceEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("OpenWithoutWith" in t.target_name for t in result.targets)


def test_mutable_default_warns(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo(x=[]):\n    return x\n", encoding="utf-8")
    result = ResourceEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("MutableDefault" in t.target_name for t in result.targets)
