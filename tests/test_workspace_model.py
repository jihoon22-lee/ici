"""#207 PR A — the workspace model built from composed configuration.

The acceptance criteria this file maps onto:

- single Python / single C++ / multi-language same folder / several
  sub-projects all express in one model;
- one qmake build referenced by two C++ components produces no duplicate
  build unit;
- ``needs`` edges separate artifact dependency from build-unit sharing, and
  cycles, dangling references and ambiguous names are diagnosed;
- a component root or build directory escaping the workspace is reported;
- an inline component and the same component in a child file produce the
  same model (SPEC-01 section 2/3 anchoring).
"""

from __future__ import annotations

import pytest

from ici.config.composition import compose, compose_standalone
from ici.config.errors import NextConfigError
from ici.config.schema import read_component, read_root
from ici.workspace import build, project_type

HEADER = 'schema_version = 1\n[workspace]\nname = "product"\n'


def _config(
    text: str,
    children: dict[str, tuple[str, str]] | None = None,
    *,
    root_path: str = "ici.toml",
):
    root = read_root(text, path=root_path)
    docs = {
        identifier: read_component(child, path=path)
        for identifier, (path, child) in (children or {}).items()
    }
    return compose(root, docs)


def _workspace(
    text: str,
    children: dict[str, tuple[str, str]] | None = None,
    *,
    root_path: str = "ici.toml",
):
    return build(_config(text, children, root_path=root_path))


class TestOneModelCoversTheLayouts:
    def test_a_single_python_component(self) -> None:
        workspace = _workspace(
            HEADER + '[[components]]\nid = "tool"\nroot = "tool"\n'
            'languages = ["python"]\nsources = ["src/**/*.py"]\n'
        )
        component = workspace.component("tool")
        assert component.languages == ("python",)
        assert component.sources == ("tool/src/**/*.py",)
        unit = workspace.analysis_units[0]
        assert (unit.component_id, unit.language) == ("tool", "python")

    def test_a_single_cpp_component_with_a_build(self) -> None:
        workspace = _workspace(
            HEADER + '[builds.native]\nsystem = "qmake"\nproject = "product.pro"\n'
            'directory = "build/ici"\nprepare = "explicit"\n'
            '[[components]]\nid = "gui"\nroot = "apps/gui"\n'
            'languages = ["cpp"]\nbuild = "native"\n'
        )
        unit = workspace.analysis_units[0]
        assert (unit.component_id, unit.language, unit.variant, unit.build_id) == (
            "gui",
            "cpp",
            "default",
            "native",
        )

    def test_two_languages_in_one_folder(self) -> None:
        """A mixed directory is one component, two units — not two folders."""

        workspace = _workspace(
            HEADER + '[[components]]\nid = "mixed"\nroot = "plugins/mixed"\n'
            'languages = ["cpp", "python"]\nsources = ["**/*"]\n'
        )
        (component,) = workspace.components
        assert component.languages == ("cpp", "python")
        units = {unit.language for unit in workspace.analysis_units}
        assert units == {"cpp", "python"}
        assert {unit.component_id for unit in workspace.analysis_units} == {"mixed"}

    def test_several_sub_projects(self) -> None:
        workspace = _workspace(
            HEADER + '[[components]]\nid = "a"\nroot = "libs/a"\nlanguages = ["cpp"]\n'
            '[[components]]\nid = "b"\nroot = "libs/b"\nlanguages = ["cpp"]\n'
            '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
        )
        assert {item.id for item in workspace.components} == {"a", "b", "tool"}
        assert len(workspace.analysis_units) == 3
        assert project_type(workspace) == "hybrid"


class TestSharedBuildUnits:
    TITLES = (
        HEADER + '[builds.native]\nsystem = "qmake"\nproject = "product.pro"\n'
        'directory = "build/ici"\nvariant = "release"\nprepare = "explicit"\n'
        '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
        'build = "native"\n'
        '[[components]]\nid = "core"\nroot = "libs/core"\nlanguages = ["cpp"]\n'
        'build = "native"\n'
    )

    def test_two_components_share_one_build_unit(self) -> None:
        workspace = _workspace(self.TITLES)
        assert len(workspace.builds) == 1
        assert workspace.builds[0].directory == "build/ici"
        assert [item.build_ids for item in workspace.components] == [
            ("native",),
            ("native",),
        ]

    def test_units_keep_the_shared_builds_variant(self) -> None:
        units = {unit.component_id: unit for unit in _workspace(self.TITLES).analysis_units}
        assert units["gui"].variant == "release"
        assert units["core"].variant == "release"

    def test_two_declarations_cannot_claim_one_directory(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                HEADER + '[builds.a]\nsystem = "qmake"\nproject = "a.pro"\n'
                'directory = "build/one"\nprepare = "explicit"\n'
                '[builds.b]\nsystem = "cmake"\nproject = "CMakeLists.txt"\n'
                'directory = "build/one"\nprepare = "explicit"\n'
                '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
            )
        assert "build/one" in str(raised.value)


