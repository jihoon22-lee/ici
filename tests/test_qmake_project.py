"""#212: qmake ``SUBDIRS`` resolution — read the declared tree, never evaluate it.

The acceptance criterion under test is that the configured root ``.pro`` and
the targets qmake would build are *connected*: a component linking the build
whose root is not reached by the tree is a mislink, and a ``.pro`` that names
a subtarget which does not exist is a diagnosis, not silence.
"""

from __future__ import annotations

import json
from pathlib import Path

from ici.domain.workspace import BuildUnit, Component
from ici.workspace.compile_units import load_compile_inputs
from ici.workspace.qmake_project import resolve_qmake_project


def _workspace(root: Path) -> None:
    for name in ("app", "lib", "tools"):
        (root / name).mkdir(parents=True)
        (root / name / f"{name}.pro").write_text(
            f"TEMPLATE = app\nTARGET = {name}\n", encoding="utf-8"
        )
        (root / name / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")


def test_subdirs_resolve_to_their_project_files(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += lib app\napp.depends = lib\n",
        encoding="utf-8",
    )

    project = resolve_qmake_project(tmp_path, "product.pro")

    by_name = {target.name: target for target in project.targets}
    assert by_name["lib"].project == "lib/lib.pro"
    assert by_name["app"].project == "app/app.pro"
    assert by_name["app"].depends == ("lib",)
    assert set(project.directories) == {".", "lib", "app"}
    assert not project.diagnostics


def test_file_and_subdir_modifiers_override_the_convention(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "renamed.pro").write_text("TEMPLATE = app\n", encoding="utf-8")
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\n"
        "SUBDIRS += core gui\n"
        "core.file = elsewhere/renamed.pro\n"
        "gui.subdir = tools\n",
        encoding="utf-8",
    )

    project = resolve_qmake_project(tmp_path, "product.pro")

    by_name = {target.name: target for target in project.targets}
    assert by_name["core"].project == "elsewhere/renamed.pro"
    assert by_name["gui"].project == "tools/tools.pro"


def test_nested_subdirs_recurse_and_cycles_terminate(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "lib" / "lib.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += ../app\n", encoding="utf-8"
    )
    (tmp_path / "product.pro").write_text("TEMPLATE = subdirs\nSUBDIRS += lib\n", encoding="utf-8")

    project = resolve_qmake_project(tmp_path, "product.pro")

    names = {target.name for target in project.targets}
    assert "lib" in names
    # lib's own SUBDIRS reached ../app through the parent's .pro.
    assert "app" in names or any(t.project == "app/app.pro" for t in project.targets)


def test_a_missing_or_ambiguous_subdir_is_a_diagnostic(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "empty").mkdir()
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += app empty gone\n", encoding="utf-8"
    )

    project = resolve_qmake_project(tmp_path, "product.pro")

    codes = {item.code for item in project.diagnostics}
    assert "qmake-subdir-missing" in codes
    assert {target.name for target in project.targets} == {"app", "empty", "gone"}


def test_variable_references_are_reported_not_evaluated(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += $$MODULE_DIR/plugin\n", encoding="utf-8"
    )

    project = resolve_qmake_project(tmp_path, "product.pro")

    assert not project.targets
    assert any(item.code == "qmake-var-unresolved" for item in project.diagnostics)


def test_conditional_subdirs_are_marked_not_dropped(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += lib\nunix:SUBDIRS += tools\n",
        encoding="utf-8",
    )

    project = resolve_qmake_project(tmp_path, "product.pro")

    by_name = {target.name: target for target in project.targets}
    assert not by_name["lib"].conditional
    assert by_name["tools"].conditional


def _component(root_name: str, build_ids: tuple[str, ...] = ()) -> Component:
    return Component(
        id=root_name,
        root=root_name,
        languages=("cpp",),
        sources=(f"{root_name}/**/*.cpp",),
        build_ids=build_ids,
    )


