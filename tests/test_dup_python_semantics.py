"""Focused contracts for the bounded Python semantic-shape helper."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ici.engines._dup_semantic import (
    SEMANTIC_SHAPE_ALGORITHM,
    SemanticLimits,
    analyze_python_source,
    analyze_python_sources,
)


def _one(source: str, *, path: str = "src/example.py", limits: SemanticLimits | None = None):
    outcome = analyze_python_source(source, file_path=path, limits=limits)
    assert len(outcome.regions) == 1
    return outcome.regions[0]


def test_named_regions_keep_source_names_but_alpha_rename_local_bindings() -> None:
    left = _one(
        """def add_left(value):
    total = value + 1
    total += 2
    del total
"""
    )
    right = _one(
        """def add_right(amount):
    result = amount + 1
    result += 2
    del result
""",
        path="src/other.py",
    )

    assert left.name == "add_left"
    assert right.name == "add_right"
    assert left.canonical_shape == right.canonical_shape
    assert '"ctx",["Load",[]]' in left.canonical_shape
    assert '"ctx",["Store",[]]' in left.canonical_shape
    assert '"ctx",["Del",[]]' in left.canonical_shape
    assert left.fingerprint == right.fingerprint
    assert left.fingerprint_algorithm == SEMANTIC_SHAPE_ALGORITHM


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "def calc(value):\n    if value > 0:\n        return value + 1\n    return 0\n",
            "def calc(value):\n    while value > 0:\n        return value + 1\n    return 0\n",
        ),
        (
            "def calc(value):\n    return value + 1\n",
            "def calc(value):\n    return value - 1\n",
        ),
        (
            "def calc(value):\n    return value > 1\n",
            "def calc(value):\n    return value >= 1\n",
        ),
        (
            "def calc(value):\n    return value + 1\n",
            "def calc(value):\n    return value + 2\n",
        ),
    ],
    ids=["control-flow", "operator", "comparison", "literal-value"],
)
def test_control_flow_operators_comparisons_and_literal_values_are_exact(left: str, right: str):
    assert _one(left).canonical_shape != _one(right).canonical_shape


def test_calls_attributes_imports_and_builtins_are_api_anchors() -> None:
    left = _one(
        """from toolkit import formatter

def render_left(items):
    output = formatter(items)
    return len(output), output.service.render()
"""
    )
    right = _one(
        """from toolkit import formatter

def render_right(values):
    result = formatter(values)
    return len(result), result.service.render()
""",
        path="src/right.py",
    )

    assert left.canonical_shape == right.canonical_shape
    assert '"Call"' in left.canonical_shape
    assert '"Attribute"' in left.canonical_shape
    assert "formatter" in left.canonical_shape
    assert "len" in left.canonical_shape
    assert "service" in left.canonical_shape
    assert "render" in left.canonical_shape


def test_defaults_annotations_and_decorators_keep_outer_scope_names() -> None:
    left = _one(
        """@value
def first(value: value = value) -> value:
    return value
"""
    )
    right = _one(
        """@other
def second(value: other = other) -> other:
    return value
""",
        path="src/right.py",
    )

    assert left.canonical_shape != right.canonical_shape
    assert left.canonical_shape.count('"value"') >= 3
    assert right.canonical_shape.count('"other"') >= 3


def test_local_binding_with_root_name_is_not_misclassified_as_recursion() -> None:
    shadowed = _one(
        """def calculate():
    calculate = 1
    return calculate
"""
    )
    recursive = _one(
        """def calculate():
    return calculate()
""",
        path="src/recursive.py",
    )

    assert "<self-name>" not in shadowed.canonical_shape
    assert "<self-name>" in recursive.canonical_shape


def test_nested_named_scopes_are_pruned_from_parent_and_emitted_separately() -> None:
    outcome = analyze_python_source(
        """def outer(value):
    def inner(first):
        return first + 99
    class Nested:
        def method(self, item):
            return item + 100
    return value + 1
