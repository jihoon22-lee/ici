"""Tests for the CMake/qmake build adapter."""

from ici.core.cmake import (
    BACKEND_CMAKE,
    BACKEND_QMAKE,
    cmake_build_argv,
    cmake_configure_argv,
    cmake_test_argv,
    parse_cmake_version,
    parse_ctest_junit,
    parse_ctest_stdout,
    parse_qtest_xunit,
    plan_gcov,
    qmake_build_argv,
    qmake_configure_argv,
    qmake_test_argv,
    select_backend,
    shadow_dir,
)


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
