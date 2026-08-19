"""Tests for non-src project layouts (lib/) and strengthened clone/complexity detection."""

from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.core.project import (
    detect_project_type,
    get_all_cpp_includes,
    get_all_cpp_sources,
    get_all_python_sources,
    get_source_dirs,
)
from ici.engines.complexity import ComplexityEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.test import TestEngine


def make_lib_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "lib" / "mylib"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        """def process_order(items):
    result = []
    for item in items:
        if item.price > 1000:
            result.append(item.apply_discount(0.1))
        else:
            result.append(item)
    total = sum(r.final for r in result)
    return total, len(result)
""",
        encoding="utf-8",
    )
    (pkg / "refund.py").write_text(
        """def refund_order(entries):
    outcome = []
    for entry in entries:
        if entry.cost > 1000:
            outcome.append(entry.apply_rate(0.1))
        else:
            outcome.append(entry)
    amount = sum(o.value for o in outcome)
    return amount, len(outcome)
""",
        encoding="utf-8",
    )
    (pkg / "router.py").write_text(
        """def dispatch(mode, payload):
    match mode:
        case 1 if payload.get("enabled"):
            return payload["items"] if len(payload["items"]) > 0 else []
        case 2 if payload.get("admin"):
            return [x for x in payload["items"] if x.active and x.score > 10]
        case 3:
            return None
    return []
""",
        encoding="utf-8",
    )
    (tmp_path / "ici.toml").write_text(
        'name = "mylib"\ntype = "python"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    return tmp_path


def test_lib_layout_source_discovery(tmp_path: Path):
    root = make_lib_project(tmp_path)
    assert detect_project_type(root) == "python"
    rels = {str(p.relative_to(root)) for p in get_all_python_sources(root)}
    assert rels == {
        "lib/mylib/__init__.py",
        "lib/mylib/core.py",
        "lib/mylib/refund.py",
        "lib/mylib/router.py",
    }


def test_source_dirs_cannot_escape_project(tmp_path: Path):
    config = {"project": {"source_dirs": ["../outside"]}}

    with pytest.raises(ValueError, match="outside project root"):
        get_source_dirs(tmp_path, config)


def test_configured_symlink_source_dir_cannot_escape_project(tmp_path: Path):
    outside = tmp_path.parent / "outside-source"
    outside.mkdir()
    (tmp_path / "linked").symlink_to(outside, target_is_directory=True)
    config = {"project": {"source_dirs": ["linked"]}}

    with pytest.raises(ValueError, match="outside project root"):
        get_source_dirs(tmp_path, config)


def test_default_symlink_source_dir_is_ignored(tmp_path: Path):
    outside = tmp_path.parent / "outside-default-source"
    outside.mkdir()
    (outside / "leak.py").write_text("SECRET = True\n", encoding="utf-8")
    (tmp_path / "src").symlink_to(outside, target_is_directory=True)

    assert get_source_dirs(tmp_path) == []
    assert get_all_python_sources(tmp_path) == []


def test_source_discovery_ignores_symlinked_files_and_directories(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "safe.py").write_text("SAFE = True\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-nested-source"
    outside.mkdir()
    (outside / "leak.py").write_text("SECRET = True\n", encoding="utf-8")
    (source / "leak.py").symlink_to(outside / "leak.py")
    (source / "linked").symlink_to(outside, target_is_directory=True)

    assert [p.relative_to(tmp_path) for p in get_all_python_sources(tmp_path)] == [
        Path("src/safe.py")
    ]


def test_cpp_source_discovery_ignores_symlinked_files_directories_and_loops(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "safe.cpp").write_text("int safe() { return 0; }\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-cpp-source"
    outside.mkdir()
    (outside / "leak.cpp").write_text("int leak() { return 1; }\n", encoding="utf-8")
    (source / "leak.cpp").symlink_to(outside / "leak.cpp")
    (source / "linked").symlink_to(outside, target_is_directory=True)
    loop_a = source / "loop-a"
    loop_b = source / "loop-b"
    loop_a.symlink_to(loop_b, target_is_directory=True)
    loop_b.symlink_to(loop_a, target_is_directory=True)

    assert [p.relative_to(tmp_path) for p in get_all_cpp_sources(tmp_path)] == [
        Path("src/safe.cpp")
    ]


def test_cpp_include_discovery_ignores_symlink_escape_and_loops(tmp_path: Path):
    include = tmp_path / "include"
    safe = include / "safe"
    safe.mkdir(parents=True)
    outside = tmp_path.parent / "outside-cpp-include"
    outside.mkdir()
    escape = include / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    loop_a = include / "loop-a"
    loop_b = include / "loop-b"
    loop_a.symlink_to(loop_b, target_is_directory=True)
    loop_b.symlink_to(loop_a, target_is_directory=True)

    include_dirs = get_all_cpp_includes(tmp_path)

    assert f"-I{include}" in include_dirs
    assert f"-I{safe}" in include_dirs
    assert f"-I{outside}" not in include_dirs
    assert f"-I{escape}" not in include_dirs
    assert not any("loop-a" in path or "loop-b" in path for path in include_dirs)


def test_dup_detects_type2_cross_file_clone_in_lib(tmp_path: Path):
    root = make_lib_project(tmp_path)
    res = DuplicateEngine(root).run()
    assert res.extra["clone_groups_count"] >= 1
    locs = {o["loc"] for g in res.extra["clone_groups"] for o in g["occurrences"]}
    assert "lib/mylib/core.py:1-9" in locs
    assert "lib/mylib/refund.py:1-9" in locs


def test_dup_detects_type2_same_file_clone(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "mod.py").write_text(
        """def process_order(items):
    result = []
    for item in items:
        if item.price > 1000:
            result.append(item.apply_discount(0.1))
        else:
            result.append(item)
    total = sum(r.final for r in result)
    return total, len(result)


def refund_order(entries):
    outcome = []
    for entry in entries:
        if entry.cost > 1000:
            outcome.append(entry.apply_rate(0.1))
        else:
            outcome.append(entry)
    amount = sum(o.value for o in outcome)
    return amount, len(outcome)
""",
        encoding="utf-8",
    )
    res = DuplicateEngine(tmp_path).run()
    assert res.extra["clone_groups_count"] >= 1


def test_dup_detects_clone_with_inserted_gap(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "a.py").write_text(
        """def calc_a(values):
    result = {}
    total = 0
    for v in values:
        total += v.amount
        if v.flag:
            total += v.bonus
    return total
""",
        encoding="utf-8",
    )
    (src / "b.py").write_text(
        """def calc_b(values):
    result = {}
    total = 0
    for v in values:
        total += v.amount
        if v.flag:
            total += v.bonus
        note = v.memo.strip()
    return total
""",
        encoding="utf-8",
    )
    res = DuplicateEngine(tmp_path).run()
    assert res.extra["clone_groups_count"] >= 1
    locs = {o["loc"] for g in res.extra["clone_groups"] for o in g["occurrences"]}
    assert "src/a.py:1-7" in locs
    assert "src/b.py:1-7" in locs


def test_complexity_counts_match_guards_and_comprehension_ifs(tmp_path: Path):
    root = make_lib_project(tmp_path)
    res = ComplexityEngine(root).run()
    router = next(t for t in res.targets if t.target_name == "dispatch()")
    assert router.metrics["complexity"] == 7


def test_test_engine_runs_lib_layout(tmp_path: Path):
    root = make_lib_project(tmp_path)
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        """from mylib.core import process_order

class Item:
    price = 2000
    final = 100
    def apply_discount(self, r):
        return self

def test_process():
    assert process_order([Item()]) == (100, 1)
""",
        encoding="utf-8",
    )
    config = {
        "engines": {
            "test": {
                "mode": "pass_fail",
                # The fixture deliberately exercises only one branch.
                "min_branch_cov": 100.0,
            }
        }
    }
    res = TestEngine(root, config=config).run()
    assert res.extra["passed_tests"] == res.extra["total_tests"] >= 1
    assert res.extra["branch_coverage"] < 100.0
    assert res.status == EngineStatus.FAIL
