"""Choosing a tool, and never choosing a different one instead (#204 PR A).

The rule these tests exist for is one line of #204: a failed explicit choice is
not an invitation to choose something else. The current path breaks it in a
specific, measurable way — when a project has no interpreter of its own,
``TestInterpreterMixin._resolve_python`` returns ``sys.executable``, so the
project's tests run on **ici's own interpreter** and the result does not say so.
Running ici under ``/usr/bin/python3`` against a project with no ``.venv``
returns ``/usr/bin/python3``.

Everything here runs against a matrix of fake executables. The probe is an
argument, so a stale VIRTUAL_ENV, two tools on PATH, a missing explicit path, a
version that is too old, output too long to read and a path with a space are
unit tests rather than fixtures on disk.
"""

from __future__ import annotations

import pytest

from ici.toolchain.environment import PROJECT_VARIABLES, PYTHON_VARIABLES, EnvironmentSnapshot
from ici.toolchain.resolution import (
    Availability,
    ProbeResult,
    ResolvedTool,
    Role,
    SelectionOrigin,
    Unresolved,
)
from ici.toolchain.resolver import Candidate, Request, Resolver


class FakeWorld:
    """A filesystem and a set of executables that exist only as answers."""

    def __init__(self, executables: dict[str, ProbeResult], links: dict[str, str] | None = None):
        self.executables = executables
        self.links = links or {}
        self.calls: list[tuple[str, ...]] = []

    def probe(self, argv):
        self.calls.append(tuple(argv))
        return self.executables.get(argv[0], ProbeResult(exit_code=127, output="not found"))

    def exists(self, path: str) -> bool:
        return path in self.executables

    def realpath(self, path: str) -> str:
        return self.links.get(path, path)

    def resolver(self) -> Resolver:
        return Resolver(self.probe, exists=self.exists, realpath=self.realpath)


def _python(version: str = "Python 3.11.9") -> ProbeResult:
    return ProbeResult(exit_code=0, output=version)


class TestAFailedExplicitChoiceIsNotAnInvitation:
    """#204 item 3, and the first acceptance criterion."""

    def test_a_missing_explicit_path_does_not_fall_back(self) -> None:
        world = FakeWorld({"/usr/bin/python3": _python()})
        result = world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(
                    Candidate("/project/.venv/bin/python", "config", "python.executable", True),
                    Candidate("/usr/bin/python3", "PATH"),
                ),
            )
        )
        assert isinstance(result, Unresolved)
        assert result.availability is Availability.UNAVAILABLE
        assert not result.usable

    def test_the_fallback_is_not_even_probed(
        self,
    ) -> None:
        # Not merely unreported: never asked. A probe of the substitute is how a
        # substitution gets made later "because we already know it works".
        world = FakeWorld({"/usr/bin/python3": _python()})
        world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(
                    Candidate("/project/.venv/bin/python", "config", "python.executable", True),
                    Candidate("/usr/bin/python3", "PATH"),
                ),
            )
        )
        assert world.calls == []

    def test_a_broken_explicit_choice_does_not_fall_back(self) -> None:
        world = FakeWorld(
            {
                "/project/.venv/bin/python": ProbeResult(exit_code=1, output="boom"),
                "/usr/bin/python3": _python(),
            }
        )
        result = world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(
                    Candidate("/project/.venv/bin/python", "config", "python.executable", True),
                    Candidate("/usr/bin/python3", "PATH"),
                ),
            )
        )
        assert isinstance(result, Unresolved)
        assert result.availability is Availability.BROKEN
        assert "/usr/bin/python3" not in str(result)

    def test_nothing_found_is_a_refusal_not_a_default(self) -> None:
        # The defect this replaces: a project with no interpreter of its own is
        # tested with ici's, and the result reads like any other pass.
        world = FakeWorld({})
        result = world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(Candidate("/project/.venv/bin/python", "project"),),
            )
        )
        assert isinstance(result, Unresolved)
        assert not result.usable

    def test_an_unresolved_tool_cannot_claim_to_be_available(self) -> None:
        with pytest.raises(ValueError, match="cannot be available"):
            Unresolved(
                role=Role.ANALYZER,
                name="ruff",
                availability=Availability.AVAILABLE,
                origin=SelectionOrigin("x", "y"),
                detail="",
            )