""",
        file_path="src/nested.py",
    )

    assert [region.name for region in outcome.regions] == [
        "outer",
        "outer.inner",
        "outer.Nested",
        "outer.Nested.method",
    ]
    outer = outcome.regions[0]
    assert "inner" not in outer.canonical_shape
    assert "Nested" not in outer.canonical_shape
    assert "99" not in outer.canonical_shape
    assert "100" not in outer.canonical_shape
    assert {region.kind for region in outcome.regions} == {"function", "class", "method"}


@pytest.mark.parametrize(
    ("reason", "source"),
    [
        ("dynamic-eval", "def unsafe(value):\n    return eval(value)\n"),
        ("dynamic-exec", "def unsafe(value):\n    exec(value)\n"),
        ("global-statement", "def unsafe(value):\n    global value\n    return value\n"),
        ("nonlocal-statement", "def outer():\n    def unsafe():\n        nonlocal missing\n"),
        ("star-import", "def unsafe():\n    from package import *\n"),
        ("unsupported-node", "def unsafe(value):\n    return (lambda item: item)(value)\n"),
        ("comprehension-scope", "def unsafe(values):\n    return [item for item in values]\n"),
    ],
)
def test_unsafe_or_unsupported_regions_are_excluded_with_reason(reason: str, source: str) -> None:
    outcome = analyze_python_source(source, file_path="src/unsafe.py")

    if reason == "nonlocal-statement":
        # The enclosing named function remains its own valid region; the
        # nested function carrying the unsafe binding is excluded separately.
        assert [region.name for region in outcome.regions] == ["outer"]
    else:
        assert outcome.regions == ()
    assert len(outcome.exclusions) == 1
    exclusion = outcome.exclusions[0]
    assert exclusion.file_path == "src/unsafe.py"
    assert exclusion.reason == reason
    assert exclusion.region_name in {"unsafe", "outer.unsafe"}


def test_malformed_source_fails_closed_without_partial_regions() -> None:
    outcome = analyze_python_source(
        "def good(value):\n    return value\n\ndef broken(value)\n    return value\n",
        file_path="src/broken.py",
    )

    assert outcome.regions == ()
    assert outcome.status == "excluded"
    assert outcome.exclusions[0].reason == "malformed-ast"


def test_module_level_dynamic_code_is_metadata_while_named_region_stays_scoped() -> None:
    outcome = analyze_python_source(
        """eval('module setup')

def safe(value):
    return value + 1
""",
        file_path="src/module_dynamic.py",
    )

    assert [region.name for region in outcome.regions] == ["safe"]
    assert outcome.exclusions[0].reason == "dynamic-eval"
    assert outcome.exclusions[0].region_name is None


@pytest.mark.parametrize(
    "limits",
    [
        SemanticLimits(max_files=1),
        SemanticLimits(max_regions=1),
        SemanticLimits(max_nodes=3),
        SemanticLimits(max_serialized_chars=10),
    ],
    ids=["files", "regions", "nodes", "serialized-chars"],
)
def test_budget_exhaustion_is_source_level_and_emits_no_partial_regions(
    limits: SemanticLimits,
) -> None:
    sources = {
        "src/a.py": "def first(value):\n    return value + 1\n",
        "src/b.py": "def second(value):\n    return value + 2\n",
    }
    outcome = analyze_python_sources(sources, limits=limits)

    assert outcome.regions == ()
    assert outcome.status == "excluded"
    assert outcome.exclusions
    assert any(
        exclusion.reason in {"max-files", "max-regions", "max-nodes", "max-serialized-chars"}
        for exclusion in outcome.exclusions
    )


def test_outcome_and_regions_are_immutable_and_order_is_deterministic() -> None:
    sources = {
        "src/z.py": "def zeta(value):\n    return value\n",
        "src/a.py": "def alpha(value):\n    return value\n",
    }
    first = analyze_python_sources(sources)
    second = analyze_python_sources(reversed(tuple(sources.items())))

    assert first == second
    assert [region.file_path for region in first.regions] == ["src/a.py", "src/z.py"]
    with pytest.raises(FrozenInstanceError):
        first.regions = ()
    with pytest.raises(FrozenInstanceError):
        first.regions[0].name = "changed"


@pytest.mark.parametrize(
    "field_name", ["max_files", "max_regions", "max_nodes", "max_serialized_chars"]
)
def test_limits_reject_non_positive_or_boolean_values(field_name: str) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        SemanticLimits(**{field_name: 0})
    with pytest.raises(ValueError, match="positive integer"):
        SemanticLimits(**{field_name: True})
