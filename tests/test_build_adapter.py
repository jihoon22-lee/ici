"""Tests for the CMake/qmake build adapter."""

import ici.core.cmake as cmake_mod
from ici.core.cmake import (
    BACKEND_CMAKE,
    BACKEND_QMAKE,
    BuildSession,
    ConfigureOptions,
    cmake_build_argv,
    cmake_configure_argv,
    cmake_test_argv,
    collect_coverage,
    configure,
    parse_cmake_version,
    parse_ctest_junit,
    parse_ctest_stdout,
    parse_make_check_stdout,
    parse_qtest_xunit,
    plan_gcov,
    qmake_build_argv,
    qmake_configure_argv,
    qmake_test_argv,
    run_tests,
    select_backend,
    shadow_dir,
)
from ici.core.runner import ProcessResult
from ici.engines.coverage_support import parse_gcov_dir


def test_root_cmakelists_selects_cmake(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind == BACKEND_CMAKE
    assert choice.descriptor == "CMakeLists.txt"
    assert "CMakeLists.txt" in choice.reason


def test_root_pro_file_selects_qmake(tmp_path):
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind == BACKEND_QMAKE
    assert choice.descriptor == "app.pro"


def test_cmake_wins_when_both_present(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "app.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind == BACKEND_CMAKE
    # The reason must say the other candidate was seen and passed over, or the
    # report cannot explain why qmake did not run.
    assert "app.pro" in choice.reason


def test_makefile_only_selects_nothing(tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind is None
    assert "Makefile" in choice.reason


def test_no_descriptor_selects_nothing(tmp_path):
    choice = select_backend(tmp_path)
    assert choice.kind is None
    assert choice.descriptor == ""


def test_subdirectory_descriptor_is_ignored(tmp_path):
    gui = tmp_path / "src" / "gui"
    gui.mkdir(parents=True)
    (gui / "CMakeLists.txt").write_text("project(gui)\n", encoding="utf-8")
    choice = select_backend(tmp_path)
    assert choice.kind is None


def test_symlinked_descriptor_is_ignored(tmp_path):
    real = tmp_path / "elsewhere.txt"
    real.write_text("project(x)\n", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").symlink_to(real)
    choice = select_backend(tmp_path)
    assert choice.kind is None


def test_shadow_dir_is_under_build(tmp_path):
    assert shadow_dir(tmp_path, BACKEND_CMAKE) == tmp_path / "build" / "ici-cmake"
    assert shadow_dir(tmp_path, BACKEND_QMAKE) == tmp_path / "build" / "ici-qmake"


def test_parse_cmake_version():
    assert parse_cmake_version("cmake version 3.22.1\n\nCMake suite...") == (3, 22)
    assert parse_cmake_version("cmake version 4.2.3") == (4, 2)
    assert parse_cmake_version("nonsense") is None


def test_cmake_configure_injects_coverage(tmp_path):
    argv = cmake_configure_argv("/usr/bin/cmake", tmp_path, tmp_path / "build/ici-cmake")
    assert argv[0] == "/usr/bin/cmake"
    assert "-S" in argv and "-B" in argv
    # Debug gives -O0 -g. Optimised builds smear gcov's line and branch mapping,
    # which is what the TEM score stands on.
    assert "-DCMAKE_BUILD_TYPE=Debug" in argv
    assert "-DCMAKE_CXX_FLAGS=--coverage" in argv
    assert "-DCMAKE_EXE_LINKER_FLAGS=--coverage" in argv


def test_cmake_build_is_parallel(tmp_path):
    argv = cmake_build_argv("/usr/bin/cmake", tmp_path / "build/ici-cmake")
    assert argv == ["/usr/bin/cmake", "--build", str(tmp_path / "build/ici-cmake"), "--parallel"]


def test_ctest_uses_junit_on_new_cmake(tmp_path):
    shadow = tmp_path / "build/ici-cmake"
    argv, junit = cmake_test_argv("/usr/bin/ctest", shadow, (3, 21))
    assert "--test-dir" in argv
    assert "--output-junit" in argv
    assert junit == shadow / "ici-ctest.xml"


def test_ctest_drops_junit_on_cmake_320(tmp_path):
    shadow = tmp_path / "build/ici-cmake"
    argv, junit = cmake_test_argv("/usr/bin/ctest", shadow, (3, 20))
    assert "--test-dir" in argv
    assert "--output-junit" not in argv
    assert junit is None


def test_ctest_drops_test_dir_on_old_cmake(tmp_path):
    shadow = tmp_path / "build/ici-cmake"
    argv, junit = cmake_test_argv("/usr/bin/ctest", shadow, (3, 19))
    assert "--test-dir" not in argv
    assert junit is None


def test_ctest_unknown_version_is_most_conservative(tmp_path):
    argv, junit = cmake_test_argv("/usr/bin/ctest", tmp_path / "s", None)
    assert "--test-dir" not in argv
    assert junit is None


def test_qmake_configure_injects_coverage(tmp_path):
    pro = tmp_path / "app.pro"
    argv = qmake_configure_argv("/usr/bin/qmake6", pro)
    assert argv[0] == "/usr/bin/qmake6"
    assert str(pro) in argv
    # qmake uses its own flag variables; CMAKE_CXX_FLAGS has no effect here.
    assert "QMAKE_CXXFLAGS+=--coverage" in argv
    assert "QMAKE_LFLAGS+=--coverage" in argv


def test_qmake_build_is_parallel():
    assert qmake_build_argv("/usr/bin/make", 4) == ["/usr/bin/make", "--jobs=4"]


def test_qmake_build_rejects_bad_jobs():
    # A zero or negative job count would make GNU make spawn unbounded jobs.
    assert qmake_build_argv("/usr/bin/make", 0) == ["/usr/bin/make", "--jobs=1"]
    assert qmake_build_argv("/usr/bin/make", -3) == ["/usr/bin/make", "--jobs=1"]


def test_qmake_test_requests_xunit_xml():
    argv = qmake_test_argv("/usr/bin/make")
    assert argv[:2] == ["/usr/bin/make", "check"]
    # CONFIG += testcase forwards TESTARGS to each QtTest binary.
    assert "TESTARGS=-xunitxml" in argv


_CTEST_JUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="ctest" tests="3">
  <testcase name="test_ring_buffer" classname="ctest" time="0.01"/>
  <testcase name="test_log_model" classname="ctest" time="0.02">
    <failure message="row count mismatch">expected 3 got 2</failure>
  </testcase>
  <testcase name="test_skipped" classname="ctest" status="notrun"/>
</testsuite>
"""


def test_parse_ctest_junit():
    results = parse_ctest_junit(_CTEST_JUNIT)
    assert [r.name for r in results] == ["test_ring_buffer", "test_log_model", "test_skipped"]
    assert results[0].passed is True
    assert results[1].passed is False
    assert "row count mismatch" in results[1].message
    # A test that never ran is not a passing test.
    assert results[2].passed is False


def test_parse_ctest_junit_rejects_malformed_xml():
    assert parse_ctest_junit("<testsuite><testcase") == []


_CTEST_STDOUT = """    Start 1: test_ring_buffer
1/2 Test #1: test_ring_buffer .................   Passed    0.01 sec
    Start 2: test_log_model
2/2 Test #2: test_log_model ...................***Failed    0.02 sec
"""


def test_parse_ctest_stdout():
    results = parse_ctest_stdout(_CTEST_STDOUT)
    assert [r.name for r in results] == ["test_ring_buffer", "test_log_model"]
    assert results[0].passed is True
    assert results[1].passed is False
    assert "Failed" in results[1].message


_QTEST_XUNIT = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite errors="0" failures="0" tests="2" name="TestScanner">
  <testcase result="pass" name="initTestCase"/>
  <testcase result="pass" name="scanCountsFiles"/>
</testsuite>
<?xml version="1.0" encoding="UTF-8"?>
<testsuite errors="0" failures="1" tests="1" name="TestTreemapWidget">
  <testcase result="fail" name="clickSelectsNode">
    <failure result="fail" message="no signal emitted"/>
  </testcase>
</testsuite>
"""


def test_parse_qtest_xunit_reads_concatenated_suites():
    # TEMPLATE = subdirs runs several test binaries; their XML documents are
    # concatenated on one stream, so a single ElementTree.fromstring fails.
    results = parse_qtest_xunit(_QTEST_XUNIT)
    names = [r.name for r in results]
    assert names == [
        "TestScanner::initTestCase",
        "TestScanner::scanCountsFiles",
        "TestTreemapWidget::clickSelectsNode",
    ]
    assert results[0].passed is True
    assert results[2].passed is False
    assert "no signal emitted" in results[2].message


def test_parse_qtest_xunit_on_empty_output():
    assert parse_qtest_xunit("") == []


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_plan_gcov_groups_by_object_directory(tmp_path):
    shadow = tmp_path / "build" / "ici-cmake"
    _touch(shadow / "CMakeFiles" / "core.dir" / "a.cpp.gcno")
    _touch(shadow / "CMakeFiles" / "core.dir" / "b.cpp.gcno")
    _touch(shadow / "CMakeFiles" / "gui.dir" / "c.cpp.gcno")

    out_dir, argvs = plan_gcov(shadow, "/usr/bin/gcov")

    assert out_dir == shadow / "ici-gcov"
    assert len(argvs) == 2
    for argv in argvs:
        assert argv[0] == "/usr/bin/gcov"
        # -b gives branch counts; -p keeps the source path in the .gcov filename
        # so two objects with the same basename do not overwrite each other.
        assert "-b" in argv and "-p" in argv
        assert argv[argv.index("-o") + 1] in (
            str(shadow / "CMakeFiles" / "core.dir"),
            str(shadow / "CMakeFiles" / "gui.dir"),
        )
    core_argv = next(a for a in argvs if "core.dir" in a[a.index("-o") + 1])
    assert len([x for x in core_argv if x.endswith(".gcno")]) == 2


def test_plan_gcov_skips_its_own_output_directory(tmp_path):
    shadow = tmp_path / "build" / "ici-cmake"
    _touch(shadow / "CMakeFiles" / "core.dir" / "a.cpp.gcno")
    _touch(shadow / "ici-gcov" / "stale.gcno")

    _out_dir, argvs = plan_gcov(shadow, "/usr/bin/gcov")

    assert len(argvs) == 1
    assert "core.dir" in argvs[0][argvs[0].index("-o") + 1]


def test_plan_gcov_with_no_gcno(tmp_path):
    shadow = tmp_path / "build" / "ici-qmake"
    shadow.mkdir(parents=True)
    out_dir, argvs = plan_gcov(shadow, "/usr/bin/gcov")
    assert out_dir == shadow / "ici-gcov"
    assert argvs == []


def _ok(*_args, **_kwargs):
    return ProcessResult(0, "cmake version 3.28.1", "", 0.01)


def test_configure_records_backend_reason_as_evidence(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cmake_mod, "run_process", _ok)

    session = configure(tmp_path)

    assert session.backend == BACKEND_CMAKE
    assert session.configured is True
    assert session.shadow == tmp_path / "build" / "ici-cmake"
    # Choosing a backend silently would make "why did this build run this way"
    # untraceable from the report alone.
    names = [e.name for e in session.tool_evidence]
    assert any("CMakeLists.txt" in name for name in names)


def test_configure_without_descriptor_has_no_backend(tmp_path):
    session = configure(tmp_path)
    assert session.backend is None
    assert session.configured is False


def test_configure_missing_tool_is_an_error(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda _name: None)

    session = configure(tmp_path)

    assert session.configured is False
    # Not NOT_APPLICABLE: there was something to build and it was not measured.
    assert any("cmake" in err for err in session.errors)


def test_configure_failure_records_stderr(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _fail(cmd, **_kwargs):
        if "--version" in cmd:
            return ProcessResult(0, "cmake version 3.28.1", "", 0.01)
        return ProcessResult(1, "", "CMake Error: bad target", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _fail)
    session = configure(tmp_path)

    assert session.configured is False
    assert any("bad target" in err for err in session.errors)


def test_run_tests_prefers_junit_when_written(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    shadow = tmp_path / "build" / "ici-cmake"
    shadow.mkdir(parents=True)
    (shadow / "ici-ctest.xml").write_text(_CTEST_JUNIT, encoding="utf-8")

    def _run(cmd, **_kwargs):
        if "--version" in cmd:
            return ProcessResult(0, "cmake version 3.28.1", "", 0.01)
        return ProcessResult(0, _CTEST_STDOUT, "", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _run)
    session = configure(tmp_path)
    results = run_tests(session)

    # The JUnit file has three cases; stdout has two. Proving which source was
    # used matters, because only one of them reports the skipped test.
    assert len(results) == 3


def test_collect_coverage_runs_every_group(tmp_path, monkeypatch):
    shadow = tmp_path / "build" / "ici-cmake"
    for name in ("core.dir", "gui.dir"):
        _touch(shadow / "CMakeFiles" / name / "a.cpp.gcno")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[list[str]] = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs.get("cwd") == shadow / "ici-gcov"
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _run)
    session = BuildSession(root=tmp_path, shadow=shadow, backend=BACKEND_CMAKE)

    out_dir = collect_coverage(session)

    assert out_dir == shadow / "ici-gcov"
    assert len(calls) == 2


_XML_BOMB = """<?xml version="1.0"?>
<!DOCTYPE t [
 <!ENTITY a "aaaaaaaaaa">
 <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
 <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
]>
<testsuite name="x"><testcase name="&c;"/></testsuite>"""


def test_ctest_junit_refuses_a_doctype():
    # ElementTree expands internal entities, and the project under verification
    # controls this document: ctest embeds test names from CMakeLists.txt, and
    # `make check` output is whatever the test binaries print. A PR could hand
    # ici a billion-laughs document. No DTD means no custom entities.
    assert parse_ctest_junit(_XML_BOMB) == []


def test_qtest_xunit_refuses_a_doctype():
    assert parse_qtest_xunit(_XML_BOMB) == []


def test_doctype_rejection_does_not_break_ordinary_documents():
    assert len(parse_ctest_junit(_CTEST_JUNIT)) == 3
    assert len(parse_qtest_xunit(_QTEST_XUNIT)) == 3


def test_configure_options_default_to_coverage(tmp_path):
    argv = cmake_configure_argv("/usr/bin/cmake", tmp_path, tmp_path / "s")
    assert "-DCMAKE_CXX_FLAGS=--coverage" in argv


def test_build_wants_no_instrumentation_at_all(tmp_path):
    # Shipping a coverage-instrumented release artifact would be wrong.
    argv = cmake_configure_argv(
        "/usr/bin/cmake", tmp_path, tmp_path / "s", ConfigureOptions(coverage=False)
    )
    assert not any(a.startswith("-DCMAKE_CXX_FLAGS") for a in argv)
    assert not any(a.startswith("-DCMAKE_EXE_LINKER_FLAGS") for a in argv)


def test_sanitize_options_carry_the_sanitizer_and_drop_coverage(tmp_path):
    options = ConfigureOptions(
        coverage=False,
        extra_cxx_flags=("-fsanitize=address,undefined",),
        extra_link_flags=("-fsanitize=address,undefined",),
        shadow_suffix="-asan",
    )
    argv = cmake_configure_argv("/usr/bin/cmake", tmp_path, tmp_path / "s", options)
    assert "-DCMAKE_CXX_FLAGS=-fsanitize=address,undefined" in argv
    assert "-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=address,undefined" in argv
    assert "--coverage" not in " ".join(argv)


def test_qmake_options_use_qmake_flag_variables(tmp_path):
    options = ConfigureOptions(coverage=False, extra_cxx_flags=("-fsanitize=address",))
    argv = qmake_configure_argv("/usr/bin/qmake6", tmp_path / "a.pro", options)
    assert "QMAKE_CXXFLAGS+=-fsanitize=address" in argv
    assert not any("--coverage" in a for a in argv)


def test_shadow_suffix_keeps_engines_out_of_each_others_trees(tmp_path):
    # test builds with --coverage and sanitize with -fsanitize. One shared tree
    # would make each run rebuild the other's objects with the wrong flags.
    assert shadow_dir(tmp_path, BACKEND_CMAKE) != shadow_dir(tmp_path, BACKEND_CMAKE, "-asan")
    assert shadow_dir(tmp_path, BACKEND_CMAKE, "-asan").name == "ici-cmake-asan"


def test_run_tests_passes_the_runner_environment_through(tmp_path, monkeypatch):
    # ASAN_OPTIONS reaches the test binary, not the build. Without this the
    # adapter would run the sanitizers with different settings than g++ did.
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    monkeypatch.setattr(cmake_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen: list[dict | None] = []

    def _run(cmd, **kwargs):
        if "--version" in cmd:
            return ProcessResult(0, "cmake version 3.28.1", "", 0.01)
        seen.append(kwargs.get("env"))
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr(cmake_mod, "run_process", _run)
    session = configure(tmp_path)
    run_tests(session, env={"ASAN_OPTIONS": "detect_leaks=1"})

    assert seen and seen[-1] == {"ASAN_OPTIONS": "detect_leaks=1"}


_MAKE_CHECK_OK = """make[2]: Entering directory '/b/tests'
./test_format -xunitxml
All checks passed
make[2]: Leaving directory '/b/tests'
./test_scanner -xunitxml
All checks passed
"""

_MAKE_CHECK_FAIL = """./test_format -xunitxml
All checks passed
./test_scanner -xunitxml
FAIL scanner.cpp:12
make[2]: *** [Makefile.test_scanner:88: check] Error 1
"""


def test_make_check_transcript_names_the_tests():
    # -xunitxml means nothing to a test that rolls its own main(). Without this
    # fallback such a project reports zero tests, and "no tests ran" over a
    # suite that actually passed is the green-gate failure this repo keeps
    # finding.
    results = parse_make_check_stdout(_MAKE_CHECK_OK, 0)
    assert [r.name for r in results] == ["test_format", "test_scanner"]
    assert all(r.passed for r in results)


def test_make_check_attributes_a_failure_to_its_test():
    results = parse_make_check_stdout(_MAKE_CHECK_FAIL, 2)
    assert [r.name for r in results] == ["test_format", "test_scanner"]
    assert results[0].passed is True
    assert results[1].passed is False


def test_make_check_blames_the_last_started_test_on_an_unattributed_failure():
    # make stops at the first failure, so anything after it never ran.
    results = parse_make_check_stdout(_MAKE_CHECK_OK, 2)
    assert results[-1].passed is False
    assert "non-zero" in results[-1].message


def test_make_check_with_no_invocations_reports_nothing():
    assert parse_make_check_stdout("nothing here\n", 0) == []


_MAKE_CHECK_MIXED = """./test_format -xunitxml
All checks passed
./test_scanner -xunitxml
All checks passed
./test_widget -xunitxml
<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="TestWidget" tests="2" failures="1">
  <testcase result="pass" name="opens"/>
  <testcase result="fail" name="clicks">
    <failure result="fail" message="no signal emitted"/>
  </testcase>
</testsuite>
make[2]: *** [Makefile.test_widget:88: check] Error 2
"""


def test_mixed_qmake_output_does_not_drop_the_plain_tests():
    # A real qmake project mixes QtTest binaries with tests that roll their own
    # main() and ignore -xunitxml. Preferring the XML would report only the
    # QtTest binary and silently drop the other two — a green gate over tests
    # nobody looked at.
    results = cmake_mod._qmake_results(_MAKE_CHECK_MIXED, 2)
    assert [r.name for r in results] == ["test_format", "test_scanner", "test_widget"]
    assert results[0].passed is True
    assert results[1].passed is True
    assert results[2].passed is False
    # QtTest's per-function detail survives on the binary that failed.
    assert "clicks" in results[2].message
    assert "no signal emitted" in results[2].message


def test_pure_qtest_output_still_reports_per_binary():
    results = cmake_mod._qmake_results(_MAKE_CHECK_OK, 0)
    assert [r.name for r in results] == ["test_format", "test_scanner"]


_MAKE_CHECK_WRAPPED = """make[2]: Entering directory '/b/tests'
./test_format -xunitxml
All checks passed
( test -e Makefile.test_widget || /usr/bin/qmake6 -o Makefile.test_widget /p/test_widget.pro ) && make -f Makefile.test_widget check
/b/tests/target_wrapper.sh  ./test_widget -xunitxml
Totals: 3 passed, 0 failed
"""


def test_wrapped_qt_test_invocations_are_still_counted():
    # qmake runs Qt-linked binaries through target_wrapper.sh so they find their
    # libraries. Anchoring the match at the start of the line loses exactly the
    # Qt tests this adapter exists to run — diskmap reported 5 of its 6.
    results = parse_make_check_stdout(_MAKE_CHECK_WRAPPED, 0)
    assert [r.name for r in results] == ["test_format", "test_widget"]
    assert all(r.passed for r in results)


def test_make_recursion_guard_is_not_mistaken_for_a_test():
    # The "( test -e Makefile.x || qmake -o ... )" line mentions paths but runs
    # no test, and make's own chatter must not become a result either.
    results = parse_make_check_stdout(_MAKE_CHECK_WRAPPED, 0)
    assert not any("Makefile" in r.name for r in results)
    assert not any(r.name.startswith("make") for r in results)


def _write_gcov(path, source_line: str) -> None:
    path.write_text(
        f"        -:    0:Source:{source_line}\n"
        "        1:    1:int twice(int a) {\n"
        "        1:    2:  return a * 2;\n"
        "        -:    3:}\n",
        encoding="utf-8",
    )


def test_gcov_relative_source_header_is_resolved(tmp_path):
    # qmake compiles from inside the shadow tree, so gcov records
    # "../../../src/format.cpp" and mangles ".." to "^" in the filename. Without
    # reading the header, every qmake project loses all of its line coverage and
    # the engine degrades to ESTIMATED — correct, but on a broken measurement.
    cov = tmp_path / "ici-gcov"
    cov.mkdir()
    _write_gcov(cov / "^#^#^#src#format.cpp.gcov", "../../../src/format.cpp")

    rows = parse_gcov_dir(cov, {"src/format.cpp"}, tmp_path)

    assert [r["file"] for r in rows] == ["src/format.cpp"]


def test_gcov_absolute_source_still_resolves(tmp_path):
    cov = tmp_path / "ici-gcov"
    cov.mkdir()
    src = tmp_path / "src" / "format.cpp"
    src.parent.mkdir(parents=True)
    src.write_text("int f() { return 1; }\n", encoding="utf-8")
    _write_gcov(cov / "weird-name.gcov", str(src))

    rows = parse_gcov_dir(cov, {"src/format.cpp"}, tmp_path)

    assert [r["file"] for r in rows] == ["src/format.cpp"]


def test_gcov_for_a_file_outside_the_project_is_ignored(tmp_path):
    # System headers land in the same directory and must not be counted.
    cov = tmp_path / "ici-gcov"
    cov.mkdir()
    _write_gcov(cov / "#usr#include#c++#15#vector.gcov", "/usr/include/c++/15/vector")

    assert parse_gcov_dir(cov, {"src/format.cpp"}, tmp_path) == []
