"""The ici-next configuration schema (#203 PR A).

Two kinds of test here, and the split matters.

The fixture tests run every file in ``tests/fixtures/config`` through the
reader. Each invalid fixture states, in its own header, the problem it exists to
provoke — so a fixture that starts failing for a *different* reason fails the
test rather than quietly continuing to look like it works. That failure mode is
what this work package is about: WP05 exists because the current loader can
change a quality gate with nothing recording that it did.

The rest are unit tests of the rules SPEC-01 states in prose, written so that
the prose and the assertion can be read side by side.
"""

from __future__ import annotations

import re
from pathlib import Path
from pathlib import PurePosixPath as Pure

import pytest

from ici.config.documents import ComponentBody, ComponentReference
from ici.config.errors import ConfigProblem, NextConfigError
from ici.config.origin import Origin, Sourced
from ici.config.paths import DeclaredPath, Executable, SourceGlob, substitute_environment
from ici.config.schema import SCHEMA_VERSION, read_component, read_root

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "config"
DESIGN = Path(__file__).resolve().parents[1] / "docs" / "design" / "ici-next"

_KIND = re.compile(r"^# kind: (root|component)$", re.MULTILINE)
_EXPECT = re.compile(r"^# expect: (.+)$", re.MULTILINE)


def _read(text: str, *, kind: str, path: str):
    return read_root(text, path=path) if kind == "root" else read_component(text, path=path)


def _kind_of(text: str, path: Path) -> str:
    match = _KIND.search(text)
    assert match, f"{path.name} must declare '# kind: root' or '# kind: component'"
    return match.group(1)


def _fixtures(folder: str) -> list[Path]:
    paths = sorted((FIXTURES / folder).glob("*.toml"))
    assert paths, f"no fixtures in {folder}"
    return paths


