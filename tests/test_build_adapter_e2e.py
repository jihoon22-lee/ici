"""End-to-end adapter runs against the fixture projects.

Two layers exist on purpose. Argv construction and output parsing are pure and
tested without tools in test_build_adapter.py. These tests need cmake/qmake/Qt
and are skipped when those are missing, because ici supports RHEL 7.9 where they
may not be.

The skip must not be silent. ici shipped a green gate for several releases while
lint had never actually run in CI (C-6). ICI_REQUIRE_BUILD_ADAPTERS=1 turns a
missing tool into a failure, and ici's own CI sets it.

What each fixture needs is read from the register in tests/fixtures/manifest.toml
rather than listed here (#201). The old lists were written by hand and the cmake
one was wrong: it named cmake, ctest and gcov, while cmake_project's CMakeLists
calls find_package(Qt6 REQUIRED). cmake being installed says nothing about Qt6,
so on a machine without Qt6 the guard found nothing missing, the test ran, and
cmake failed during configure — a failure reported where "not run here" was the
truth. The register probes Qt6 by asking cmake, which is the only way to know.
"""

import shutil
from pathlib import Path

from fixture_manifest import require
from ici.core.cmake import ConfigureOptions, build, collect_coverage, configure, run_tests
from ici.core.context import BuildVariant, discover_project_model
from ici.core.models import EngineStatus, EvidenceState
from ici.core.qmake_context import prepare_qmake_compilation_context
from ici.engines.test import TestEngine


def _prepare(fixture_id: str, tmp_path: Path) -> Path:
    """Skip unless the register says this machine can run the fixture, then copy it.

    Copying is unconditional in the sense that it always happens after the
    guard; doing it before would leave a tree behind for a test that never ran.
    """

    entry = require(fixture_id)
    target = tmp_path / entry.path.name
    shutil.copytree(entry.path, target)
    return target


def test_cmake_fixture_builds_and_tests_a_q_object(tmp_path):
    root = _prepare("cpp/cmake_project", tmp_path)

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
    pattern = "*.gcov.json.gz" if session.coverage_format == "gcov-json" else "*.gcov"
    assert list(gcov_dir.glob(pattern)), "gcov produced no output"


def test_cmake_fixture_reports_exact_gcov_values_and_geometry(tmp_path):
    root = _prepare("cpp/cmake_project", tmp_path)

    result = TestEngine(root).run()

    assert result.status == EngineStatus.PASS, result.summary
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["line_coverage"] == 100.0
    assert result.extra["branch_coverage"] == 100.0
    assert result.extra["function_coverage"] == 100.0
    assert result.extra["coverage_source"] == "gcov"
    provenance = result.extra["coverage_provenance"]["cpp"]
    if provenance["format"] == "gcov-json":
        assert provenance["function_geometry"] == "exact"
        assert provenance["source_mapping"] == "recorded-compilation-directory-or-project-root"
        assert provenance["throw_branches_excluded"] is True
        assert provenance["covered_sources"] == provenance["expected_sources"] == 1
        function_targets = [
            target
            for target in result.targets
            if target.target_name.startswith("Coverage:Function:")
        ]
        assert {target.start_line for target in function_targets} == {3, 5, 9}
        assert all(target.start_column and target.end_column for target in function_targets)
    else:
        assert provenance["format"] == "gcov-text"
        assert "limitations" in provenance
    policy = result.extra["coverage_policy"]
    assert policy["metrics"] == {"branch": 100.0, "function": 100.0, "line": 100.0}
    assert policy["files"] == {"src/counter.cpp": 100.0}


def test_qmake_fixture_builds_and_tests_a_q_object(tmp_path):
    root = _prepare("cpp/qmake_project", tmp_path)

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
    root = _prepare("cpp/qmake_project", tmp_path)
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