class TestNeedsEdges:
    BASE = (
        HEADER + '[builds.native]\nsystem = "qmake"\nproject = "p.pro"\n'
        'directory = "build/ici"\nprepare = "explicit"\n'
    )
    CORE = (
        '[[components]]\nid = "core"\nroot = "libs/core"\nlanguages = ["cpp"]\nbuild = "native"\n'
    )

    def test_a_python_component_needing_a_native_artifact(self) -> None:
        workspace = _workspace(
            self.BASE
            + self.CORE
            + '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
            'needs = ["core"]\n'
        )
        assert workspace.component_needs("tool") == ("core",)
        assert workspace.build_needs("tool") == ()

    def test_needs_may_name_the_build_directly(self) -> None:
        workspace = _workspace(
            self.BASE
            + self.CORE
            + '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
            'needs = ["native"]\n'
        )
        # A build-target need is an artifact edge, not a component ordering edge.
        assert workspace.build_needs("tool") == ("native",)
        assert workspace.component_needs("tool") == ()

    def test_a_dangling_need_is_diagnosed(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                self.BASE + '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
                'needs = ["ghost"]\n'
            )
        assert "ghost" in str(raised.value)

    def test_a_cycle_is_diagnosed(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                HEADER + '[[components]]\nid = "a"\nroot = "a"\nlanguages = ["cpp"]\n'
                'needs = ["b"]\n'
                '[[components]]\nid = "b"\nroot = "b"\nlanguages = ["cpp"]\n'
                'needs = ["a"]\n'
            )
        assert "a -> b -> a" in str(raised.value)

    def test_a_need_naming_both_a_component_and_a_build_is_refused(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                HEADER + '[builds.app]\nsystem = "qmake"\nproject = "a.pro"\n'
                'directory = "build/a"\nprepare = "explicit"\n'
                '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\n'
                'build = "app"\n'
                '[[components]]\nid = "tool"\nroot = "tool"\nlanguages = ["python"]\n'
                'needs = ["app"]\n'
            )
        assert "ambiguous" in str(raised.value)


