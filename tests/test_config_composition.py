"""Composing the layers (#203 PR B).

Three things are being asserted, and they are the acceptance criteria of the
work package rather than a translation of the implementation:

1. **Where a component is written does not change what it means.** A workspace
   should be free to move a component into its own file, and a person doing
   that reorganisation should not have to wonder whether they changed the build.
2. **A component cannot quietly lower a root requirement**, and a local overlay
   cannot change what is checked at all. Both are refusals that name the key and
   the file — a precedence rule that silently wins is indistinguishable from the
   defect this work package exists to end.
3. **A display setting does not move the policy digest.** Two machines with
   different build directories are running the same policy, and a digest that
   disagreed would make every local difference look like a policy difference.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from ici.config.composition import compose, compose_standalone
from ici.config.errors import NextConfigError
from ici.config.layers import Layer
from ici.config.overlay import read_local
from ici.config.schema import read_component, read_root
from ici.domain.enums import ScopeKind

ROOT_INLINE = """
schema_version = 1
[workspace]
name = "product"
[checks.lint]
enabled = true
required = true
[[components]]
id = "tool-b"
root = "python/tool-b"
languages = ["python"]
sources = ["**/*.py"]
[components.python]
executable = ".venv/bin/python"
"""

ROOT_REFERENCE = """
schema_version = 1
[workspace]
name = "product"
[checks.lint]
enabled = true
required = true
[[components]]
id = "tool-b"
config = "python/tool-b/ici.toml"
"""

CHILD = """
schema_version = 1
[component]
root = "."
languages = ["python"]
sources = ["**/*.py"]
[python]
executable = ".venv/bin/python"
"""


def _inline():
    return compose(read_root(ROOT_INLINE, path="root.toml"))


def _split():
    return compose(
        read_root(ROOT_REFERENCE, path="root.toml"),
        {"tool-b": read_component(CHILD, path="python/tool-b/ici.toml")},
    )


class TestWhereAComponentIsWrittenDoesNotChangeWhatItMeans:
    """SPEC-01 section 2: splitting files changes storage, not meaning."""

    def test_both_layouts_produce_the_same_component(self) -> None:
        inline = _inline().component("tool-b")
        split = _split().component("tool-b")
        assert inline is not None and split is not None
        # Compared without origins, which are the one thing that *should*
        # differ: the value came from a different file.
        assert _shape(inline) == _shape(split)

    def test_both_layouts_produce_the_same_policy_digest(self) -> None:
        assert _inline().policy_digest == _split().policy_digest

    def test_the_origin_is_the_one_thing_that_differs(self) -> None:
        inline = _inline().component("tool-b")
        split = _split().component("tool-b")
        assert inline is not None and split is not None
        assert inline.declared_in == "root.toml"
        assert split.declared_in == "python/tool-b/ici.toml"
        assert split.root.origin.file == "python/tool-b/ici.toml"

    def test_a_child_file_is_given_its_id_by_the_root(self) -> None:
        # The child does not name itself, so a component file left on its own
        # cannot become a workspace (SPEC-01 section 2).
        document = read_component(CHILD, path="python/tool-b/ici.toml")
        assert document.component.id is None
        assert _split().component("tool-b") is not None

    def test_a_reference_whose_file_was_not_read_is_refused(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            compose(read_root(ROOT_REFERENCE, path="root.toml"))
        assert "tool-b" in str(raised.value)


def _shape(component) -> tuple:
    return (
        component.id,
        component.root.value,
        component.languages.value,
        component.sources,
        component.python_executable.value if component.python_executable else None,
        tuple((c.id, c.enabled.value, c.required.value) for c in component.checks),
    )


class TestAComponentCannotLowerARootRequirement:
    """SPEC-01 section 3: the root owns ``required``."""

    LOWERING = """
    schema_version = 1
    [workspace]
    [checks.lint]
    required = true
    [[components]]
    id = "gui"
    root = "apps/gui"
    languages = ["cpp"]
    [components.checks.lint]
    required = false
    """

    def test_lowering_is_refused_with_the_key_and_both_files(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            compose(read_root(self.LOWERING, path="root.toml"))
        message = str(raised.value)
        assert "gui cannot make lint optional" in message
        assert "checks.lint.required" in message

    def test_the_refusal_says_how_to_do_it_properly(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            compose(read_root(self.LOWERING, path="root.toml"))
        hints = [problem.hint for problem in raised.value.problems if problem.hint]
        assert any("exemptions.gui" in hint for hint in hints)

    def test_raising_a_requirement_is_allowed(self) -> None:
        # Only lowering is refused. A component that wants to hold itself to a
        # higher standard than the root is not a policy hole.
        config = compose(
            read_root(
                "schema_version = 1\n[workspace]\n[checks.lint]\nrequired = false\n"
                '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
                "[components.checks.lint]\nrequired = true\n",
                path="root.toml",
            )
        )
        component = config.component("gui")
        assert component is not None
        check = component.check("lint")
        assert check is not None
        assert check.required.value is True
        assert check.required.layer is Layer.COMPONENT

    def test_a_component_may_still_adjust_enabled(self) -> None:
        config = compose(
            read_root(
                "schema_version = 1\n[workspace]\n[checks.line]\nenabled = true\n"
                '[[components]]\nid = "gui"\nroot = "apps/gui"\nlanguages = ["cpp"]\n'
                "[components.checks.line]\nenabled = false\n",
                path="root.toml",
            )
        )
        component = config.component("gui")
        assert component is not None
        check = component.check("line")
        assert check is not None
        assert check.enabled.value is False


class TestARootMayGrantANamedExemption:
    """The escape hatch SPEC-01 section 3 describes, with its condition."""

    EXEMPTED = """
    schema_version = 1
    [workspace]
    [checks.lint]
    required = true
    [checks.lint.exemptions.gui]
    reason = "Qt moc output is not lintable; tracked in #123"
    [[components]]
    id = "gui"
    root = "apps/gui"
    languages = ["cpp"]
    [components.checks.lint]
    required = false
    """

    def test_an_exempted_component_may_lower_it(self) -> None:
        component = compose(read_root(self.EXEMPTED, path="root.toml")).component("gui")
        assert component is not None
        check = component.check("lint")
        assert check is not None
        assert check.required.value is False
        assert check.relaxed_by_exemption

    def test_the_reason_is_carried_with_the_relaxation(self) -> None:
        component = compose(read_root(self.EXEMPTED, path="root.toml")).component("gui")
        assert component is not None
        check = component.check("lint")
        assert check is not None and check.exempted_reason is not None
        assert "moc" in check.exempted_reason.value

    def test_an_exemption_without_a_reason_is_refused(self) -> None:
        # A reasonless exemption is the silent relaxation with an extra step.
        with pytest.raises(NextConfigError) as raised:
            read_root(
                "schema_version = 1\n[workspace]\n[checks.lint]\nrequired = true\n"
                "[checks.lint.exemptions.gui]\n"
                '[[components]]\nid = "gui"\nroot = "a"\nlanguages = ["cpp"]\n',
                path="root.toml",
            )
        assert "must say why" in str(raised.value)

    def test_an_exemption_for_one_component_does_not_cover_another(self) -> None:
        with pytest.raises(NextConfigError) as raised:
            compose(
                read_root(
                    self.EXEMPTED.replace('id = "gui"', 'id = "core"'),
                    path="root.toml",
                )
            )
        assert "core cannot make lint optional" in str(raised.value)


class TestALocalOverlayMovesPathsAndNothingElse:
    def test_it_accepts_a_path(self) -> None:
        values = read_local('[builds.native]\ndirectory = "/home/me/build"\n', path="local.toml")
        assert values["builds.native.directory"].value == "/home/me/build"

    @pytest.mark.parametrize(
        "body",
        [
            "[checks.lint]\nrequired = false\n",
            "[checks.line]\nenabled = false\n",
            '[components.gui]\nlanguages = ["python"]\n',
            '[components.gui]\nexclude = ["vendor/**"]\n',
        ],
    )
    def test_it_refuses_anything_that_changes_what_is_checked(self, body: str) -> None:
        with pytest.raises(NextConfigError) as raised:
            read_local(body, path="local.toml")
        assert "may only adjust paths" in str(raised.value)

    def test_a_refused_key_is_named_rather_than_ignored(self) -> None:
        # Ignoring it would leave the author believing it took effect.
        with pytest.raises(NextConfigError) as raised:
            read_local("[checks.lint]\nrequired = false\n", path="local.toml")
        assert any(
            problem.origin.key == "checks.lint.required" for problem in raised.value.problems
        )

    def test_a_wildcard_covers_one_key_and_not_any_depth(self) -> None:
        with pytest.raises(NextConfigError):
            read_local('[builds.a.b]\ndirectory = "x"\n', path="local.toml")

    def test_an_overlay_can_move_an_interpreter(self) -> None:
        config = compose(
            read_root(ROOT_INLINE, path="root.toml"),
            local=read_local(
                '[components.tool-b.python]\nexecutable = "/opt/py/bin/python"\n',
                path="local.toml",
            ),
        )
        component = config.component("tool-b")
        assert component is not None and component.python_executable is not None
        assert component.python_executable.value == "/opt/py/bin/python"
        assert component.python_executable.layer is Layer.LOCAL


class TestThePolicyDigest:
    """SPEC-01 section 3, and its acceptance criterion about display settings."""

    def test_a_local_overlay_does_not_move_it(self) -> None:
        without = compose(read_root(ROOT_INLINE, path="root.toml"))
        with_overlay = compose(
            read_root(ROOT_INLINE, path="root.toml"),
            local=read_local(
                '[components.tool-b.python]\nexecutable = "/opt/py/bin/python"\n',
                path="local.toml",
            ),
        )
        assert without.policy_digest == with_overlay.policy_digest

    def test_a_workspace_name_does_not_move_it(self) -> None:
        renamed = ROOT_INLINE.replace('name = "product"', 'name = "product-renamed"')
        assert (
            compose(read_root(ROOT_INLINE, path="root.toml")).policy_digest
            == compose(read_root(renamed, path="root.toml")).policy_digest
        )

    def test_changing_a_requirement_does_move_it(self) -> None:
        relaxed = ROOT_INLINE.replace("required = true", "required = false")
        assert (
            compose(read_root(ROOT_INLINE, path="root.toml")).policy_digest
            != compose(read_root(relaxed, path="root.toml")).policy_digest
        )

    def test_the_file_a_component_lives_in_does_not_move_it(self) -> None:
        assert _inline().policy_digest == _split().policy_digest


class TestEveryValueStillKnowsWhichLayerWon:
    def test_an_unconfigured_check_reports_the_default_layer(self) -> None:
        config = compose(
            read_root(
                "schema_version = 1\n[workspace]\n[checks.line]\n"
                '[[components]]\nid = "a"\nroot = "."\nlanguages = ["python"]\n',
                path="root.toml",
            )
        )
        assert config.checks[0].required.layer is Layer.DEFAULTS
        assert config.checks[0].required.origin.file == "<built-in defaults>"

    def test_a_root_value_reports_the_root_layer_and_its_key(self) -> None:
        config = compose(read_root(ROOT_INLINE, path="root.toml"))
        required = config.checks[0].required
        assert required.layer is Layer.ROOT
        assert str(required.origin) == "root.toml: checks.lint.required"

    def test_a_decided_value_reads_as_value_origin_and_layer(self) -> None:
        config = compose(read_root(ROOT_INLINE, path="root.toml"))
        rendered = str(config.checks[0].required)
        assert "True" in rendered and "root.toml" in rendered and "root" in rendered


class TestAStandaloneComponentIsNotAWorkspacePass:
    """SPEC-01 section 2: a single component run cannot stand in for its root."""

    def test_it_is_marked_standalone(self) -> None:
        config = compose_standalone(
            read_component(CHILD, path="python/tool-b/ici.toml"), component_id="tool-b"
        )
        assert config.scope_kind is ScopeKind.STANDALONE

    def test_a_composed_workspace_is_not(self) -> None:
        assert _inline().scope_kind is ScopeKind.FULL

    def test_it_still_carries_the_component(self) -> None:
        config = compose_standalone(
            read_component(CHILD, path="python/tool-b/ici.toml"), component_id="tool-b"
        )
        component = config.component("tool-b")
        assert component is not None
        # A standalone component is its own workspace: root "." is its own
        # directory, and globs resolve against that root.
        assert component.root.value == "."
        assert component.sources == ("**/*.py",)


class TestLayerPrecedence:
    def test_the_order_is_read_from_the_declaration(self) -> None:
        for earlier, later in itertools.pairwise(Layer):
            assert later.beats(earlier)
            assert not earlier.beats(later)

    def test_a_layer_beats_itself(self) -> None:
        # Last write wins within one layer, which is what lets a later key in the
        # same file replace an earlier one.
        assert Layer.ROOT.beats(Layer.ROOT)

    def test_replacing_a_decided_value_keeps_its_type(self) -> None:
        config = compose(read_root(ROOT_INLINE, path="root.toml"))
        decided = config.checks[0].required
        assert replace(decided, value=False).origin == decided.origin
