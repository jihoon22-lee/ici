"""#207 PR C — the legacy ``ProjectModel`` view as a projection of a workspace.

The projection exists for compatibility readers — engines and report writers
that still take ``context.project``. What it must never do is re-derive the
answer from a root: ``project_type`` comes from component languages, source
lists from the inventory, and fields the workspace cannot honestly fill stay
empty rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.config.composition import compose
from ici.config.schema import read_root
from ici.workspace import build, project_context
from ici.workspace.inventory import take

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'


def _workspace(text: str):
    return build(compose(read_root(text, path="ici.toml"), {}))


def test_a_python_workspace_projects_a_python_project(tmp_path: Path) -> None:
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tool" / "lib.py").write_text("other = 2\n", encoding="utf-8")
    workspace = _workspace(
        HEADER + '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
    )

    model = project_context(workspace, take(workspace, root=tmp_path), root=tmp_path)

    assert model.project_type == "python"
    assert model.name == "product"
    assert model.python_sources == ("tool/app.py", "tool/lib.py")
    assert model.cpp_sources == ()
    assert model.backend is None


def test_a_mixed_workspace_projects_hybrid(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.cpp").write_text("int main() {}\n", encoding="utf-8")
    (tmp_path / "app" / "tool.py").write_text("value = 1\n", encoding="utf-8")
    workspace = _workspace(
        HEADER + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp", "python"]\n'
    )

    model = project_context(workspace, take(workspace, root=tmp_path), root=tmp_path)

    assert model.project_type == "hybrid"
    assert model.python_sources == ("app/tool.py",)
    assert model.cpp_sources == ("app/main.cpp",)


def test_a_subset_projection_sees_only_the_selected_scope(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "two.cpp").write_text("int two() { return 2; }\n", encoding="utf-8")
    workspace = _workspace(
        HEADER + '[[components]]\nid = "a"\nroot = "a"\nlanguages = ["python"]\n'
        '[[components]]\nid = "b"\nroot = "b"\nlanguages = ["cpp"]\n'
    )
    stock = take(workspace, root=tmp_path)

    model = project_context(workspace, stock, root=tmp_path, components=("a",))

    assert model.project_type == "python"
    assert model.python_sources == ("a/one.py",)
    assert model.cpp_sources == ()


def test_an_unknown_component_is_not_projectable(tmp_path: Path) -> None:
    workspace = _workspace(
        HEADER + '[[components]]\nid = "a"\nroot = "a"\nlanguages = ["python"]\n'
    )

    with pytest.raises(ValueError, match="unregistered"):
        project_context(workspace, root=tmp_path, components=("ghost",))


def test_the_one_referenced_build_becomes_the_backend(tmp_path: Path) -> None:
    workspace = _workspace(
        HEADER + '[builds.native]\nsystem = "qmake"\nproject = "product.pro"\n'
        'directory = "build/ici"\nprepare = "explicit"\n'
        '[[components]]\nid = "gui"\nroot = "apps/gui"\n'
        'languages = ["cpp"]\nbuild = "native"\n'
    )

    model = project_context(workspace, root=tmp_path)

    assert model.backend == "qmake"
    assert model.backend_descriptor == "product.pro"
    assert "builds.native" in model.backend_reason


def test_several_builds_have_no_single_legacy_answer(tmp_path: Path) -> None:
    workspace = _workspace(
        HEADER + '[builds.a]\nsystem = "qmake"\ndirectory = "build/a"\n'
        '[builds.b]\nsystem = "cmake"\ndirectory = "build/b"\n'
        '[[components]]\nid = "x"\nroot = "x"\nlanguages = ["cpp"]\nbuild = "a"\n'
        '[[components]]\nid = "y"\nroot = "y"\nlanguages = ["cpp"]\nbuild = "b"\n'
    )

    model = project_context(workspace, root=tmp_path)

    assert model.backend is None
    assert "several build units" in model.backend_reason


def test_a_user_prepared_build_drives_no_backend(tmp_path: Path) -> None:
    workspace = _workspace(
        HEADER + '[builds.native]\nsystem = "explicit"\ndirectory = "build/ici"\n'
        '[[components]]\nid = "gui"\nroot = "apps/gui"\n'
        'languages = ["cpp"]\nbuild = "native"\n'
    )

    model = project_context(workspace, root=tmp_path)

    assert model.backend is None
    assert "explicit" in model.backend_reason


def test_without_an_inventory_the_source_lists_stay_empty(tmp_path: Path) -> None:
    workspace = _workspace(
        HEADER + '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
    )

    model = project_context(workspace, root=tmp_path)

    assert model.python_sources == ()
    assert model.project_type == "python"


def test_generated_inputs_are_not_sources(tmp_path: Path) -> None:
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "app.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "gen.py").write_text("made = True\n", encoding="utf-8")
    workspace = _workspace(
        HEADER + '[builds.native]\nsystem = "qmake"\ndirectory = "build"\n'
        '[[components]]\nid = "tool"\nroot = "."\nlanguages = ["python"]\n'
        'sources = ["**/*.py"]\n'
    )
    stock = take(workspace, root=tmp_path)

    model = project_context(workspace, stock, root=tmp_path)

    assert model.python_sources == ("tool/app.py",)
