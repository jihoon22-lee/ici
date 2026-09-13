"""What ici actually does when it runs a tool (WP03 #201).

PR A built the recorder; this is the verification it exists for. #201 asks for a
harness that checks execution counts, environment preservation, and the
fail/blocked distinction, and a harness nothing exercises checks none of them.

Everything here goes through ``run_process``, which is the single place ici
spawns anything. Testing the choke point rather than one engine means a new
engine inherits these guarantees instead of needing its own copy of them.

The claims are not decorative. R01 forbids ici from sourcing a shell
initialisation file, and until now that was an architectural assertion with no
executable proof — the recorder makes it measurable, so a refactor that
introduced ``shell=True`` would fail here rather than in a customer's locked-down
environment.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from ici.core.runner import run_process
from toolcontract import ToolBox

# What a caller sees when the program could not be started at all, as opposed to
# a program that ran and failed. See TestFailIsNotBlocked.
_BLOCKED_MARKER = "Failed to execute"


class TestNoShellIsInvolved:
    """``run_process`` passes argv straight to Popen. These prove it."""

    def test_shell_metacharacters_arrive_literally(self, tmp_path):
        """A shell would expand every one of these before the tool saw them."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        hostile = ["$HOME", "*", "a;b", "`id`", "$(whoami)", "x|y", "~", "a&&b"]

        run_process([str(tool.path), *hostile], cwd=tmp_path)

        assert list(tool.only_call().argv) == hostile

    def test_a_shell_initialisation_file_injects_nothing(self, tmp_path):
        """R01: ici must not source a shell profile.

        Four profiles are planted because which one a shell reads depends on the
        shell and on whether it thinks it is interactive; a regression that
        picked any of them would be caught.
        """

        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        home = tmp_path / "home"
        home.mkdir()
        for name in (".bashrc", ".profile", ".bash_profile", ".zshrc"):
            (home / name).write_text("export ICI_FROM_PROFILE=leaked\n", encoding="utf-8")

        run_process(
            [str(tool.path)],
            cwd=tmp_path,
            env={"HOME": str(home), "PATH": os.environ["PATH"]},
            replace_env=True,
        )

        assert tool.only_call().env_value("ICI_FROM_PROFILE") is None

    def test_an_argument_that_looks_like_a_redirect_is_just_an_argument(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        target = tmp_path / "should-not-exist"

        run_process([str(tool.path), ">", str(target)], cwd=tmp_path)

        assert tool.only_call().argv == (">", str(target))
        assert not target.exists()


class TestEnvironmentPreservation:
    """R02 is about ici not rewriting the environment a project relies on."""

    def test_the_parent_environment_reaches_the_tool(self, tmp_path, monkeypatch):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        monkeypatch.setenv("ICI_PROBE_PARENT", "visible")

        run_process([str(tool.path)], cwd=tmp_path)

        assert tool.only_call().env_value("ICI_PROBE_PARENT") == "visible"

    def test_an_added_variable_does_not_clear_the_rest(self, tmp_path, monkeypatch):
        """``env=`` updates the inherited environment rather than replacing it.

        A project whose build reads a variable ici knows nothing about keeps it.
        """

        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        monkeypatch.setenv("ICI_PROBE_PARENT", "visible")

        run_process([str(tool.path)], cwd=tmp_path, env={"ICI_PROBE_EXTRA": "added"})

        call = tool.only_call()
        assert call.env_value("ICI_PROBE_EXTRA") == "added"
        assert call.env_value("ICI_PROBE_PARENT") == "visible"

    def test_replace_env_really_replaces(self, tmp_path, monkeypatch):
        """The isolating option has to actually isolate, or a test that uses it
        to prove independence proves nothing."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        monkeypatch.setenv("ICI_PROBE_PARENT", "visible")

        run_process(
            [str(tool.path)],
            cwd=tmp_path,
            env={"PATH": os.environ["PATH"]},
            replace_env=True,
        )

        call = tool.only_call()
        assert call.env_value("ICI_PROBE_PARENT") is None
        # LC_CTYPE is added by the interpreter, not by ici. Naming it keeps the
        # assertion exact instead of allowing any amount of leakage.
        assert set(call.env) <= {"PATH", "LC_CTYPE"}

    def test_the_working_directory_is_the_one_asked_for(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        run_process([str(tool.path)], cwd=elsewhere)

        assert Path(tool.only_call().cwd).resolve() == elsewhere.resolve()


class TestExecutionCount:
    """Run-sharing claims are claims about counts (#201, and R08's shared
    execution). Counting is the only way to check them."""

    def test_one_call_runs_the_tool_exactly_once(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")

        run_process([str(tool.path)], cwd=tmp_path)

        assert tool.call_count == 1

    def test_repeated_calls_are_counted_separately(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer")
        for index in range(3):
            run_process([str(tool.path), str(index)], cwd=tmp_path)

        assert [call.argv for call in tool.calls] == [("0",), ("1",), ("2",)]

    def test_a_tool_that_was_never_needed_never_ran(self, tmp_path):
        """The other half: a count of zero is a real result, not a missing one."""

        box = ToolBox(tmp_path / "bin")
        unused = box.add("clang-tidy")
        used = box.add("ruff")

        run_process([str(used.path)], cwd=tmp_path)

        assert used.call_count == 1
        assert unused.call_count == 0


class TestFailIsNotBlocked:
    """A tool that ran and disliked the code, versus one that never started.

    Conflating them is how a gate reports "clean" for an analysis that never
    happened, which is the same mistake #201 names for baseline comparison.
    """

    def test_a_tool_that_ran_and_failed_reports_its_own_exit_code(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer", exit_code=7, stderr="12 violations\n")

        result = run_process([str(tool.path)], cwd=tmp_path)

        assert result.returncode == 7
        assert result.stderr == "12 violations\n"
        assert tool.call_count == 1
        assert _BLOCKED_MARKER not in result.stderr

    def test_a_tool_that_does_not_exist_is_marked_as_unstartable(self, tmp_path):
        result = run_process([str(tmp_path / "absent-tool")], cwd=tmp_path)

        assert result.returncode == -1
        assert result.stderr.startswith(_BLOCKED_MARKER)

    def test_a_missing_tool_does_not_raise(self, tmp_path):
        """It returns a result instead, so a caller cannot mistake the absence
        for a crash in ici itself."""

        assert run_process([str(tmp_path / "absent-tool")], cwd=tmp_path).returncode == -1

    def test_a_signal_death_is_not_reported_as_unstartable(self, tmp_path):
        """The distinction that a returncode alone cannot carry.

        A process killed by SIGHUP also yields -1, so the exit code is not a
        sufficient test for "could not start" — the stderr marker is. Pinning
        this stops a refactor from collapsing the two into one code path.
        """

        suicide = tmp_path / "suicide"
        suicide.write_text(
            f"#!{sys.executable}\nimport os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
            encoding="utf-8",
        )
        suicide.chmod(suicide.stat().st_mode | stat.S_IXUSR)

        result = run_process([str(suicide)], cwd=tmp_path)

        assert result.returncode == -9
        assert not result.stderr.startswith(_BLOCKED_MARKER)

    def test_truncation_is_reported_rather_than_hidden(self, tmp_path):
        """Silently shortened output is how a parser reads half a report and
        calls it complete."""

        box = ToolBox(tmp_path / "bin")
        tool = box.add("analyzer", stdout="x" * 5000)

        result = run_process([str(tool.path)], cwd=tmp_path, max_output_chars=100)

        assert result.truncated is True
        assert len(result.stdout) == 100
        assert tool.call_count == 1

    def test_output_that_fits_is_not_marked_truncated(self, tmp_path):
        box = ToolBox(tmp_path / "bin")
        box.add("analyzer", stdout="short\n")

        result = run_process([str(tmp_path / "bin" / "analyzer")], cwd=tmp_path)

        assert result.truncated is False
        assert result.stdout == "short\n"
