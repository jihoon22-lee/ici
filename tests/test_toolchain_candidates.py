"""Candidates come from the configuration and nowhere else (#204 PR C, item 7).

Two hardcoded assumptions leave the new path here, and they are the same mistake
in different clothes: a location that got into the answer without anyone putting
it there.

- ``core/env.py`` compiles one department's C++ library, pinned to one version,
  into ici.
- ``find_project_executable`` prefers a ``.venv`` whether or not the project
  mentioned one — the first link in the chain that ends with ``_resolve_python``
  returning ``sys.executable``.

The test that matters most here asserts an absence: a component that declares no
interpreter produces **no candidates**, so the resolver has nothing to fall back
to. The first acceptance criterion of #204 is reached by having nothing to
substitute rather than by remembering not to substitute.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

from ici.config.composition import compose
from ici.config.schema import read_root
from ici.toolchain.assumptions import ASSUMPTIONS, stale, warnings_for
from ici.toolchain.candidates import analyzer_request, python_request
from ici.toolchain.resolution import ProbeResult, ResolvedTool, Role, Unresolved
from ici.toolchain.resolver import Resolver

WORKSPACE = """
schema_version = 1
[workspace]
[[components]]
id = "silent"
root = "a"
languages = ["python"]
[[components]]
id = "declares"
root = "b"
languages = ["python"]
[components.python]
executable = ".venv/bin/python"
[[components]]
id = "bare"
root = "c"
languages = ["python"]
[components.python]
executable = "python3.11"
"""


def _component(component_id: str):
    component = compose(read_root(WORKSPACE, path="ici.toml")).component(component_id)
    assert component is not None
    return component


def _resolver(*, exists: bool = True) -> Resolver:
    return Resolver(
        lambda argv: ProbeResult(exit_code=0, output="Python 3.11.9"),
        exists=lambda path: exists,
        realpath=lambda path: path,
    )


class TestAComponentThatDeclaresNothingGetsNothing:
    def test_it_produces_no_candidates(self) -> None:
        assert python_request(_component("silent")).candidates == ()

    def test_the_resolver_has_nothing_to_fall_back_to(self) -> None:
        # Not "remembers not to fall back" — there is nothing to fall back to.
        resolved = _resolver().resolve(python_request(_component("silent")))
        assert isinstance(resolved, Unresolved)
        assert not resolved.usable

    def test_nothing_is_probed_for_it(self) -> None:
        resolver = _resolver()
        resolver.resolve(python_request(_component("silent")))
        assert resolver.probed == ()

    def test_no_venv_is_offered_without_being_declared(self) -> None:
        # The assumption item 7 removes: .venv was searched whether or not the
        # project mentioned it.
        paths = [candidate.path for candidate in python_request(_component("silent")).candidates]
        assert not any(".venv" in path for path in paths)


class TestADeclaredInterpreterIsTakenAsChosen:
    def test_it_is_anchored_to_the_component_root(self) -> None:
        request = python_request(_component("declares"))
        assert [c.path for c in request.candidates] == ["b/.venv/bin/python"]

    def test_it_is_explicit_so_a_failure_does_not_look_elsewhere(self) -> None:
        assert python_request(_component("declares")).candidates[0].explicit

    def test_a_declared_interpreter_that_is_missing_is_unresolved(self) -> None:
        resolved = _resolver(exists=False).resolve(python_request(_component("declares")))
        assert isinstance(resolved, Unresolved)

    def test_it_carries_the_key_that_would_change_it(self) -> None:
        candidate = python_request(_component("declares")).candidates[0]
        assert candidate.config_key
        assert "python.executable" in candidate.config_key

    def test_a_bare_name_stays_a_path_lookup(self) -> None:
        # SPEC-01 section 3 splits on the separator; anchoring "python3.11" to a
        # component root would turn a PATH lookup into a path that does not exist.
        assert [c.path for c in python_request(_component("bare")).candidates] == ["python3.11"]

    def test_the_component_root_does_not_depend_on_the_working_directory(self) -> None:
        first = python_request(_component("declares"), workspace_root=PurePosixPath("/w"))
        second = python_request(_component("declares"), workspace_root=PurePosixPath("/w"))
        assert first.candidates[0].path == second.candidates[0].path == "/w/b/.venv/bin/python"


class TestAnAnalyzerComesFromTheBundleOrIsNamed:
    def test_the_bundle_is_used_when_nothing_is_declared(self) -> None:
        request = analyzer_request("ruff", bundle_path="/bundle/tools/ruff")
        assert [c.source for c in request.candidates] == ["bundle"]

    def test_a_declared_path_replaces_the_bundle(self) -> None:
        request = analyzer_request(
            "ruff", bundle_path="/bundle/tools/ruff", declared_path="/opt/ruff"
        )
        assert [c.path for c in request.candidates] == ["/opt/ruff"]
        assert request.candidates[0].explicit

    def test_path_is_never_a_candidate(self) -> None:
        # A linter picked up from PATH makes the result depend on what else is
        # installed on the machine.
        request = analyzer_request("ruff", bundle_path="/bundle/tools/ruff")
        assert all(candidate.source != "PATH" for candidate in request.candidates)

    def test_with_neither_there_is_nothing_to_resolve(self) -> None:
        resolved = _resolver().resolve(analyzer_request("ruff"))
        assert isinstance(resolved, Unresolved)

    def test_a_bundled_analyzer_resolves(self) -> None:
        resolved = _resolver().resolve(analyzer_request("ruff", bundle_path="/bundle/tools/ruff"))
        assert isinstance(resolved, ResolvedTool)
        assert resolved.role is Role.ANALYZER


class TestTheMigrationWarningsStayTrue:
    """A note that rots is worse than none: it misleads with authority."""

    def test_every_assumption_still_describes_real_code(self) -> None:
        assert stale() == (), stale()

    def test_each_one_names_its_replacement(self) -> None:
        for assumption in ASSUMPTIONS:
            assert assumption.replacement
            assert assumption.file in str(assumption)

    def test_the_hardcoded_library_is_named(self) -> None:
        lines = " ".join(warnings_for(("nas-cpp-library",)))
        assert "department" in lines and "ici.toml" in lines

    def test_the_interpreter_fallback_is_named(self) -> None:
        lines = " ".join(warnings_for(("interpreter-fallback",)))
        assert "does not say so" in lines

    def test_asking_for_one_returns_one(self) -> None:
        assert len(warnings_for(("automatic-venv",))) == 1
        assert len(warnings_for()) == len(ASSUMPTIONS)


class TestNothingHereIsHardcoded:
    """Asserted on the syntax tree, not on the text.

    The first version of these two grepped the source and failed on the
    docstrings that explain what the new path avoids — a check that reads prose
    would also pass on a module whose prose said the right thing while its code
    did not.
    """

    @staticmethod
    def _modules() -> list[Path]:
        package = Path(__file__).resolve().parents[1] / "src" / "ici" / "toolchain"
        # assumptions.py names the old path on purpose; that is its whole job.
        return [m for m in sorted(package.glob("*.py")) if m.name != "assumptions.py"]

    @staticmethod
    def _literals(module: Path) -> list[str]:
        """Every string the code uses, with docstrings left out."""

        tree = ast.parse(module.read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        }
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def test_no_department_path_is_used_by_the_new_toolchain(self) -> None:
        for module in self._modules():
            for literal in self._literals(module):
                assert "ips-core-lib" not in literal, module.name
                assert "nas_shared" not in literal, module.name

    def test_no_venv_path_is_used_as_a_candidate(self) -> None:
        # Declared ones come from the config; none is written here.
        for module in self._modules():
            for literal in self._literals(module):
                assert ".venv" not in literal, f"{module.name}: {literal!r}"

    def test_nothing_reads_sys_executable(self) -> None:
        # An attribute access, which no docstring can be mistaken for.
        for module in self._modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "executable":
                    assert not (isinstance(node.value, ast.Name) and node.value.id == "sys"), (
                        module.name
                    )