def _build(directory: str = "build", definition: str | None = None) -> BuildUnit:
    return BuildUnit(
        id="native",
        system="qmake",
        variant="release",
        directory=directory,
        definition=definition,
    )


def _db(root: Path, directory: str, files: list[Path]) -> None:
    build_dir = root / directory
    build_dir.mkdir(parents=True, exist_ok=True)
    build_dir.joinpath("compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build_dir),
                    "file": str(item),
                    "arguments": ["g++", "-c", str(item)],
                }
                for item in files
            ]
        ),
        encoding="utf-8",
    )


def test_a_shared_build_covers_two_components(tmp_path) -> None:
    # #212 acceptance: two C++ components share one build's preparation — the
    # same database answers both components' coverage questions.
    _workspace(tmp_path)
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += app lib\n", encoding="utf-8"
    )
    build = _build(definition="product.pro")
    _db(tmp_path, "build", [tmp_path / "app" / "main.cpp", tmp_path / "lib" / "main.cpp"])

    app_inputs = load_compile_inputs(
        tmp_path, _component("app", ("native",)), ("app/main.cpp",), (build,)
    )
    lib_inputs = load_compile_inputs(
        tmp_path, _component("lib", ("native",)), ("lib/main.cpp",), (build,)
    )

    assert app_inputs.database_path == lib_inputs.database_path == "build/compile_commands.json"
    assert app_inputs.covered == ("app/main.cpp",)
    assert lib_inputs.covered == ("lib/main.cpp",)
    # The other component's TUs are visible as shared-build extras, not hidden.
    assert "lib/main.cpp" in app_inputs.extra
    assert {t.name for t in app_inputs.targets} == {"app", "lib"}


def test_a_component_outside_the_subdirs_tree_is_diagnosed(tmp_path) -> None:
    _workspace(tmp_path)
    (tmp_path / "product.pro").write_text(
        "TEMPLATE = subdirs\nSUBDIRS += app lib\n", encoding="utf-8"
    )
    build = _build(definition="product.pro")
    _db(tmp_path, "build", [tmp_path / "app" / "main.cpp", tmp_path / "lib" / "main.cpp"])

    inputs = load_compile_inputs(
        tmp_path, _component("tools", ("native",)), ("tools/main.cpp",), (build,)
    )

    assert inputs.missing == ("tools/main.cpp",)
    assert any(item.code == "qmake-target-missing" for item in inputs.diagnostics)


def test_generated_inputs_are_named_and_missing_ones_flagged(tmp_path) -> None:
    _workspace(tmp_path)
    build = _build()
    _db(tmp_path, "build", [tmp_path / "app" / "main.cpp"])
    # A moc output the build wrote, and one the database names but is gone.
    (tmp_path / "build" / "moc_panel.cpp").write_text("void moc();\n", encoding="utf-8")
    entries = json.loads((tmp_path / "build" / "compile_commands.json").read_text(encoding="utf-8"))
    entries += [
        {
            "directory": str(tmp_path / "build"),
            "file": str(tmp_path / "build" / "moc_panel.cpp"),
            "arguments": ["g++", "-c", "moc_panel.cpp"],
        },
        {
            "directory": str(tmp_path / "build"),
            "file": str(tmp_path / "build" / "moc_gone.cpp"),
            "arguments": ["g++", "-c", "moc_gone.cpp"],
        },
    ]
    (tmp_path / "build" / "compile_commands.json").write_text(json.dumps(entries), encoding="utf-8")

    inputs = load_compile_inputs(
        tmp_path, _component("app", ("native",)), ("app/main.cpp",), (build,)
    )

    assert inputs.covered == ("app/main.cpp",)
    assert "build/moc_panel.cpp" in inputs.generated
    missing = [d for d in inputs.diagnostics if d.code == "generated-input-missing"]
    assert missing and "moc_gone" in missing[0].message
