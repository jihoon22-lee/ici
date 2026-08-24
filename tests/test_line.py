"""Tests for Line Count Engine and 500/1000 line threshold."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.line import LineCountEngine


def test_line_count_pass(tmp_python_project: Path):
    engine = LineCountEngine(tmp_python_project)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert res.extra["code"] > 0
    assert len(res.targets) > 0


def test_line_count_500_warning(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    big_file = src / "big.py"
    # Write 600 lines of code
    big_file.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.WARN
    target = next(t for t in res.targets if t.file_path == "src/big.py")
    assert target.status == EngineStatus.WARN


def test_line_count_1000_error(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    giant_file = src / "giant.py"
    # Write 1100 lines of code
    giant_file.write_text("\n".join(f"x_{i} = {i}" for i in range(1100)), encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    target = next(t for t in res.targets if t.file_path == "src/giant.py")
    assert target.status == EngineStatus.FAIL


def test_line_excludes_test_and_doc_dirs(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    big_test = tests / "big_test.py"
    big_test.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# guide\n" * 700, encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert not any(t.file_path.startswith(("tests/", "docs/")) for t in res.targets)
    assert res.extra["code"] == 0


def test_line_gate_dirs_configurable(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir(parents=True)
    big_test = tests / "big_test.py"
    big_test.write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")

    engine = LineCountEngine(
        tmp_path,
        config={"engines": {"line": {"include_dirs": ["tests"], "gate_dirs": ["tests"]}}},
    )
    res = engine.run()
    assert res.status == EngineStatus.WARN
    target = next(t for t in res.targets if t.file_path == "tests/big_test.py")
    assert target.status == EngineStatus.WARN


def test_line_exclude_dirs(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "big.py").write_text("\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8")
    engine = LineCountEngine(tmp_path, config={"engines": {"line": {"exclude_dirs": ["src"]}}})
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert not any(t.file_path.startswith("src/") for t in res.targets)


def test_line_all_files_mode(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "big_test.py").write_text(
        "\n".join(f"x_{i} = {i}" for i in range(600)), encoding="utf-8"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# guide\n" * 50, encoding="utf-8")

    engine = LineCountEngine(tmp_path)
    res = engine.run()

    scopes = {f["path"]: f["scope"] for f in res.extra["files_data"]}
    assert scopes["src/app.py"] == "source"
    assert scopes["tests/big_test.py"] == "extra"
    assert scopes["docs/guide.md"] == "extra"
    assert not any(t.file_path.startswith(("tests/", "docs/")) for t in res.targets)
    all_totals = res.extra["all"]
    assert all_totals["files"] == 3
    assert res.extra["code"] < all_totals["code"]


def test_line_respects_configured_project_source_dirs(tmp_path: Path):
    # Regression: a project with a non-default `project.source_dirs` (e.g.
    # ["packages"]) used to be scanned as 0 files by `line`, silently
    # disabling both the file listing and the size gate.
    packages = tmp_path / "packages"
    packages.mkdir(parents=True)
    big_file = packages / "big.py"
    big_file.write_text("\n".join(f"x_{i} = {i}" for i in range(60)), encoding="utf-8")

    engine = LineCountEngine(
        tmp_path,
        config={
            "project": {"source_dirs": ["packages"]},
            "engines": {"line": {"warn_limit": 10, "fail_limit": 20}},
        },
    )
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert len(res.targets) == 1
    target = res.targets[0]
    assert target.file_path == "packages/big.py"
    assert target.status == EngineStatus.FAIL


def test_line_explicit_gate_dirs_override_is_not_expanded(tmp_path: Path):
    # An explicit, narrower `gate_dirs` must be honored as-is even when
    # project.source_dirs names a different directory — auto-union only
    # kicks in when gate_dirs was left at its shipped default.
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "small.py").write_text("x = 1\n", encoding="utf-8")
    packages = tmp_path / "packages"
    packages.mkdir(parents=True)
    (packages / "big.py").write_text("\n".join(f"x_{i} = {i}" for i in range(60)), encoding="utf-8")

    engine = LineCountEngine(
        tmp_path,
        config={
            "project": {"source_dirs": ["packages"]},
            "engines": {
                "line": {"warn_limit": 10, "fail_limit": 20, "gate_dirs": ["src"]},
            },
        },
    )
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert not any(
        t.file_path == "packages/big.py" and t.status != EngineStatus.PASS for t in res.targets
    )