class TestLaunchPathIsNotIdentity:
    """#204 item 3: do not run the realpath of a virtual environment link."""

    def test_it_launches_what_was_written(self) -> None:
        world = FakeWorld(
            {"/project/.venv/bin/python": _python()},
            links={"/project/.venv/bin/python": "/usr/bin/python3.11"},
        )
        result = world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(Candidate("/project/.venv/bin/python", "project"),),
            )
        )
        assert isinstance(result, ResolvedTool)
        assert result.launch_path == "/project/.venv/bin/python"
        assert result.identity_path == "/usr/bin/python3.11"
        assert result.launches_through_a_link

    def test_it_probes_the_launch_path(self) -> None:
        # Probing the realpath would report on an interpreter outside the
        # environment — the answer the run will not get.
        world = FakeWorld(
            {"/project/.venv/bin/python": _python()},
            links={"/project/.venv/bin/python": "/usr/bin/python3.11"},
        )
        world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(Candidate("/project/.venv/bin/python", "project"),),
            )
        )
        assert world.calls[0][0] == "/project/.venv/bin/python"

    def test_a_tool_that_is_not_a_link_says_so(self) -> None:
        world = FakeWorld({"/usr/bin/gcc": ProbeResult(exit_code=0, output="gcc 13.2.0")})
        result = world.resolver().resolve(
            Request(role=Role.COMPILER, name="gcc", candidates=(Candidate("/usr/bin/gcc", "PATH"),))
        )
        assert isinstance(result, ResolvedTool)
        assert not result.launches_through_a_link

    def test_a_resolved_tool_needs_both_paths(self) -> None:
        with pytest.raises(ValueError, match="launch path"):
            ResolvedTool(
                role=Role.ANALYZER,
                name="ruff",
                launch_path="",
                identity_path="/x",
                origin=SelectionOrigin("x", "y"),
            )


class TestNotFoundTooOldAndBrokenAreThreeAnswers:
    """Three fixes: install it, upgrade it, find out what is wrong with it."""

    def test_missing_is_unavailable(self) -> None:
        world = FakeWorld({})
        result = world.resolver().resolve(
            Request(role=Role.ANALYZER, name="ruff", candidates=(Candidate("/bin/ruff", "PATH"),))
        )
        assert isinstance(result, Unresolved)
        assert result.availability is Availability.UNAVAILABLE

    def test_too_old_is_unsupported(self) -> None:
        world = FakeWorld({"/bin/ruff": ProbeResult(exit_code=0, output="ruff 0.1.2")})
        result = world.resolver().resolve(
            Request(
                role=Role.ANALYZER,
                name="ruff",
                candidates=(Candidate("/bin/ruff", "PATH"),),
                minimum_version=(0, 16),
            )
        )
        assert isinstance(result, Unresolved)
        assert result.availability is Availability.UNSUPPORTED
        assert "0.1.2" in result.detail and "0.16" in result.detail

    @pytest.mark.parametrize(
        ("result", "expected"),
        [
            (ProbeResult(exit_code=1, output="boom"), "exited 1"),
            (ProbeResult(exit_code=0, timed_out=True), "timed out"),
            (ProbeResult(exit_code=0, truncated=True), "more output than could be read"),
        ],
    )
    def test_a_tool_that_cannot_be_asked_is_broken(self, result, expected: str) -> None:
        world = FakeWorld({"/bin/ruff": result})
        resolved = world.resolver().resolve(
            Request(role=Role.ANALYZER, name="ruff", candidates=(Candidate("/bin/ruff", "PATH"),))
        )
        assert isinstance(resolved, Unresolved)
        assert resolved.availability is Availability.BROKEN
        assert expected in resolved.detail

    def test_unreadable_version_output_is_not_silently_accepted(self) -> None:
        world = FakeWorld({"/bin/ruff": ProbeResult(exit_code=0, output="hello")})
        resolved = world.resolver().resolve(
            Request(
                role=Role.ANALYZER,
                name="ruff",
                candidates=(Candidate("/bin/ruff", "PATH"),),
                minimum_version=(0, 16),
            )
        )
        assert isinstance(resolved, Unresolved)
        assert resolved.availability is Availability.UNSUPPORTED


