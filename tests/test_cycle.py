"""Tests for cycle engine (cyclic dependency detection)."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.cycle import CycleEngine, _find_cycles_tarjan

_CFG = {"engines": {"cycle": {"mode": "pass_warn_fail", "max_reported": 20}}}


def test_no_cycle_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("import src.a\n", encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS
    assert result.extra["total_cycles"] == 0


def test_python_import_cycle_warns(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    # a imports b, b imports a -> cycle
    (src / "a.py").write_text("from b import y\n", encoding="utf-8")
    (src / "b.py").write_text("from a import x\ny = 2\n", encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    assert result.status in (EngineStatus.WARN, EngineStatus.FAIL)
    assert any("Cycle" in t.target_name for t in result.targets)


def test_three_module_cycle_detected(tmp_path: Path):
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("from pkg.b import f\n", encoding="utf-8")
    (pkg / "b.py").write_text("from pkg.c import g\n", encoding="utf-8")
    (pkg / "c.py").write_text("from pkg.a import h\n", encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    assert result.status in (EngineStatus.WARN, EngineStatus.FAIL)
    three_cycles = [t for t in result.targets if "3" in t.target_name]
    assert three_cycles


def test_cpp_include_cycle(tmp_path: Path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "a.h").write_text('#pragma once\n#include "b.h"\n', encoding="utf-8")
    (inc / "b.h").write_text('#pragma once\n#include "a.h"\n', encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    assert result.status in (EngineStatus.WARN, EngineStatus.FAIL)
    assert any("CppCycle" in t.target_name for t in result.targets)


def test_tarjan_finds_simple_cycle():
    graph = {1: {2}, 2: {1}, 3: set()}
    cycles = _find_cycles_tarjan(graph)
    assert len(cycles) == 1
    assert sorted(cycles[0]) == [1, 2]


def test_tarjan_acyclic_returns_empty():
    graph = {1: {2}, 2: set(), 3: {1}}
    assert _find_cycles_tarjan(graph) == []