class TestScopeBoundaries:
    def test_a_component_root_escaping_the_workspace_is_reported(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                HEADER + '[[components]]\nid = "evil"\nroot = "../outside"\n'
                'languages = ["python"]\n'
            )
        assert "escapes the workspace root" in str(raised.value)

    def test_a_build_directory_escaping_is_reported(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                HEADER + '[builds.native]\nsystem = "qmake"\nproject = "p.pro"\n'
                'directory = "../build"\nprepare = "explicit"\n'
                '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
            )
        assert "escapes the workspace root" in str(raised.value)

    def test_an_unsupported_prepare_word_is_refused(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            _workspace(
                HEADER + '[builds.native]\nsystem = "qmake"\nproject = "p.pro"\n'
                'directory = "build/ici"\nprepare = "auto"\n'
                '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
            )
        assert "prepare" in str(raised.value)


class TestChildFilesAnchorToTheirOwnDirectory:
    """SPEC-01 section 3: a declared path anchors to the file that wrote it.

    The same component written inline and in a child file must produce the
    same model — but the two spell the root differently, because the child
    anchors to its own directory: ``root = "."`` there is ``python/tool-b``.
    """

    INLINE = (
        HEADER + '[[components]]\nid = "tool-b"\nroot = "python/tool-b"\n'
        'languages = ["python"]\nsources = ["src/**/*.py"]\n'
        '[components.python]\nexecutable = "python/tool-b/.venv/bin/python"\ntest_paths = ["tests"]\n'
    )
    REFERENCE = HEADER + '[[components]]\nid = "tool-b"\nconfig = "python/tool-b/ici.toml"\n'
    CHILD = """
schema_version = 1
[component]
root = "."
languages = ["python"]
sources = ["src/**/*.py"]
[python]
executable = ".venv/bin/python"
test_paths = ["tests"]
"""

    def test_inline_and_child_produce_the_same_model(self) -> None:
        inline = _workspace(self.INLINE)
        split = _workspace(self.REFERENCE, {"tool-b": ("python/tool-b/ici.toml", self.CHILD)})
        (a, b) = inline.component("tool-b"), split.component("tool-b")
        assert a == b
        assert inline.analysis_units == split.analysis_units
        assert inline.policy_digest == split.policy_digest

    def test_a_child_anchored_root_resolves_to_the_childs_directory(self) -> None:
        workspace = _workspace(self.REFERENCE, {"tool-b": ("python/tool-b/ici.toml", self.CHILD)})
        component = workspace.component("tool-b")
        assert component.root == "python/tool-b"
        assert component.test_paths == ("python/tool-b/tests",)
        assert component.sources == ("python/tool-b/src/**/*.py",)

    def test_the_python_unit_carries_the_declared_runtime(self) -> None:
        workspace = _workspace(self.INLINE)
        (unit,) = workspace.analysis_units
        assert unit.runtime == "python/tool-b/.venv/bin/python"
        assert unit.id == "tool-b.python"


class TestChildOnlyKeys:
    """needs/external/vendor in a child file mean what they mean inline."""

    CHILD = """
schema_version = 1
[component]
root = "."
languages = ["cpp"]
sources = ["src/**/*.cpp", "third_party/**/*.cpp"]
vendor = ["third_party/**"]
external = ["/opt/sysroot/include", "libs/public"]
needs = ["core"]
"""

    REFERENCE = (
        HEADER + '[builds.native]\nsystem = "qmake"\nproject = "p.pro"\n'
        'directory = "build/ici"\nprepare = "explicit"\n'
        '[[components]]\nid = "core"\nroot = "libs/core"\nlanguages = ["cpp"]\n'
        'build = "native"\n'
        '[[components]]\nid = "gui"\nconfig = "apps/gui/ici.toml"\n'
    )

    def test_external_inputs_keep_their_role(self) -> None:
        workspace = _workspace(
            self.REFERENCE,
            {"gui": ("/repo/apps/gui/ici.toml", self.CHILD)},
            root_path="/repo/ici.toml",
        )
        gui = workspace.component("gui")
        # Inside the workspace the path normalises; outside it stays absolute.
        assert gui.external == ("/opt/sysroot/include", "apps/gui/libs/public")
        assert gui.vendor == ("apps/gui/third_party/**",)
        assert workspace.component_needs("gui") == ("core",)


def test_standalone_component_builds_a_one_component_workspace() -> None:
    config = compose_standalone(
        read_component(
            'schema_version = 1\n[component]\nroot = "."\nlanguages = ["python"]\n',
            path="tool/ici.toml",
        ),
        component_id="tool",
    )
    workspace = build(config)
    assert workspace.component("tool").root == "."
    assert [unit.language for unit in workspace.analysis_units] == ["python"]
    assert project_type(workspace) == "python"


class TestInterpreterAndPrepare:
    """Review-driven: executables anchor like paths; absent prepare is legal."""

    def test_a_path_executable_anchors_to_its_own_file(self) -> None:
        workspace = _workspace(
            HEADER + '[[components]]\nid = "t"\nconfig = "python/t/ici.toml"\n',
            {
                "t": (
                    "python/t/ici.toml",
                    'schema_version = 1\n[component]\nroot = "."\n'
                    'languages = ["python"]\n[python]\nexecutable = ".venv/bin/python"\n',
                )
            },
        )
        assert workspace.analysis_units[0].runtime == "python/t/.venv/bin/python"

    def test_a_bare_executable_stays_a_path_lookup(self) -> None:
        workspace = _workspace(
            HEADER + '[[components]]\nid = "t"\nroot = "t"\nlanguages = ["python"]\n'
            '[components.python]\nexecutable = "python3"\n'
        )
        assert workspace.analysis_units[0].runtime == "python3"

    def test_a_build_without_prepare_is_usable(self) -> None:
        """No prepare means ici has no approved preparation — not an error."""
        workspace = _workspace(
            HEADER + '[builds.native]\nsystem = "qmake"\nproject = "p.pro"\n'
            'directory = "build/ici"\n'
            '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
            'build = "native"\n'
        )
        assert workspace.builds[0].prepare_argv == ()