class TestACapabilityIsAskedForNotInferred:
    """#204 item 6: verified by the option contract, not a version string."""

    def test_a_supported_option_is_recorded(self) -> None:
        world = FakeWorld({"/bin/mypy": ProbeResult(exit_code=0, output="mypy 1.11.0")})
        result = world.resolver().resolve(
            Request(
                role=Role.ANALYZER,
                name="mypy",
                candidates=(Candidate("/bin/mypy", "bundle"),),
                required_capabilities=("warn-unused-ignores",),
                capability_probes={"warn-unused-ignores": ("--warn-unused-ignores", "--version")},
            )
        )
        assert isinstance(result, ResolvedTool)
        assert result.capabilities == ("warn-unused-ignores",)

    def test_a_new_enough_version_missing_the_option_is_unsupported(self) -> None:
        # A build can lack what its version implies, which is why the version is
        # not the check.
        def probe(argv):
            if "--warn-unused-ignores" in argv:
                return ProbeResult(exit_code=2, output="unrecognized option")
            return ProbeResult(exit_code=0, output="mypy 99.0.0")

        resolver = Resolver(probe, exists=lambda p: True, realpath=lambda p: p)
        result = resolver.resolve(
            Request(
                role=Role.ANALYZER,
                name="mypy",
                candidates=(Candidate("/bin/mypy", "bundle"),),
                minimum_version=(1, 0),
                required_capabilities=("warn-unused-ignores",),
                capability_probes={"warn-unused-ignores": ("--warn-unused-ignores", "--version")},
            )
        )
        assert isinstance(result, Unresolved)
        assert result.availability is Availability.UNSUPPORTED
        assert "warn-unused-ignores" in result.detail

    def test_a_capability_with_no_way_to_check_it_is_not_assumed(self) -> None:
        world = FakeWorld({"/bin/mypy": ProbeResult(exit_code=0, output="mypy 1.11.0")})
        result = world.resolver().resolve(
            Request(
                role=Role.ANALYZER,
                name="mypy",
                candidates=(Candidate("/bin/mypy", "bundle"),),
                required_capabilities=("something",),
            )
        )
        assert isinstance(result, Unresolved)
        assert "no way to check it" in result.detail


class TestOnlyWhatIsAskedForIsProbed:
    """The third acceptance criterion, checkable because probes are recorded."""

    def test_a_candidate_after_the_winner_is_never_probed(self) -> None:
        world = FakeWorld(
            {"/bin/ruff": ProbeResult(exit_code=0, output="ruff 0.16.4"), "/other/ruff": _python()}
        )
        resolver = world.resolver()
        resolver.resolve(
            Request(
                role=Role.ANALYZER,
                name="ruff",
                candidates=(Candidate("/bin/ruff", "bundle"), Candidate("/other/ruff", "PATH")),
            )
        )
        assert resolver.probed == ("/bin/ruff",)

    def test_a_role_nobody_asked_about_is_never_probed(self) -> None:
        # A Python-only workspace must not reach for qmake.
        world = FakeWorld({"/bin/qmake": ProbeResult(exit_code=0, output="3.1")})
        resolver = world.resolver()
        resolver.resolve(
            Request(role=Role.ANALYZER, name="ruff", candidates=(Candidate("/bin/ruff", "PATH"),))
        )
        assert "/bin/qmake" not in resolver.probed

    def test_a_repeated_request_reuses_the_answer(self) -> None:
        world = FakeWorld({"/bin/ruff": ProbeResult(exit_code=0, output="ruff 0.16.4")})
        resolver = world.resolver()
        request = Request(
            role=Role.ANALYZER, name="ruff", candidates=(Candidate("/bin/ruff", "bundle"),)
        )
        resolver.resolve(request)
        resolver.resolve(request)
        assert len([call for call in world.calls if call[0] == "/bin/ruff"]) == 1


