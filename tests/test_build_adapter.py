"""Tests for the CMake/qmake build adapter."""

from ici.core.cmake import (
    BACKEND_CMAKE,
    BACKEND_QMAKE,
    cmake_build_argv,
    cmake_configure_argv,
    cmake_test_argv,
    parse_cmake_version,
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
