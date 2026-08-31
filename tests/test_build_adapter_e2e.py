"""End-to-end adapter runs against the fixture projects.

Two layers exist on purpose. Argv construction and output parsing are pure and
tested without tools in test_build_adapter.py. These tests need cmake/qmake/Qt
and are skipped when those are missing, because ici supports RHEL 7.9 where they
may not be.

The skip must not be silent. ici shipped a green gate for several releases while
lint had never actually run in CI (C-6). ICI_REQUIRE_BUILD_ADAPTERS=1 turns a
missing tool into a failure, and ici's own CI sets it.
"""

import os
import shutil
from pathlib import Path

import pytest

from ici.core.cmake import ConfigureOptions, build, collect_coverage, configure, run_tests
from ici.core.context import BuildVariant, discover_project_model
from ici.core.qmake_context import prepare_qmake_compilation_context

FIXTURES = Path(__file__).resolve().parents[1] / "examples" / "cpp-fixtures"


def _require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if not missing:
        return
    message = f"build adapter tools unavailable: {', '.join(missing)}"
    if os.environ.get("ICI_REQUIRE_BUILD_ADAPTERS") == "1":
        pytest.fail(message)
    pytest.skip(message)


def _require_qmake() -> None:
    """Accept either the Qt 6 qmake name or the Qt 5 fallback name."""

    if shutil.which("qmake6") is None and shutil.which("qmake") is None:
        _require("qmake6", "qmake")


def _copy(fixture: str, tmp_path: Path) -> Path:
    target = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, target)
    return target


def test_cmake_fixture_builds_and_tests_a_q_object(tmp_path):
    _require("cmake", "ctest", "gcov")
    root = _copy("cmake_project", tmp_path)

    session = configure(root, ConfigureOptions(BuildVariant.COVERAGE))
    assert session.configured, session.errors
    assert (session.shadow / "compile_commands.json").is_file()
    assert build(session), session.errors

    results = run_tests(session)
    # A Q_OBJECT class links only when moc ran. Before the adapter this failed
    # with "undefined reference to vtable".
    assert results, "no tests were reported"
    assert all(r.passed for r in results), [r.message for r in results if not r.passed]
    assert [r.name for r in results] == ["test_counter"]

    gcov_dir = collect_coverage(session)
    assert gcov_dir is not None, session.errors
    assert list(gcov_dir.glob("*.gcov")), "gcov produced no output"


def test_qmake_fixture_builds_and_tests_a_q_object(tmp_path):
    _require("qmake6", "make", "gcov")
    root = _copy("qmake_project", tmp_path)

    session = configure(root, ConfigureOptions(BuildVariant.COVERAGE))
    assert session.configured, session.errors
    assert build(session), session.errors

    results = run_tests(session)
    assert results, "no tests were reported"
    assert all(r.passed for r in results), [r.message for r in results if not r.passed]
    # Per binary, matching CTest. qmake runs Qt-linked tests through
    # target_wrapper.sh, and reading only the start of each transcript line
    # dropped exactly those. A fixture with a single Qt test hid that behind the
    # XML fallback until a real mixed project surfaced it.
    assert [r.name for r in results] == ["test_counter"]


def test_qmake_fixture_captures_compilation_context(tmp_path):
    _require_qmake()
    _require("make")
    root = _copy("qmake_project", tmp_path)
    project = discover_project_model(root, {})

    context = prepare_qmake_compilation_context(root, {}, project)

    database = root / "build" / "ici-qmake-build" / "compile_commands.json"
    shadow = root / "build" / "ici-qmake-build"
    assert database.is_file()
    assert shadow.is_dir()
    assert context.database_path == "build/ici-qmake-build/compile_commands.json"
    assert context.origin == "qmake"
    assert context.generator == "qmake"
    assert not context.diagnostics

    sources = {unit.source for unit in context.units}
    assert set(project.compilable_cpp_sources) <= sources
    assert {"src/counter.cpp", "tests/test_counter.cpp"} <= sources
    assert "build/ici-qmake-build/src/moc_counter.cpp" in sources

    for unit in context.units:
        for value in (unit.source, unit.directory, unit.output):
            if not value:
                continue
            path = Path(value)
            assert not path.is_absolute()
            assert ".." not in path.parts
            assert path.as_posix() == value
        assert (root / unit.source).is_file()
        assert (root / unit.directory).is_dir()
        if unit.output:
            assert (root / unit.output).is_file()
        assert all("compiler-wrapper" not in argument for argument in unit.argv)