class TestTheChoiceExplainsItself:
    """The fourth acceptance criterion: the reason and the key to change it."""

    def test_it_names_the_key_that_would_change_the_answer(self) -> None:
        world = FakeWorld({"/bin/python": _python()})
        result = world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(Candidate("/bin/python", "config", "components.a.python.executable"),),
            )
        )
        assert isinstance(result, ResolvedTool)
        assert result.origin.config_key == "components.a.python.executable"
        assert "components.a.python.executable" in str(result.origin)

    def test_it_records_the_candidates_it_passed_over(self) -> None:
        world = FakeWorld({"/second/ruff": ProbeResult(exit_code=0, output="ruff 0.16.4")})
        result = world.resolver().resolve(
            Request(
                role=Role.ANALYZER,
                name="ruff",
                candidates=(Candidate("/first/ruff", "bundle"), Candidate("/second/ruff", "PATH")),
            )
        )
        assert isinstance(result, ResolvedTool)
        assert result.passed_over == ("/first/ruff",)

    def test_a_refusal_reports_what_it_considered(self) -> None:
        world = FakeWorld({})
        result = world.resolver().resolve(
            Request(
                role=Role.ANALYZER,
                name="ruff",
                candidates=(Candidate("/a/ruff", "bundle"), Candidate("/b/ruff", "PATH")),
            )
        )
        assert isinstance(result, Unresolved)
        assert result.considered == ("/a/ruff", "/b/ruff")

    def test_a_path_with_a_space_survives(self) -> None:
        world = FakeWorld({"/opt/my tools/python": _python()})
        result = world.resolver().resolve(
            Request(
                role=Role.PROJECT_PYTHON,
                name="python",
                candidates=(Candidate("/opt/my tools/python", "config"),),
            )
        )
        assert isinstance(result, ResolvedTool)
        assert result.launch_path == "/opt/my tools/python"


class TestTheEnvironmentIsAValue:
    """#204 item 1 and item 5."""

    def test_core_loses_only_the_python_variables(self) -> None:
        snapshot = EnvironmentSnapshot(
            {"PYTHONPATH": "/x", "VIRTUAL_ENV": "/v", "PATH": "/usr/bin", "SSL_CERT_FILE": "/ca"}
        )
        core = snapshot.for_core()
        assert all(name not in core.variables for name in PYTHON_VARIABLES)
        # WP01 measured that removing SSL_CERT_FILE loses the CA bundle an
        # internal server needs, so "isolation" stops before it.
        assert core.get("SSL_CERT_FILE") == "/ca"
        assert core.get("PATH") == "/usr/bin"

    def test_a_project_child_keeps_what_the_project_needs(self) -> None:
        values = {name: f"value-of-{name}" for name in PROJECT_VARIABLES}
        snapshot = EnvironmentSnapshot(values)
        assert snapshot.for_project().variables == values

    def test_an_overlay_does_not_touch_the_snapshot(self) -> None:
        # Per-task copies, so a task that needed PYTHONPATH set does not leave it
        # set for the next one.
        snapshot = EnvironmentSnapshot({"PATH": "/usr/bin"})
        child = snapshot.for_project(overlay={"PYTHONPATH": "/src"})
        assert child.get("PYTHONPATH") == "/src"
        assert snapshot.get("PYTHONPATH") is None

    def test_a_snapshot_cannot_be_edited(self) -> None:
        snapshot = EnvironmentSnapshot({"PATH": "/usr/bin"})
        with pytest.raises(TypeError):
            snapshot.variables["PATH"] = "/elsewhere"  # type: ignore[index]

    def test_a_stale_virtualenv_is_dropped(self) -> None:
        # A terminal left open across a rebuild reports on an interpreter that
        # is not the one running.
        snapshot = EnvironmentSnapshot({"VIRTUAL_ENV": "/gone", "PATH": "/usr/bin"})
        assert snapshot.without_stale_virtualenv().get("VIRTUAL_ENV") is None

    def test_a_live_virtualenv_is_kept(self) -> None:
        snapshot = EnvironmentSnapshot({"VIRTUAL_ENV": "/live", "PATH": "/live/bin:/usr/bin"})
        assert snapshot.without_stale_virtualenv().get("VIRTUAL_ENV") == "/live"

    def test_the_same_mapping_gives_the_same_answer(self) -> None:
        # The second acceptance criterion: no shell rc file is consulted, so a
        # selection can be reproduced from a recorded environment.
        values = {"PATH": "/usr/bin", "VIRTUAL_ENV": "/live"}
        assert EnvironmentSnapshot(values).for_core().variables == (
            EnvironmentSnapshot(dict(values)).for_core().variables
        )

    def test_it_can_say_what_differs(self) -> None:
        first = EnvironmentSnapshot({"PATH": "/a", "QTDIR": "/qt"})
        second = EnvironmentSnapshot({"PATH": "/b", "QTDIR": "/qt"})
        assert first.differences(second) == ("PATH",)
