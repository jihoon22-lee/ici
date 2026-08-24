"""Tests for cognitive engine."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.cognitive import CognitiveEngine

_CFG = {
    "engines": {"cognitive": {"mode": "pass_warn_fail", "warn": 15, "fail": 25, "warn_nesting": 4}}
}


def test_simple_function_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    result = CognitiveEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_high_cognitive_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    # Nested ifs to increase cognitive
    code = "def foo(x, y, z):\n"
    for i in range(5):
        code += f"    if x > {i}:\n        if y > {i}:\n            x += 1\n"
    (src / "a.py").write_text(code, encoding="utf-8")
    result = CognitiveEngine(tmp_path, _CFG).run()
    assert result.status in (EngineStatus.WARN, EngineStatus.FAIL)
    assert any("Cognitive" in t.message for t in result.targets)


def test_cognitive_respects_thresholds(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def foo():\n    if True:\n        return 1\n", encoding="utf-8")
    cfg = {"engines": {"cognitive": {"mode": "pass_warn_fail", "warn": 100, "fail": 200}}}
    result = CognitiveEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.PASS


def test_default_thresholds_match_shipped_policy(tmp_path: Path):
    # A standalone/partial config (no warn/fail set) must fall back to the
    # same 30/60 policy DEFAULT_CONFIG ships, not a stricter undocumented
    # pair -- a function scoring under 30 should stay clean either way.
    src = tmp_path / "src"
    src.mkdir()
    code = "def foo(x, y, z):\n"
    for i in range(3):
        code += f"    if x > {i}:\n        if y > {i}:\n            x += 1\n"
    (src / "a.py").write_text(code, encoding="utf-8")
    result = CognitiveEngine(tmp_path, {"engines": {"cognitive": {"mode": "pass_warn_fail"}}}).run()
    assert result.status == EngineStatus.PASS
