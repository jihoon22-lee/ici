"""Stopping a process tree, and the difference between clean and unchecked.

#205's third acceptance criterion asks for cleanup to be *verified*. The tests
that matter most here are the ones that make sure an unverified cleanup cannot
pass for a successful one: that is the same shape of mistake as the empty PASS
the first criterion forbids, and it is easier to make, because a cleanup
routine that raises nothing looks exactly like a cleanup routine that worked.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from ici.execution.tree import (
    Cleanup,
    _group_from_stat,
    can_inspect_groups,
    group_of,
    members_of,
    signal_group,
    stop_group,
)

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
needs_proc = pytest.mark.skipif(
    not can_inspect_groups(), reason="process groups cannot be enumerated here"
)


def _spawn(source: str) -> subprocess.Popen[bytes]:
    """Start a Python child in its own session, as the runner does."""

    return subprocess.Popen(
        [sys.executable, "-c", source],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _wait_until(predicate, limit: float = 10.0) -> bool:
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _reap(proc: subprocess.Popen[bytes]) -> None:
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - safety net
        proc.kill()
        proc.wait(timeout=5)


# --- the distinction the criterion is about -------------------------------


def test_a_cleanup_nobody_could_check_is_not_a_clean_one() -> None:
    unchecked = Cleanup(group=1234, signalled=True, survivors=None)

    assert not unchecked.verified
    assert not unchecked.is_clean
    assert "not verified" in str(unchecked)


def test_a_cleanup_that_looked_and_found_nothing_is_clean() -> None:
    checked = Cleanup(group=1234, signalled=True, survivors=frozenset())

    assert checked.verified
    assert checked.is_clean


def test_a_cleanup_that_found_survivors_names_them() -> None:
    left = Cleanup(group=1234, signalled=True, survivors=frozenset({7, 3}))

    assert left.verified
    assert not left.is_clean
    assert "3, 7" in str(left)


# --- reading /proc --------------------------------------------------------


def test_a_command_name_with_spaces_and_brackets_does_not_shift_the_fields() -> None:
    # The comm field is untrusted text: a program can be called ") 1 2 3 (".
    line = "42 (weird ) name (x) 4) S 41 99 99 0 -1 4194304 100 0 0 0"

    assert _group_from_stat(line) == 99


def test_a_zombie_is_not_counted_as_a_survivor() -> None:
    # It holds nothing and can start nothing, so counting it would report every
    # cleanup as having failed for as long as nobody had called wait().
    assert _group_from_stat("42 (python3) Z 41 99 99 0 -1 0 0 0 0 0") is None


def test_a_truncated_stat_line_is_not_guessed_at() -> None:
    assert _group_from_stat("42 (python3) S 41") is None
    assert _group_from_stat("nothing like a stat line") is None


# --- refusing to shoot ourselves -----------------------------------------


@posix_only
def test_signalling_icis_own_group_is_refused() -> None:
    # Without this the cleanup path would take down the process running the
    # cleanup, and in a test run, the test runner.
    with pytest.raises(ValueError, match="own process group"):
        signal_group(os.getpgrp(), signal.SIGTERM)


@pytest.mark.parametrize("group", [0, -1])
def test_a_number_that_is_not_a_process_group_is_refused(group: int) -> None:
    with pytest.raises(ValueError, match="not a process group"):
        signal_group(group, signal.SIGTERM)


def test_signalling_a_group_that_is_gone_reports_that_it_was_not_delivered() -> None:
    # 2**22 - 1 is above the usual pid_max, so nothing is there to signal.
    assert signal_group(4_194_303, signal.SIGTERM) is False


# --- real trees -----------------------------------------------------------


@posix_only
@needs_proc
def test_a_childs_group_holds_the_children_it_starts() -> None:
    proc = _spawn(
        "import subprocess, sys, time\n"
        "kids = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
        " for _ in range(3)]\n"
        "print('up', flush=True)\n"
        "time.sleep(30)\n"
    )
    try:
        group = group_of(proc.pid)
        assert group == proc.pid
        assert _wait_until(lambda: len(members_of(group) or ()) == 4)

        cleanup = stop_group(group, grace=2.0, reason="test")

        assert cleanup.signalled
        assert cleanup.interrupted
        assert cleanup.verified, "the tree was never inspected"
        assert cleanup.is_clean, f"left behind: {cleanup.survivors}"
    finally:
        _reap(proc)


@posix_only
@needs_proc
def test_a_supervisor_cannot_outrun_the_cleanup_by_starting_more_children() -> None:
    # #205 names the child-respawn case. Signalling pids one at a time is a
    # race against a process whose whole job is to make more of them; the group
    # reaches the replacement too, because a child inherits its parent's group.
    proc = _spawn(
        "import subprocess, sys, time\n"
        "print('up', flush=True)\n"
        "while True:\n"
        "    kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.3)'])\n"
        "    kid.wait()\n"
    )
    try:
        group = group_of(proc.pid)
        assert _wait_until(lambda: len(members_of(group) or ()) >= 2)

        cleanup = stop_group(group, grace=2.0, reason="test")

        assert cleanup.is_clean, f"a replacement survived: {cleanup.survivors}"
        assert members_of(group) == frozenset()
    finally:
        _reap(proc)


@posix_only
@needs_proc
def test_a_tool_that_ignores_sigterm_is_killed_after_the_grace_period() -> None:
    proc = _spawn(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('deaf', flush=True)\n"
        "time.sleep(60)\n"
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == b"deaf"
        group = group_of(proc.pid)

        cleanup = stop_group(group, grace=0.3, reason="test")

        assert cleanup.escalated, "SIGTERM was ignored and nothing followed it"
        assert cleanup.is_clean, f"survived the kill: {cleanup.survivors}"
    finally:
        _reap(proc)


@posix_only
@needs_proc
def test_a_tool_given_the_chance_exits_on_its_own_and_is_not_killed() -> None:
    # The grace period is not a formality: a tool that handles SIGTERM is how
    # its own temporary files get removed.
    proc = _spawn(
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))\n"
        "print('ready', flush=True)\n"
        "time.sleep(60)\n"
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == b"ready"

        cleanup = stop_group(group_of(proc.pid), grace=5.0, reason="test")

        assert cleanup.signalled
        assert not cleanup.escalated
        assert cleanup.is_clean
    finally:
        _reap(proc)


@posix_only
@needs_proc
def test_stopping_a_group_that_has_already_gone_reports_no_signal() -> None:
    proc = _spawn("pass")
    group = group_of(proc.pid) or proc.pid
    _reap(proc)
    assert _wait_until(lambda: members_of(group) == frozenset())

    cleanup = stop_group(group, grace=0.2, reason="test")

    assert not cleanup.signalled
    assert not cleanup.interrupted
    assert cleanup.is_clean


def test_group_of_a_pid_that_is_gone_is_not_a_number() -> None:
    assert group_of(4_194_303) is None


@posix_only
@needs_proc
def test_a_group_whose_leader_has_gone_but_whose_child_lives_is_still_interrupted() -> None:
    # "Interrupted" is about the group, not the leader. A tool whose leader has
    # exited while a child still holds its output open has not finished saying
    # what it found, and reading the leader's exit code there is how a
    # cancelled run turns back into a clean pass.
    proc = _spawn(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    )
    group = group_of(proc.pid) or proc.pid
    try:
        proc.wait(timeout=10)
        # The leader is reaped; only the child it started is left in the group.
        assert _wait_until(lambda: len(members_of(group) or ()) == 1)

        cleanup = stop_group(group, grace=2.0, reason="test")

        assert cleanup.signalled
        assert cleanup.interrupted, "the surviving child was treated as nothing to stop"
        assert cleanup.is_clean
    finally:
        _reap(proc)


def test_a_cleanup_that_stopped_nothing_is_not_an_interruption() -> None:
    assert not Cleanup(group=1, signalled=False, survivors=frozenset()).interrupted
