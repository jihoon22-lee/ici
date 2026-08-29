"""Tests for the CMake/qmake build adapter."""

from ici.core.cmake import (
    BACKEND_CMAKE,
    BACKEND_QMAKE,
    select_backend,
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
