"""Finding the workspace, and refusing to guess which one (#203 PR C).

SPEC-01 section 2 is mostly a list of things discovery must not do, and each
one is a way a run can silently analyse the wrong thing. These tests are that
list, one refusal at a time, plus the filesystem side-effect check #203 asks
for: reading a configuration writes nothing, and ``init`` changes nothing until
it is told to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.config.discovery import discover, load
from ici.config.errors import NextConfigError
from ici.config.scaffold import propose, write
from ici.domain.enums import ScopeKind

ROOT = """
schema_version = 1
[workspace]
name = "product"
[[components]]
id = "gui"
root = "apps/gui"
languages = ["cpp"]
[[components]]
id = "tool-b"
config = "python/tool-b/ici.toml"
"""

CHILD = """
schema_version = 1
[component]
root = "."
languages = ["python"]
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A checkout with a root, a registered child and an unregistered one."""

    repo = tmp_path / "repo"
    (repo / "apps" / "gui").mkdir(parents=True)
    (repo / "python" / "tool-b").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "ici.toml").write_text(ROOT, encoding="utf-8")
    (repo / "python" / "tool-b" / "ici.toml").write_text(CHILD, encoding="utf-8")
    # Registered nowhere. Must not join the build by existing.
    (repo / "apps" / "gui" / "ici.toml").write_text(CHILD, encoding="utf-8")
    return repo


class TestTheSearchFindsTheRootFromAnywhereInside:
    def test_from_the_root_itself(self, workspace: Path) -> None:
        assert discover(workspace).path == workspace / "ici.toml"

    def test_from_a_subdirectory(self, workspace: Path) -> None:
        # SPEC-01 section 6: where you stand does not change the scope.
        assert discover(workspace / "python" / "tool-b").path == workspace / "ici.toml"

    def test_the_scope_is_the_same_from_either_place(self, workspace: Path) -> None:
        from_root = load(workspace)
        from_inside = load(workspace / "apps" / "gui")
        assert [c.id for c in from_root.components] == [c.id for c in from_inside.components]
        assert from_root.policy_digest == from_inside.policy_digest


class TestWhatDiscoveryRefusesToDo:
    def test_a_component_file_is_not_promoted_to_a_workspace(self, workspace: Path) -> None:
        # Promoting it would make "did the workspace pass" depend on which
        # directory someone happened to be standing in.
        found = discover(workspace / "apps" / "gui")
        assert found.path == workspace / "ici.toml"
        assert found.is_workspace

    def test_an_unregistered_child_does_not_join_the_build(self, workspace: Path) -> None:
        config = load(workspace)
        declared = {Path(component.declared_in).name for component in config.components}
        assert declared  # the run found something
        assert not any(
            Path(component.declared_in).parent.name == "gui" for component in config.components
        ), "apps/gui/ici.toml is registered nowhere and must not be read"

    def test_the_search_stops_at_the_checkout_root(self, tmp_path: Path) -> None:
        # A stray file above a project must not capture it.
        (tmp_path / "ici.toml").write_text(ROOT, encoding="utf-8")
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        with pytest.raises(NextConfigError) as raised:
            discover(repo)
        assert "checkout root" in str(raised.value)

    def test_a_nested_workspace_is_not_absorbed(self, workspace: Path) -> None:
        nested = workspace / "vendor" / "other"
        nested.mkdir(parents=True)
        (nested / "ici.toml").write_text(
            'schema_version = 1\n[workspace]\nname = "other"\n', encoding="utf-8"
        )
        # The outer root does not grow a component, and the inner one answers
        # for itself when you stand in it.
        assert len(load(workspace).components) == 2
        assert discover(nested).path == nested / "ici.toml"

    def test_a_registered_file_that_is_missing_is_reported(self, workspace: Path) -> None:
        (workspace / "python" / "tool-b" / "ici.toml").unlink()
        with pytest.raises(NextConfigError) as raised:
            load(workspace)
        assert "tool-b is registered here" in str(raised.value)

    def test_nothing_found_says_what_was_looked_at(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        with pytest.raises(NextConfigError) as raised:
            discover(tmp_path)
        assert "no ici.toml with a [workspace] table" in str(raised.value)
        assert any("ici init" in (p.hint or "") for p in raised.value.problems)


class TestAnExplicitConfigIsTakenAsGiven:
    def test_it_can_name_a_component_file(self, workspace: Path) -> None:
        config = load(workspace, explicit=workspace / "python" / "tool-b" / "ici.toml")
        assert config.scope_kind is ScopeKind.STANDALONE

    def test_a_standalone_run_is_not_a_workspace_pass(self, workspace: Path) -> None:
        # SPEC-01 section 2. The scope kind is how the result says so, rather
        # than a caller having to remember.
        assert load(workspace).scope_kind is ScopeKind.FULL
        assert (
            load(workspace, explicit=workspace / "python" / "tool-b" / "ici.toml").scope_kind
            is ScopeKind.STANDALONE
        )

    def test_a_missing_explicit_file_is_an_error_not_a_search(self, workspace: Path) -> None:
        with pytest.raises(NextConfigError) as raised:
            discover(workspace, explicit=workspace / "nowhere.toml")
        assert "no such file" in str(raised.value)


class TestReadingAConfigurationWritesNothing:
    """The filesystem side-effect check #203 asks for."""

    def test_loading_changes_no_file(self, workspace: Path) -> None:
        before = _inventory(workspace)
        load(workspace)
        assert _inventory(workspace) == before

    def test_discovery_changes_no_file(self, workspace: Path) -> None:
        before = _inventory(workspace)
        discover(workspace / "apps" / "gui")
        assert _inventory(workspace) == before

    def test_a_failed_load_changes_no_file(self, workspace: Path) -> None:
        (workspace / "ici.toml").write_text("schema_version = 1\n[workspace]\nnope = 1\n")
        before = _inventory(workspace)
        with pytest.raises(NextConfigError):
            load(workspace)
        assert _inventory(workspace) == before


