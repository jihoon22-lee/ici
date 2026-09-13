"""The corpus register and the tool-contract harness (WP03 #201, PR A).

These test the machinery, not the engines. The register is only worth having if
it is complete and its probes are honest, and the harness is only worth having
if it records what it claims to and needs nothing from a shell profile.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixture_manifest import (
    REPO_ROOT,
    STRICT_ENV,
    Fixture,
    ManifestError,
    load_manifest,
    missing_requirements,
    require,
)
from toolcontract import ToolBox

CPP_FIXTURE_DIR = REPO_ROOT / "examples" / "cpp-fixtures"
PYTHON_FIXTURE_DIR = REPO_ROOT / "examples" / "python-fixtures"
NEXT_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "ici-next"


class TestTheRegisterIsComplete:
    """A register that misses a fixture is worse than none: it looks authoritative."""

    def test_every_cpp_fixture_directory_is_registered(self):
        on_disk = {path.name for path in CPP_FIXTURE_DIR.iterdir() if path.is_dir()}
        registered = {
            entry.path.name
            for entry in load_manifest().values()
            if entry.path.parent == CPP_FIXTURE_DIR
        }

        assert on_disk == registered

    def test_every_python_fixture_directory_is_registered(self):
        on_disk = {path.name for path in PYTHON_FIXTURE_DIR.iterdir() if path.is_dir()}
        registered = {
            entry.path.name
            for entry in load_manifest().values()
            if entry.path.parent == PYTHON_FIXTURE_DIR
        }

        assert on_disk == registered

    def test_every_ici_next_fixture_file_is_registered(self):
        on_disk = {path.name for path in NEXT_FIXTURE_DIR.glob("*.json")}
        registered = {
            entry.path.name
            for entry in load_manifest().values()
            if entry.path.parent == NEXT_FIXTURE_DIR
        }

        assert on_disk == registered

    def test_every_registered_path_exists(self):
        missing = [entry.id for entry in load_manifest().values() if not entry.path.exists()]

        assert missing == []

    def test_every_entry_names_its_provenance_and_licence(self):
        """#201: the corpus must be re-runnable from this repository alone, which
        means knowing where each piece came from."""

        for entry in load_manifest().values():
            assert entry.provenance, entry.id
            assert entry.license, entry.id

    def test_every_entry_says_what_it_should_produce(self):
        for entry in load_manifest().values():
            assert entry.expectation, entry.id

    def test_every_language_with_defect_seeds_has_a_control(self):
        """#201 asks for a clean counterpart, not just defect seeds.

        Stated as the invariant rather than a list of ids: a language gaining
        its first defect seed without a control should fail here, and adding a
        control should not require editing this test.
        """

        entries = load_manifest().values()
        languages = {
            language for entry in entries if entry.role != "control" for language in entry.languages
        }
        controlled = {
            language for entry in entries if entry.role == "control" for language in entry.languages
        }

        assert languages <= controlled, f"no control fixture for: {sorted(languages - controlled)}"

    def test_data_fixtures_declare_no_tool_requirements(self):
        """The real/mock split is only meaningful if 'data' really means no tools."""

        for entry in load_manifest().values():
            if entry.kind == "data":
                assert entry.requires == (), entry.id

    def test_real_tool_fixtures_declare_at_least_one(self):
        for entry in load_manifest().values():
            if entry.kind == "real-tool":
                assert entry.requires, entry.id

    def test_an_unknown_id_is_an_error_not_a_skip(self):
        """A typo in a guard must not quietly turn a test off."""

        with pytest.raises(ManifestError, match="no fixture"):
            require("cpp/does-not-exist")


class TestTheProbesAreHonest:
    def test_cmake_project_requires_qt6_not_merely_cmake(self):
        """The defect this register exists for.

        cmake_project's CMakeLists calls find_package(Qt6 REQUIRED). A guard
        naming only cmake, ctest and gcov cannot skip on a machine without Qt6,
        so it runs the test and reports a failure instead.
        """

        entry = load_manifest()["cpp/cmake_project"]
        cmake_packages = [
            item["cmake_package"] for item in entry.requires if "cmake_package" in item
        ]

        assert cmake_packages == ["Qt6"]

    def test_the_declared_requirement_matches_what_the_cmakelists_asks_for(self):
        """Keeps the register honest against the fixture it describes."""

        entry = load_manifest()["cpp/cmake_project"]
        text = (entry.path / "CMakeLists.txt").read_text(encoding="utf-8")
        declared = next(item for item in entry.requires if "cmake_package" in item)

        assert f"find_package({declared['cmake_package']}" in text
        for component in declared.get("components", ()):
            assert component in text

    def test_qmake_project_probes_the_program_because_qmake_ships_with_qt(self):
        """The contrast: finding qmake really does imply the libraries, so no
        package probe is needed here."""

        entry = load_manifest()["cpp/qmake_project"]

        assert any("any_executable" in item for item in entry.requires)
        assert not any("cmake_package" in item for item in entry.requires)

    def test_a_missing_requirement_is_named_not_merely_counted(self):
        """A skip reason that does not say what is missing costs an investigation."""

        for entry in load_manifest().values():
            for reason in missing_requirements(entry.id):
                assert reason.strip()
                assert reason != repr({})

    def test_data_fixtures_are_runnable_everywhere(self):
        for entry in load_manifest().values():
            if entry.kind == "data":
                assert missing_requirements(entry.id) == [], entry.id


class TestTheStrictSwitchStillBites:
    """ici shipped a green gate while lint had never run (C-6). A register that
    made a missing toolchain quietly skip in CI would repeat exactly that."""

    def _unsatisfiable(self, monkeypatch) -> None:
        monkeypatch.setattr("fixture_manifest._executable_available", lambda _name: False)
        monkeypatch.setattr(
            "fixture_manifest._cmake_package_available", lambda _pkg, _components: False
        )

    def test_without_the_switch_a_missing_tool_skips(self, monkeypatch):
        self._unsatisfiable(monkeypatch)
        monkeypatch.delenv(STRICT_ENV, raising=False)

        with pytest.raises(pytest.skip.Exception, match="needs"):
            require("cpp/cmake_project")

    def test_with_the_switch_a_missing_tool_fails(self, monkeypatch):
        self._unsatisfiable(monkeypatch)
        monkeypatch.setenv(STRICT_ENV, "1")

        with pytest.raises(pytest.fail.Exception, match="needs"):
            require("cpp/cmake_project")

    def test_the_failure_names_the_missing_requirement(self, monkeypatch):
        self._unsatisfiable(monkeypatch)
        monkeypatch.setenv(STRICT_ENV, "1")

        with pytest.raises(pytest.fail.Exception) as caught:
            require("cpp/cmake_project")

        assert "Qt6" in str(caught.value)


class TestTheRecorderRecords:
    def test_argv_reaches_the_assertion_unchanged(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("gcov")

        subprocess.run(
            ["gcov", "--branch-probabilities", "counter.gcda"],
            env=box.prepend_to_path(),
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )

        assert tool.only_call().argv == ("--branch-probabilities", "counter.gcda")

    def test_the_working_directory_is_recorded(self, tmp_path):
        """Several engines depend on being run from the project root."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("ctest")
        work = tmp_path / "shadow-build"
        work.mkdir()

        subprocess.run(
            ["ctest"], env=box.prepend_to_path(), cwd=work, capture_output=True, check=False
        )

        assert Path(tool.only_call().cwd).resolve() == work.resolve()

    def test_the_environment_the_tool_saw_is_recorded(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("qmake6")
        env = box.prepend_to_path({"ICI_MARKER": "kept"})

        subprocess.run(["qmake6"], env=env, cwd=tmp_path, capture_output=True, check=False)

        assert tool.only_call().env_value("ICI_MARKER") == "kept"

    def test_a_variable_that_was_never_set_reads_as_none_not_empty(self, tmp_path):
        """ "unset" and "set to empty" are different facts about an environment."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("cmake")

        subprocess.run(
            ["cmake"],
            env=box.prepend_to_path({"PATH": ""}),
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )

        assert tool.only_call().env_value("ICI_NEVER_SET") is None

    def test_repeated_calls_are_counted_in_order(self, tmp_path):
        """Run-sharing claims are about counts, so the log must not overwrite."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("g++")
        env = box.prepend_to_path()
        for source in ("a.cpp", "b.cpp", "c.cpp"):
            subprocess.run(
                ["g++", "-c", source], env=env, cwd=tmp_path, capture_output=True, check=False
            )

        assert tool.call_count == 3
        assert [call.argv[-1] for call in tool.calls] == ["a.cpp", "b.cpp", "c.cpp"]

    def test_only_call_refuses_to_read_the_first_of_several(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("nm")
        env = box.prepend_to_path()
        for _ in range(2):
            subprocess.run(["nm"], env=env, cwd=tmp_path, capture_output=True, check=False)

        with pytest.raises(AssertionError, match="run once, saw 2"):
            tool.only_call()

    def test_a_tool_that_was_never_run_has_no_calls(self, tmp_path):
        box = ToolBox(tmp_path / "bin")

        assert box.add("clang-tidy").calls == []

    def test_exit_code_and_streams_are_configurable(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        box.add("mypy", exit_code=2, stdout="found 3 errors\n", stderr="warning\n")

        completed = subprocess.run(
            ["mypy"],
            env=box.prepend_to_path(),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )

        assert completed.returncode == 2
        assert completed.stdout == "found 3 errors\n"
        assert completed.stderr == "warning\n"

    def test_a_truncated_final_record_is_dropped_not_raised(self, tmp_path):
        """A tool killed mid-write must not turn into a harness error."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("ruff")
        subprocess.run(
            ["ruff", "check"],
            env=box.prepend_to_path(),
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )
        with tool.log.open("a", encoding="utf-8") as stream:
            stream.write('{"argv": ["chec')

        assert tool.call_count == 1


class TestTheHarnessNeedsNoShellProfile:
    """#201 step 2, and the reason it matters: R01 forbids ici from sourcing a
    shell initialisation file. A harness that needed one could not detect a
    regression in that rule."""

    def test_the_recorder_runs_with_a_stripped_environment(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("g++")

        subprocess.run(
            ["g++", "-v"],
            env={"PATH": str(box.directory)},
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )

        assert tool.only_call().argv == ("-v",)

    def test_the_recorder_does_not_resolve_its_interpreter_through_path(self, tmp_path):
        """The shebang names the running interpreter. Looking one up on PATH
        would mean these tests measured the machine's PATH rather than ici's
        treatment of it."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("cmake")

        assert tool.path.read_text(encoding="utf-8").startswith(f"#!{sys.executable}\n")

    def test_no_shell_is_involved(self, tmp_path):
        """A /bin/sh recorder would read whatever the platform's non-interactive
        sh decides to read. This one is Python from the first byte."""

        box = ToolBox(tmp_path / "bin")
        body = box.add("ctest").path.read_text(encoding="utf-8")

        assert "/bin/sh" not in body

    def test_path_is_recorded_as_the_tool_saw_it(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("gcov")
        extra = tmp_path / "extra"
        extra.mkdir()

        subprocess.run(
            ["gcov"],
            env={"PATH": f"{box.directory}{os.pathsep}{extra}"},
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )

        assert tool.only_call().path_entries == (str(box.directory), str(extra))


class TestTheRegisterIsUsable:
    def test_require_returns_the_entry_so_a_guard_needs_one_lookup(self):
        entry = require("cpp/clean_baseline")

        assert isinstance(entry, Fixture)
        assert entry.path.is_dir()

    def test_a_data_fixture_never_skips(self):
        """Nothing about the machine should be able to turn these off."""

        assert require("next/run-success").path.is_file()