class TestValidFixturesAreAccepted:
    @pytest.mark.parametrize("path", _fixtures("valid"), ids=lambda p: p.stem)
    def test_it_reads(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        document = _read(text, kind=_kind_of(text, path), path=path.name)
        assert document.schema_version.value == SCHEMA_VERSION

    def test_no_valid_fixture_declares_an_expectation(self) -> None:
        # A valid fixture with an "expect" line is one that was moved and not
        # re-read; it would silently stop testing anything.
        for path in _fixtures("valid"):
            assert not _EXPECT.search(path.read_text(encoding="utf-8")), path.name


class TestInvalidFixturesAreRejectedForTheStatedReason:
    @pytest.mark.parametrize("path", _fixtures("invalid"), ids=lambda p: p.stem)
    def test_it_reports_what_it_says_it_will(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        expected = _EXPECT.search(text)
        assert expected, f"{path.name} must declare '# expect: <substring of the problem>'"
        wanted = expected.group(1)

        with pytest.raises(NextConfigError) as raised:
            _read(text, kind=_kind_of(text, path), path=path.name)

        reported = [str(problem) for problem in raised.value.problems]
        assert any(wanted in line for line in reported), (
            f"{path.name} expected a problem containing {wanted!r}, got: {reported}"
        )

    @pytest.mark.parametrize("path", _fixtures("invalid"), ids=lambda p: p.stem)
    def test_every_problem_names_a_file(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        with pytest.raises(NextConfigError) as raised:
            _read(text, kind=_kind_of(text, path), path=path.name)
        for problem in raised.value.problems:
            assert problem.origin.file == path.name


class TestEveryValueKnowsWhereItCameFrom:
    """SPEC-01 section 3: the source file and key of every final value."""

    def test_a_nested_value_reports_its_full_key(self) -> None:
        document = read_root(
            "schema_version = 1\n[workspace]\n"
            '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n',
            path="root.toml",
        )
        component = document.inline[0]
        assert component.root is not None
        assert str(component.root.origin) == "root.toml: components[0].root"

    def test_a_list_entry_reports_its_index(self) -> None:
        document = read_root(
            "schema_version = 1\n[workspace]\n"
            '[[components]]\nid = "a"\nroot = "."\nlanguages = ["python"]\n'
            'sources = ["a/**/*.py", "b/**/*.py"]\n',
            path="root.toml",
        )
        assert str(document.inline[0].sources[1].origin) == "root.toml: components[0].sources[1]"

    def test_a_top_level_key_is_not_prefixed(self) -> None:
        # The message is something a user greps their file for.
        document = read_root('schema_version = 1\n[workspace]\nname = "p"\n', path="root.toml")
        assert str(document.schema_version.origin) == "root.toml: schema_version"


class TestPathsAndGlobsAreDifferentTypes:
    """SPEC-01 section 3 splits them, and splitting them is the point.

    A declared path is anchored to the file that declared it, a source glob to
    the component root. Were they one type, moving a component into its own
    file would change which files its globs matched.
    """

    def test_a_declared_path_anchors_to_its_own_file(self) -> None:
        origin = Origin(file="python/tool-b/ici.toml", key="python.executable")
        resolved = DeclaredPath(".venv/bin/python", origin).resolve(
            declaring_directory=Pure("/w/python/tool-b"), environment={}, problems=[]
        )
        assert resolved == Pure("/w/python/tool-b/.venv/bin/python")

    def test_a_source_glob_anchors_to_the_component_root(self) -> None:
        origin = Origin(file="root.toml", key="components[0].sources[0]")
        assert SourceGlob("**/*.py", origin).resolve(component_root=Pure("libs/core")) == (
            "libs/core/**/*.py"
        )

    def test_the_same_glob_declared_in_two_files_means_one_thing(self) -> None:
        # The property SPEC-01 section 2 asks for: file layout changes storage,
        # not meaning.
        in_root = SourceGlob("**/*.py", Origin(file="root.toml", key="a"))
        in_child = SourceGlob("**/*.py", Origin(file="libs/core/ici.toml", key="b"))
        root = Pure("libs/core")
        assert in_root.resolve(component_root=root) == in_child.resolve(component_root=root)

    def test_resolving_never_consults_the_working_directory(self) -> None:
        # Anchors are arguments, so there is nowhere for cwd to enter. The test
        # states it because SPEC-01 section 3 requires the guarantee, and a
        # later os.getcwd() would be easy to add and invisible without this.
        path = DeclaredPath("build/out", Origin(file="root.toml", key="builds.n.directory"))
        first = path.resolve(declaring_directory=Pure("/a"), environment={}, problems=[])
        second = path.resolve(declaring_directory=Pure("/a"), environment={}, problems=[])
        assert first == second == Pure("/a/build/out")

    def test_a_relative_anchor_is_refused(self) -> None:
        path = DeclaredPath("build", Origin(file="root.toml", key="k"))
        with pytest.raises(ValueError, match="absolute"):
            path.resolve(declaring_directory=Pure("relative"), environment={}, problems=[])

    def test_dot_segments_collapse_without_touching_the_disk(self) -> None:
        path = DeclaredPath("../shared/./tools", Origin(file="a/ici.toml", key="k"))
        assert path.resolve(declaring_directory=Pure("/w/a"), environment={}, problems=[]) == Pure(
            "/w/shared/tools"
        )


class TestAnExecutableIsEitherALookupOrAPath:
    """SPEC-01 section 3 splits on the separator, not on what exists."""

    @pytest.mark.parametrize("raw", ["python", "qmake", "python3.13"])
    def test_a_bare_name_is_a_path_lookup(self, raw: str) -> None:
        assert Executable(raw, Origin(file="f", key="k")).searches_path

    @pytest.mark.parametrize("raw", ["./python", ".venv/bin/python", "/usr/bin/python3"])
    def test_anything_with_a_separator_is_a_path(self, raw: str) -> None:
        assert not Executable(raw, Origin(file="f", key="k")).searches_path

    def test_a_lookup_refuses_to_pretend_it_is_a_path(self) -> None:
        with pytest.raises(ValueError, match="PATH lookup"):
            Executable("python", Origin(file="f", key="k")).as_declared_path()


class TestEnvironmentSubstitution:
    """SPEC-01 section 3: ``${env:NAME}`` only, and a missing name is reported."""

    def test_it_substitutes_a_declared_variable(self) -> None:
        problems: list[ConfigProblem] = []
        result = substitute_environment(
            "${env:TOOLS}/bin/qmake",
            origin=Origin(file="f", key="k"),
            environment={"TOOLS": "/opt/qt"},
            problems=problems,
        )
        assert result == "/opt/qt/bin/qmake"
        assert problems == []

    def test_a_missing_variable_is_a_problem_not_an_empty_string(self) -> None:
        # Substituting nothing would turn "${env:X}/bin" into "/bin", which is a
        # real path on most machines — the worst possible failure.
        problems: list[ConfigProblem] = []
        result = substitute_environment(
            "${env:MISSING}/bin",
            origin=Origin(file="f", key="k"),
            environment={},
            problems=problems,
        )
        assert result == "${env:MISSING}/bin"
        assert len(problems) == 1
        assert "MISSING" in problems[0].message

    @pytest.mark.parametrize("raw", ["$HOME/bin", "${HOME}/bin", "$(pwd)/bin", "`pwd`/bin"])
    def test_shell_forms_are_left_alone(self, raw: str) -> None:
        problems: list[ConfigProblem] = []
        assert (
            substitute_environment(
                raw, origin=Origin(file="f", key="k"), environment={"HOME": "/h"}, problems=problems
            )
            == raw
        )
        assert problems == []


class TestOneMistakeDoesNotHideTheNext:
    def test_every_problem_in_a_file_is_reported_at_once(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            read_root(
                "schema_version = 1\n[workspace]\nnaem = 1\nprofil = 2\n"
                '[[components]]\nid = "a"\nroot = "."\nlanguages = ["python"]\n',
                path="root.toml",
            )
        reported = " ".join(str(problem) for problem in raised.value.problems)
        assert "naem" in reported and "profil" in reported

    def test_a_close_key_gets_a_suggestion(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            read_root('schema_version = 1\n[workspace]\nporfile = "standard"\n', path="root.toml")
        assert any(problem.hint == "did you mean profile?" for problem in raised.value.problems)


class TestReferencesAndDefinitionsStaySeparate:
    def test_a_reference_entry_is_not_a_definition(self) -> None:
        document = read_root(
            "schema_version = 1\n[workspace]\n"
            '[[components]]\nid = "tool-b"\nconfig = "python/tool-b/ici.toml"\n',
            path="root.toml",
        )
        entry = document.components[0]
        assert isinstance(entry, ComponentReference)
        assert document.inline == ()
        assert document.references == (entry,)

    def test_a_definition_is_not_a_reference(self) -> None:
        document = read_root(
            "schema_version = 1\n[workspace]\n"
            '[[components]]\nid = "a"\nroot = "."\nlanguages = ["python"]\n',
            path="root.toml",
        )
        assert isinstance(document.components[0], ComponentBody)
        assert document.references == ()


class TestTheSpecExamplesPassTheRealSchema:
    """SPEC-01's completion criterion, which until now read "parses as TOML".

    The examples are the contract's own illustration of itself. If the schema
    and the document disagree, one of them is wrong, and a reader has no way to
    tell which — so the document's examples go through the same reader a user's
    file does.
    """

    @staticmethod
    def _examples() -> list[str]:
        text = (DESIGN / "spec-01-workspace-config-cli.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\n(.*?)```", text, flags=re.DOTALL)
        assert len(blocks) >= 2, "SPEC-01 should keep the root and child config examples"
        return blocks

    def test_the_root_example_reads(self) -> None:
        document = read_root(self._examples()[0], path="spec-01 root example")
        assert [entry.id.value for entry in document.references] == ["tool-b"]
        assert {body.id.value for body in document.inline if body.id} == {"gui", "core", "tool-a"}

    def test_the_child_example_reads(self) -> None:
        document = read_component(self._examples()[1], path="spec-01 child example")
        assert document.component.languages is not None
        assert document.component.languages.value == ("python",)

    def test_the_child_example_does_not_name_itself(self) -> None:
        # SPEC-01 section 2: a component file is registered by a root, not
        # promoted to a workspace of its own.
        document = read_component(self._examples()[1], path="spec-01 child example")
        assert document.component.id is None


class TestSourcedValues:
    def test_a_value_and_its_origin_travel_together(self) -> None:
        origin = Origin(file="root.toml", key="workspace.name")
        assert Sourced(value="product", origin=origin).replace("other").origin == origin

    def test_an_origin_needs_a_file(self) -> None:
        with pytest.raises(ValueError, match="file"):
            Origin(file="", key="k")
