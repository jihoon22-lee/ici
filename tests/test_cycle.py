"""Tests for cycle engine (cyclic dependency detection)."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.cycle import CycleEngine, _find_actual_cycle_path, _find_cycles_tarjan

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


def test_tarjan_self_loop_detected():
    graph = {1: {1}}
    cycles = _find_cycles_tarjan(graph)
    assert cycles == [[1]]


def test_tarjan_handles_deep_chain_without_recursion_error():
    # A single long chain used to blow Python's recursion limit in the
    # recursive Tarjan implementation; the iterative version must not.
    n = 5000
    graph = {i: {i + 1} for i in range(n)}
    graph[n] = {0}  # close the chain into one big cycle
    cycles = _find_cycles_tarjan(graph)
    assert len(cycles) == 1
    assert len(cycles[0]) == n + 1


def test_find_actual_cycle_path_follows_real_edges_only():
    # a -> b -> c -> a is a real cycle; d is in the same SCC-adjacent set but
    # has no incoming/outgoing edge that should ever appear in the chain.
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    path = _find_actual_cycle_path("a", {"a", "b", "c"}, graph)
    assert path[0] == "a"
    # Every consecutive pair (including wrap-around) must be a real edge.
    for i in range(len(path)):
        assert path[(i + 1) % len(path)] in graph[path[i]]


def test_python_cycle_message_only_contains_real_edges(tmp_path: Path):
    # Regression: previously the reported chain was an alphabetically sorted
    # SCC member list, which could show edges that were never imported.
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("from b import y\n", encoding="utf-8")
    (src / "b.py").write_text("from a import x\ny = 2\n", encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    target = next(t for t in result.targets if "Cycle" in t.target_name)
    modules = target.metrics["modules"]
    assert set(modules) == {"a", "b"}


def test_stdlib_module_name_collision_is_not_a_false_cycle(tmp_path: Path):
    # A project module literally named "html" (matching this project's own
    # reporters.html package) must not be linked to unrelated modules that
    # merely `import html` from the standard library.
    src = tmp_path / "src"
    pkg = src / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "html.py").write_text("x = 1\n", encoding="utf-8")
    (pkg / "other.py").write_text("import html\n", encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    assert result.extra["py_cycles"] == 0


def test_ambiguous_cpp_header_basename_is_not_wired(tmp_path: Path):
    # Two different "util.h" files exist under different directories; without
    # an -I search-path model there is no correct way to pick one, so no edge
    # should be created rather than silently wiring to the wrong file.
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    (a_dir / "util.h").write_text("#pragma once\n", encoding="utf-8")
    (b_dir / "util.h").write_text("#pragma once\n", encoding="utf-8")
    (a_dir / "main.cpp").write_text('#include "util.h"\n', encoding="utf-8")
    result = CycleEngine(tmp_path, _CFG).run()
    assert result.extra["cpp_cycles"] == 0


def test_required_defaults_to_false(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = CycleEngine(tmp_path, {"engines": {"cycle": {}}}).run()
    assert result.required is False
