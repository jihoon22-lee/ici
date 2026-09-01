"""Tests for cognitive engine."""

import ast
from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.cognitive import CognitiveEngine, _cognitive_for_function

_CFG = {
    "engines": {"cognitive": {"mode": "pass_warn_fail", "warn": 15, "fail": 25, "warn_nesting": 4}}
}

_SCOPE_CFG = {
    "engines": {
        "cognitive": {
            "mode": "pass_warn_fail",
            "warn": 1,
            "fail": 100,
            "warn_nesting": 99,
        }
    }
}


def test_simple_function_passes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    result = CognitiveEngine(tmp_path, _CFG).run()
    assert result.status == EngineStatus.PASS


def test_cognitive_excludes_nested_scope_bodies(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "scopes.py").write_text(
        """def outer(x, xs):
    if x:
        def inner(y):
            if y:
                return 1
            return 0
        @class_deco(x if x else 0)
        class Holder(Base(x) if x else Base):
            if x:
                marker = 1
            def method(self, y):
                if y:
                    return 1
                return 0
        chooser = lambda value: value if value and x else 0
        return inner(x)
    return 0
""",
        encoding="utf-8",
    )

    result = CognitiveEngine(tmp_path, _SCOPE_CFG).run()

    assert result.status == EngineStatus.WARN
    assert {target.status for target in result.targets} == {EngineStatus.WARN}
    rows = {
        (target.target_name, target.start_line): (
            target.metrics["cognitive"],
            target.metrics["nesting"],
        )
        for target in result.targets
    }
    assert rows == {
        ("outer", 1): (1, 1),
        ("inner", 3): (1, 1),
        ("method", 11): (1, 1),
    }


def test_cognitive_bounds_async_nested_loop_state():
    tree = ast.parse(
        """async def async_outer(xs):
    async for x in xs:
        async def async_inner(ys):
            async for y in ys:
                if y and y > 0:
                    break
            return 0
        return await async_inner(xs)
    return 0
"""
    )
    nodes = {
        (node.name, node.lineno): node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert _cognitive_for_function(nodes[("async_outer", 1)]) == (1, 1)
    assert _cognitive_for_function(nodes[("async_inner", 3)]) == (7, 2)


def test_cognitive_keeps_definition_expressions_and_comprehensions():
    tree = ast.parse(
        """@deco(flag and ready)
def decorated(value=(a and b)):
    if value:
        return value
    return 0

def outer(x):
    if x:
        def inner(y=(a and b)):
            if y:
                return y
            return 0
        return inner()
    return 0

def comp(xs, ys):
    return [x for x in xs if x and x > 0 for y in ys if y]
"""
    )
    nodes = {
        (node.name, node.lineno): node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert _cognitive_for_function(nodes[("decorated", 2)]) == (3, 1)
    assert _cognitive_for_function(nodes[("outer", 7)]) == (3, 1)
    assert _cognitive_for_function(nodes[("inner", 9)]) == (2, 1)
    assert _cognitive_for_function(nodes[("comp", 16)]) == (3, 0)


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
