"""Tests for static_hygiene engine (headers, cycles, dangerous patterns)."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.static_hygiene import (
    StaticHygieneEngine,
    build_include_graph,
    find_include_cycles,
)

_CFG = {"engines": {"static_hygiene": {"mode": "pass_warn", "required": False}}}


def test_clean_project_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    result = StaticHygieneEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_missing_header_guard_warns(tmp_path: Path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "lib.h").write_text("int add(int a, int b);\n", encoding="utf-8")
    result = StaticHygieneEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("HeaderGuard" in t.target_name for t in result.targets)


def test_pragma_once_passes(tmp_path: Path):
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "ok.h").write_text("#pragma once\nint ok();\n", encoding="utf-8")
    result = StaticHygieneEngine(tmp_path, _CFG).run()
    assert not any("HeaderGuard" in t.target_name for t in result.targets)


def test_two_node_cycle_detected(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.h").write_text('#pragma once\n#include "b.h"\n', encoding="utf-8")
    (src / "b.h").write_text('#pragma once\n#include "a.h"\n', encoding="utf-8")
    result = StaticHygieneEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.WARN
    assert any("IncludeCycle" in t.target_name for t in result.targets)
    assert result.extra["cycles"]


def test_acyclic_includes_no_cycle_warning(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text('#include "util.h"\nint main(){}\n', encoding="utf-8")
    (src / "util.h").write_text("#pragma once\ninline int util(){return 1;}\n", encoding="utf-8")
    graph = build_include_graph(tmp_path)
    assert find_include_cycles(graph) == []


def test_eval_and_secret_patterns_warn(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "risky.py").write_text(
        'value = eval(user_input)\nAPI_KEY = "super-secret-key-123"\n', encoding="utf-8"
    )
    result = StaticHygieneEngine(tmp_path, _CFG).run()
    names = {t.target_name for t in result.targets}
    assert any("Security:Eval" in n for n in names)
    assert any("HardcodedSecret" in n for n in names)


def test_disabled_checks_respected(tmp_path: Path):
    cfg = {
        "engines": {
            "static_hygiene": {
                "check_header_guards": False,
                "check_security_patterns": False,
            }
        }
    }
    (tmp_path / "include").mkdir()
    (tmp_path / "include" / "x.h").write_text("int x;\n", encoding="utf-8")
    result = StaticHygieneEngine(tmp_path, cfg).run()
    assert result.status == EngineStatus.PASS
