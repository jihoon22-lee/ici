"""End-to-end policy contracts for Python AST-shape duplicate groups."""

from __future__ import annotations

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.dup import DuplicateEngine
from ici.reporters.html.sections.dup import _render_dup_section


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _run(root: Path, *, policy: str = "auto", min_window: int = 2):
    return DuplicateEngine(
        root,
        {
            "project": {"type": "python", "source_dirs": ["src"]},
            "engines": {
                "dup": {
                    "python_semantic": policy,
                    "min_window": min_window,
                    "warn_pct": 0.0,
                    "fail_pct": 100.0,
                    "mode": "pass_warn",
                }
            },
        },
    ).run()


def _semantic_groups(result) -> list[dict]:
    return [
        group
        for group in result.extra.get("clone_groups", [])
        if group.get("detection") == "python-ast-semantic-shape"
    ]


def test_ast_shape_detects_equivalent_callables_across_physical_line_layouts(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "src/left.py",
        """def calculate(value):
    total = (
        value
        + 1
    )
    return total * 2
""",
    )
    _write(
        tmp_path,
        "src/right.py",
        """def compute(amount):
    result = amount + 1
    return result * 2
""",
    )

    result = _run(tmp_path)

    groups = _semantic_groups(result)
    assert len(groups) == 1
    assert groups[0]["fingerprint_algorithm"] == "sha256/semantic-shape-v1"
    assert {item["file_path"] for item in groups[0]["occurrences"]} == {
        "src/left.py",
        "src/right.py",
    }
    assert result.extra["python_semantic_mode"] == "bounded"
    assert result.extra["python_semantic_groups_reported"] == 1
    assert result.status == EngineStatus.WARN
    assert "Python AST shape" in _render_dup_section(result, tmp_path)


def test_ast_shape_preserves_external_api_and_literal_anchors(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/left.py",
        """def fetch(client, key):
    value = client.fetch(key)
    return value + 1
""",
    )
    _write(
        tmp_path,
        "src/right.py",
        """def remove(service, item):
    result = service.remove(item)
    return result + 2
""",
    )

    result = _run(tmp_path)

    assert _semantic_groups(result) == []
    assert result.extra["python_semantic_groups_reported"] == 0


def test_auto_policy_keeps_safe_groups_and_records_unsafe_region_exclusions(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "src/left.py",
        """def left(value):
    total = (
        value
        + 1
    )
    return total * 2
""",
    )
    _write(
        tmp_path,
        "src/right.py",
        """def right(value):
    total = value + 1
    return total * 2
""",
    )
    _write(
        tmp_path,
        "src/dynamic.py",
        """def dynamic(source):
    return eval(source)
""",
    )

    result = _run(tmp_path)

    assert len(_semantic_groups(result)) == 1
    assert result.extra["python_semantic_mode"] == "partial"
    assert result.extra["python_semantic_exclusion_counts"] == {"dynamic-eval": 1}


def test_required_policy_fails_closed_when_a_region_is_excluded(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/dynamic.py",
        """def dynamic(source):
    return eval(source)
""",
    )

    result = _run(tmp_path, policy="required")

    assert result.status == EngineStatus.ERROR
    assert result.evidence.value == "NOT_RUN"
    assert result.extra["python_semantic_mode"] == "unavailable"
    assert result.targets[0].file_path == "src/dynamic.py"
    assert result.targets[0].start_line == 1
    assert "dynamic-eval" in result.targets[0].message


def test_off_policy_leaves_ast_shape_analysis_disabled(tmp_path: Path) -> None:
    source = """def {name}(value):
    total = value + 1
    return total * 2
"""
    _write(tmp_path, "src/left.py", source.format(name="left"))
    _write(tmp_path, "src/right.py", source.format(name="right"))

    result = _run(tmp_path, policy="off")

    assert _semantic_groups(result) == []
    assert result.extra["python_semantic_mode"] == "off"
    assert result.extra["python_semantic_regions_observed"] == 0


def test_parent_callable_spans_are_not_attributed_to_pruned_nested_shapes(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "src/left.py",
        """def outer_left(value):
    def inner_left(item):
        return item + 1
    return value * 2
""",
    )
    _write(
        tmp_path,
        "src/right.py",
        """def outer_right(amount):
    def inner_right(element):
        return element + 9
    return amount * 2
""",
    )

    result = _run(tmp_path)

    assert _semantic_groups(result) == []
    assert result.extra["python_semantic_parent_regions_excluded"] == 2
