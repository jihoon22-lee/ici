"""#213 — the cmake side of build reach.

``resolve_cmake_project`` reads a declared root ``CMakeLists.txt`` textually:
``add_subdirectory`` gives directories, ``add_executable``/``add_library``
give targets, and anything the file makes conditional stays conditional.
Nothing is configured, generated, or written — cmake itself never runs.
"""

from __future__ import annotations

from pathlib import Path

from ici.domain.workspace import BuildUnit, Component
from ici.workspace.cmake_project import resolve_cmake_project
from ici.workspace.compile_units import load_compile_inputs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_add_subdirectory_tree_resolves_targets_and_directories(tmp_path: Path) -> None:
    _write(
        tmp_path / "CMakeLists.txt",
        """cmake_minimum_required(VERSION 3.16)
project(app)
add_subdirectory(src)
add_subdirectory(lib)
""",
    )
    _write(tmp_path / "src" / "CMakeLists.txt", "add_executable(app main.cpp)\n")
    _write(tmp_path / "lib" / "CMakeLists.txt", "add_library(core STATIC core.cpp)\n")

    project = resolve_cmake_project(tmp_path, "CMakeLists.txt")

    assert set(project.directories) == {".", "src", "lib"}
    names = {target.name: target for target in project.targets}
    assert names["app"].project == "src/CMakeLists.txt"
    assert names["app"].directory == "src"
    assert names["core"].project == "lib/CMakeLists.txt"


def test_a_conditional_subdirectory_is_marked_not_promised(tmp_path: Path) -> None:
    _write(
        tmp_path / "CMakeLists.txt",
        """project(app)
add_subdirectory(src)
if(BUILD_TOOLS)
    add_subdirectory(tools)
endif()
""",
    )
    _write(tmp_path / "src" / "CMakeLists.txt", "add_executable(app main.cpp)\n")
    _write(tmp_path / "tools" / "CMakeLists.txt", "add_executable(tool tool.cpp)\n")

    project = resolve_cmake_project(tmp_path, "CMakeLists.txt")

    conditional = {t.name: t.conditional for t in project.targets}
    assert conditional["app"] is False
    assert conditional["tool"] is True


def test_a_variable_subdirectory_is_reported_not_guessed(tmp_path: Path) -> None:
    _write(
        tmp_path / "CMakeLists.txt",
        "project(app)\nadd_subdirectory(${MODULE_DIR})\n",
    )

    project = resolve_cmake_project(tmp_path, "CMakeLists.txt")

    assert project.targets == ()
    assert any(d.code == "cmake-subdir-unresolved" for d in project.diagnostics)


def test_an_add_subdirectory_without_a_list_file_is_unresolved(tmp_path: Path) -> None:
    _write(tmp_path / "CMakeLists.txt", "project(app)\nadd_subdirectory(missing)\n")

    project = resolve_cmake_project(tmp_path, "CMakeLists.txt")

    assert any(d.code == "cmake-subdir-unresolved" for d in project.diagnostics)


def test_a_commented_subdirectory_never_claims_reach(tmp_path: Path) -> None:
    _write(
        tmp_path / "CMakeLists.txt",
        "project(app)\n# add_subdirectory(vendored)\n",
    )
    _write(tmp_path / "vendored" / "CMakeLists.txt", "add_library(v v.cpp)\n")

    project = resolve_cmake_project(tmp_path, "CMakeLists.txt")

    assert project.targets == ()
    assert set(project.directories) == {"."}


def test_add_dependencies_become_target_depends(tmp_path: Path) -> None:
    _write(
        tmp_path / "CMakeLists.txt",
        """add_library(core STATIC core.cpp)
add_executable(app main.cpp)
add_dependencies(app core)
""",
    )

    project = resolve_cmake_project(tmp_path, "CMakeLists.txt")

    app = next(t for t in project.targets if t.name == "app")
    assert app.depends == ("core",)


def test_an_unreadable_definition_is_a_diagnostic_not_a_crash(tmp_path: Path) -> None:
    project = resolve_cmake_project(tmp_path, "no/such/CMakeLists.txt")

    assert any(d.code == "cmake-project-missing" for d in project.diagnostics)


def _component(root: str, build_ids: tuple[str, ...] = ()) -> Component:
    return Component(
        id="app",
        root=root,
        languages=("cpp",),
        sources=("**/*.cpp",),
        include=(),
        exclude=(),
        vendor=(),
        test_paths=(),
        build_ids=build_ids,
        needs=(),
        external=(),
    )


def test_a_component_outside_the_cmake_tree_is_a_coverage_gap(tmp_path: Path) -> None:
    _write(tmp_path / "CMakeLists.txt", "project(x)\nadd_subdirectory(src)\n")
    _write(tmp_path / "src" / "CMakeLists.txt", "add_executable(app main.cpp)\n")
    build = BuildUnit(
        id="release",
        system="cmake",
        variant="release",
        directory="build/release",
        definition="CMakeLists.txt",
    )
    component = _component("elsewhere", build_ids=("release",))

    inputs = load_compile_inputs(tmp_path, component, ("elsewhere/x.cpp",), (build,))

    assert any(d.code == "cmake-target-missing" for d in inputs.diagnostics)


def test_a_component_reached_by_the_cmake_tree_gets_no_gap(tmp_path: Path) -> None:
    _write(tmp_path / "CMakeLists.txt", "project(x)\nadd_subdirectory(app)\n")
    _write(tmp_path / "app" / "CMakeLists.txt", "add_executable(app main.cpp)\n")
    build = BuildUnit(
        id="release",
        system="cmake",
        variant="release",
        directory="build/release",
        definition="CMakeLists.txt",
    )
    component = _component("app", build_ids=("release",))

    inputs = load_compile_inputs(tmp_path, component, ("app/main.cpp",), (build,))

    assert not any(d.code == "cmake-target-missing" for d in inputs.diagnostics)


def test_a_make_builds_missing_definition_is_named(tmp_path: Path) -> None:
    build = BuildUnit(
        id="native",
        system="make",
        variant="default",
        directory="out",
        definition="Makefile",
    )
    component = _component("src", build_ids=("native",))

    inputs = load_compile_inputs(tmp_path, component, ("src/x.cpp",), (build,))

    assert any(d.code == "build-definition-missing" for d in inputs.diagnostics)


def test_a_make_build_makes_no_target_claims(tmp_path: Path) -> None:
    _write(tmp_path / "Makefile", "all:\n\ttrue\n")
    build = BuildUnit(
        id="native", system="make", variant="default", directory="out", definition="Makefile"
    )
    component = _component("src", build_ids=("native",))

    inputs = load_compile_inputs(tmp_path, component, ("src/x.cpp",), (build,))

    assert inputs.targets == ()
    assert not any(d.code == "build-definition-missing" for d in inputs.diagnostics)


def test_the_database_origin_names_the_linked_build(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "x.cpp", "int main() {}\n")
    _write(
        tmp_path / "build" / "dbg" / "compile_commands.json",
        f'[{{"directory": "{tmp_path}", "file": "src/x.cpp", '
        '"command": "c++ -c src/x.cpp -o build/dbg/x.o"}]',
    )
    build = BuildUnit(id="dbg", system="cmake", variant="debug", directory="build/dbg")
    component = _component("src", build_ids=("dbg",))

    inputs = load_compile_inputs(tmp_path, component, ("src/x.cpp",), (build,))

    assert inputs.database_path == "build/dbg/compile_commands.json"
    assert "cmake build 'dbg'" in inputs.origin
