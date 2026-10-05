"""WP19 — C++/Qt test suites and gcov evidence from the project's own build.

The promises under test (#217):

- suites come from the build's own declarations — ``add_test`` for cmake,
  ``QT += testlib``/``testcase`` for qmake — and the artifacts the build
  left behind, not from anything ici builds
- a declared suite with no built binary or CTestTestfile is blocked, not a
  quiet zero; a run that produced no verdicts is a parse failure
- the instrumented test run and the coverage read share one execution:
  ``cpp.coverage`` reads ``.gcda`` after ``cpp.test``, never re-runs it
- gcov evidence is verified — reports that are absent, unreadable or
  internally inconsistent are refused
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.adapters.providers.cpptest import CtestProvider, QtestProvider
from ici.domain.workspace import BuildUnit
from ici.execution.process import Outcome, TaskOutcome, TaskSpec
from ici.workspace.test_suites import suites_for_build

runner = CliRunner()

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'
_NO_CPP_TESTS = '[checks."cpp.test"]\nenabled = false\n[checks."cpp.coverage"]\nenabled = false\n'

needs_ctest = pytest.mark.skipif(not shutil.which("ctest"), reason="ctest missing")
needs_gxx = pytest.mark.skipif(
    not (shutil.which("g++") and shutil.which("gcov")), reason="g++/gcov missing"
)


def _cmake_cpp_workspace(root: Path, *, tests: bool = True, built: bool = False) -> None:
    (root / "app").mkdir()
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    add_test = "add_test(NAME unit COMMAND unit_test)\n" if tests else ""
    (root / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(p)\n"
        "enable_testing()\nadd_executable(app main.cpp)\n" + add_test,
        encoding="utf-8",
    )
    if built:
        build = root / "build"
        build.mkdir()
        (build / "CTestTestfile.cmake").write_text(
            'add_test(unit "./unit_test")\n', encoding="utf-8"
        )
        (build / "unit_test").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (build / "unit_test").chmod(
            (build / "unit_test").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
        )
    (root / "ici.toml").write_text(
        HEADER + '[builds.release]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build"\nvariant = "release"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
        'build = "release"\n',
        encoding="utf-8",
    )


def _qmake_cpp_workspace(root: Path, *, built: bool = False) -> None:
    (root / "app").mkdir()
    (root / "app" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_unit.pro").write_text(
        "QT += testlib\nCONFIG += testcase\nSOURCES += test_unit.cpp\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_unit.cpp").write_text("// test\n", encoding="utf-8")
    (root / "project.pro").write_text("TEMPLATE = subdirs\nSUBDIRS = app tests\n", encoding="utf-8")
    if built:
        build = root / "build"
        build.mkdir()
        binary = build / "test_unit"
        binary.write_text(
            "#!/bin/sh\n"
            'echo "PASS   : TestUnit::ok()"\n'
            "echo \"FAIL!  : TestUnit::bad() 'false' returned FALSE\"\n"
            'echo "   Loc: [tests/test_unit.cpp(3)]"\n'
            'echo "Totals: 1 passed, 1 failed, 0 skipped, 0 blacklisted"\n'
            "exit 1\n",
            encoding="utf-8",
        )
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    (root / "ici.toml").write_text(
        HEADER + '[builds.release]\nsystem = "qmake"\nproject = "project.pro"\n'
        'directory = "build"\nvariant = "release"\n'
        '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\n'
        'build = "release"\n',
        encoding="utf-8",
    )


# --- suite discovery ------------------------------------------------------


def test_cmake_add_test_declares_a_ctest_suite(tmp_path) -> None:
    _cmake_cpp_workspace(tmp_path)
    build = BuildUnit(
        id="release",
        system="cmake",
        variant="release",
        directory="build",
        definition="CMakeLists.txt",
    )
    suites = suites_for_build(tmp_path, build)
    assert [s.kind for s in suites] == ["ctest"]
    assert suites[0].build_dir == "build"


def test_qmake_testlib_pro_declares_a_qtest_suite(tmp_path) -> None:
    _qmake_cpp_workspace(tmp_path)
    build = BuildUnit(
        id="release",
        system="qmake",
        variant="release",
        directory="build",
        definition="project.pro",
    )
    suites = suites_for_build(tmp_path, build)
    assert [s.kind for s in suites] == ["qtest"]
    assert suites[0].binary == "test_unit"


def test_a_make_build_declares_no_discoverable_suite(tmp_path) -> None:
    build = BuildUnit(id="native", system="make", variant="debug", directory="build")
    assert suites_for_build(tmp_path, build) == ()


# --- plan-level gating ----------------------------------------------------


def test_an_unbuilt_ctest_suite_is_blocked_not_empty(tmp_path, monkeypatch) -> None:
    _cmake_cpp_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan"])
    assert plan.exit_code == 0, plan.output
    assert "test suite not built" in plan.output


def test_a_build_without_suites_blocks_the_test_check(tmp_path, monkeypatch) -> None:
    _cmake_cpp_workspace(tmp_path, tests=False)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan"])
    assert "no declared test suite" in plan.output


def test_an_unbuilt_qtest_binary_is_blocked(tmp_path, monkeypatch) -> None:
    _qmake_cpp_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)

    plan = runner.invoke(app, ["next", "plan"])
    assert "test binary not built" in plan.output


# --- provider parsing -----------------------------------------------------


def _outcome(name: str, stdout: str, exit_code: int = 0) -> TaskOutcome:
    spec = TaskSpec(name=name, argv=(name,), cwd=Path.cwd())
    return TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=exit_code, stdout=stdout)


def test_ctest_verdicts_become_findings() -> None:
    output = (
        "    Test #1: alpha .........................   Passed    0.01 sec\n"
        "    Test #2: beta ..........................***Failed    0.02 sec\n"
        "50% tests passed, 1 tests failed out of 2\n"
    )
    parsed = CtestProvider().parse(_outcome("app.cpp.test.release-ctest", output, 8))
    assert parsed.findings and parsed.findings[0].rule_id == "ctest.failed"
    assert parsed.measurements[0].numerator == 1
    assert parsed.measurements[0].denominator == 2


def test_ctest_zero_tests_is_never_a_pass() -> None:
    parsed = CtestProvider().parse(_outcome("t", "No tests were found!!!\n", 0))
    assert parsed.failed_to_parse is not None


def test_qtest_verdicts_keep_the_loc_location() -> None:
    output = (
        "********* Start testing of TestUnit *********\n"
        "PASS   : TestUnit::ok()\n"
        "FAIL!  : TestUnit::bad() 'false' returned FALSE.\n"
        "   Loc: [tests/test_unit.cpp(3)]\n"
        "Totals: 1 passed, 1 failed, 0 skipped, 0 blacklisted, 0ms\n"
    )
    parsed = QtestProvider().parse(_outcome("app.cpp.test.release-tests", output, 1))
    assert parsed.findings
    finding = parsed.findings[0]
    assert finding.rule_id == "qtest.fail"
    assert finding.primary_location.path == "tests/test_unit.cpp"
    assert finding.primary_location.start_line == 3


# --- real execution -------------------------------------------------------


@needs_ctest
def test_ctest_runs_the_built_suite_and_reports_a_failure(tmp_path, monkeypatch) -> None:
    _cmake_cpp_workspace(tmp_path, built=True)
    build = tmp_path / "build"
    (build / "unit_test").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--cpp"])
    assert result.exit_code in (1, 3), result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    rules = {f["rule_id"] for f in document["findings"]}
    assert "ctest.failed" in rules


@needs_gxx
def test_instrumented_build_shares_one_run_for_test_and_coverage(tmp_path, monkeypatch) -> None:
    """A real instrumented suite: g++ --coverage, the run writes .gcda, gcov
    reads it — one execution feeding both checks (#219 item 5)."""

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "lib.cpp").write_text(
        "int covered() { return 1; }\nint missed() { return 0; }\n",
        encoding="utf-8",
    )
    (app_dir / "test.cpp").write_text(
        "int covered();\nint main() { return covered() == 1 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(p)\nenable_testing()\n"
        "add_executable(t app/test.cpp app/lib.cpp)\nadd_test(NAME t COMMAND t)\n",
        encoding="utf-8",
    )
    build = tmp_path / "build"
    build.mkdir()
    subprocess.run(
        [
            "cmake",
            "-S",
            str(tmp_path),
            "-B",
            str(build),
            "-DCMAKE_BUILD_TYPE=Debug",
            "-DCMAKE_CXX_FLAGS=--coverage",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(["cmake", "--build", str(build)], check=True, capture_output=True)
    assert list(build.rglob("*.gcno")), "instrumented build left no .gcno"
    (tmp_path / "ici.toml").write_text(
        HEADER + '[builds.cov]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
        'directory = "build"\nvariant = "coverage"\n'
        '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
        'build = "cov"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["next", "verify", "--cpp"])
    assert result.exit_code == 0, result.output
    document = json.loads((tmp_path / ".ici" / "next" / "result.json").read_text())
    names = {m["name"] for m in document["metrics"]}
    assert "coverage.cpp.lines" in names
    line_cov = next(m for m in document["metrics"] if m["name"] == "coverage.cpp.lines")
    assert 0.0 < line_cov["value"] <= 100.0