def _inventory(root: Path) -> set[tuple[str, int, float]]:
    return {
        (str(path.relative_to(root)), path.stat().st_size, path.stat().st_mtime)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TestInitProposesAndChangesNothing:
    """#203 item 5, and its acceptance criterion."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "module.py").write_text("value = 1\n", encoding="utf-8")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "p"\n', encoding="utf-8")
        return tmp_path

    def test_proposing_writes_nothing(self, project: Path) -> None:
        before = _inventory(project)
        propose(project)
        assert _inventory(project) == before

    def test_it_proposes_a_component_it_can_justify(self, project: Path) -> None:
        proposal = propose(project)
        assert proposal.candidates[0].languages == ("python",)
        assert "pyproject.toml" in proposal.candidates[0].evidence

    def test_what_it_writes_parses_under_the_real_schema(self, project: Path) -> None:
        from ici.config.schema import read_root

        read_root(propose(project).text, path="proposed")

    def test_it_references_a_tool_config_without_reading_it(self, project: Path) -> None:
        # SPEC-01 section 7: a flattened copy is a second source of truth.
        proposal = propose(project)
        assert "pyproject.toml" in proposal.referenced_tool_configs
        assert "[tool.ruff]" not in proposal.text
        assert 'name = "p"' not in proposal.text

    def test_it_refuses_to_choose_between_two_builds(self, tmp_path: Path) -> None:
        # SPEC-01 section 6 forbids picking the first .pro: a build chosen by
        # directory order is a build nobody decided on.
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            (tmp_path / name / f"{name}.pro").write_text("TEMPLATE=app\n", encoding="utf-8")
            (tmp_path / name / "m.cpp").write_text("int main(){}\n", encoding="utf-8")
        from ici.config.schema import read_root

        proposal = propose(tmp_path)
        assert proposal.undecided
        # Asserted on the parsed result, not the text: the text mentions
        # [builds.<id>] in the comment telling the user what to add, and a
        # substring check would pass on a proposal that had chosen one anyway.
        assert read_root(proposal.text, path="proposed").builds == ()
        assert "UNDECIDED" in proposal.text
        assert "a.pro" in proposal.text and "b.pro" in proposal.text

    def test_one_build_is_not_an_ambiguity(self, tmp_path: Path) -> None:
        (tmp_path / "only.pro").write_text("TEMPLATE=app\n", encoding="utf-8")
        (tmp_path / "m.cpp").write_text("int main(){}\n", encoding="utf-8")
        assert propose(tmp_path).undecided == ()

    def test_it_says_so_when_it_found_nothing(self, tmp_path: Path) -> None:
        proposal = propose(tmp_path)
        assert proposal.candidates == ()
        assert "no component was written" in proposal.text.lower()

    def test_writing_refuses_to_replace_an_existing_file(self, project: Path) -> None:
        target = project / "ici.toml"
        target.write_text("# mine\n", encoding="utf-8")
        with pytest.raises(FileExistsError):
            write(propose(project), target)
        assert target.read_text(encoding="utf-8") == "# mine\n"

    def test_overwriting_takes_an_explicit_yes(self, project: Path) -> None:
        target = project / "ici.toml"
        target.write_text("# mine\n", encoding="utf-8")
        write(propose(project), target, overwrite=True)
        assert "schema_version = 1" in target.read_text(encoding="utf-8")

    def test_a_proposal_reports_that_it_would_overwrite(self, project: Path) -> None:
        assert propose(project).would_overwrite is False
        (project / "ici.toml").write_text("# mine\n", encoding="utf-8")
        assert propose(project).would_overwrite is True

    def test_writing_touches_only_the_target(self, project: Path) -> None:
        before = _inventory(project)
        write(propose(project), project / "ici.toml")
        after = _inventory(project)
        added = {name for name, _, _ in after} - {name for name, _, _ in before}
        assert added == {"ici.toml"}
        unchanged = {entry for entry in after if entry[0] != "ici.toml"}
        assert unchanged == before

    def test_a_written_config_is_then_discoverable(self, project: Path) -> None:
        (project / ".git").mkdir()
        write(propose(project), project / "ici.toml")
        assert discover(project).is_workspace
