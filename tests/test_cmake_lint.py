"""Tests for cmake_lint engine."""

from pathlib import Path

from ici.core.models import EngineStatus
from ici.engines.cmake_lint import CMakeLintEngine


def test_cmake_lint_no_file_is_pass(tmp_path: Path):
    engine = CMakeLintEngine(
        tmp_path,
        {"engines": {"cmake_lint": {"enabled": True, "mode": "pass_warn", "required": False}}},
    )
    result = engine.run()
    assert result.status == EngineStatus.PASS
    assert "No CMakeLists.txt" in result.summary


def test_cmake_lint_valid_file_pass(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\nproject(demo)\nadd_executable(demo main.cpp)\nset(CMAKE_CXX_STANDARD 17)\nset(CMAKE_EXPORT_COMPILE_COMMANDS ON)\n",
        encoding="utf-8",
    )
    engine = CMakeLintEngine(
        tmp_path,
        {
            "engines": {
                "cmake_lint": {"mode": "pass_warn", "required": False, "min_version": "3.16"}
            }
        },
    )
    result = engine.run()
    assert result.status == EngineStatus.PASS
    assert result.engine_name == "cmake_lint"


def test_cmake_lint_missing_min_required_warn(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text("project(demo)\n", encoding="utf-8")
    engine = CMakeLintEngine(
        tmp_path, {"engines": {"cmake_lint": {"mode": "pass_warn", "required": False}}}
    )
    result = engine.run()
    assert result.status == EngineStatus.WARN
    assert any("MinVersion" in t.target_name for t in result.targets)


def test_cmake_lint_low_version_warn(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\nproject(demo)\n", encoding="utf-8"
    )
    engine = CMakeLintEngine(
        tmp_path,
        {
            "engines": {
                "cmake_lint": {"mode": "pass_warn", "required": False, "min_version": "3.16"}
            }
        },
    )
    result = engine.run()
    assert result.status == EngineStatus.WARN
    assert any("3.10" in t.message for t in result.targets)


def test_cmake_lint_missing_project_warn(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.20)\n", encoding="utf-8"
    )
    engine = CMakeLintEngine(
        tmp_path, {"engines": {"cmake_lint": {"mode": "pass_warn", "required": False}}}
    )
    result = engine.run()
    assert result.status == EngineStatus.WARN
    assert any("Project" in t.target_name for t in result.targets)


def test_cmake_lint_add_subdirectory_escape_warn(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        'cmake_minimum_required(VERSION 3.20)\nproject(demo)\nadd_subdirectory("../outside")\n',
        encoding="utf-8",
    )
    engine = CMakeLintEngine(
        tmp_path, {"engines": {"cmake_lint": {"mode": "pass_warn", "required": False}}}
    )
    result = engine.run()
    assert result.status == EngineStatus.WARN
    assert any("AddSubdirectory" in t.target_name for t in result.targets)


def test_cmake_lint_respects_min_version_config(tmp_path: Path):
    (tmp_path / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.18)\nproject(demo)\n", encoding="utf-8"
    )
    # With min 3.16, should pass (no warn on version)
    engine = CMakeLintEngine(
        tmp_path,
        {
            "engines": {
                "cmake_lint": {"mode": "pass_warn", "required": False, "min_version": "3.16"}
            }
        },
    )
    result = engine.run()
    # Should not have MinVersion warn, but may have other warns (e.g., missing CXX_STANDARD not triggered without add_executable)
    # So check version warn not present
    assert not any("MinVersion" in t.target_name and "3.18" in t.message for t in result.targets)
