"""Cancelling a run, and never letting the cancellation look like a clean pass.

The five things #205 says must not become a PASS end with cancellation, and it
is the one most likely to slip through, because a cancelled tool usually exits
0 or dies by a signal that some other layer already knows how to explain away.
So most of these tests are about what a cancelled run must *not* be reported
as, and about proving the cleanup was checked rather than assumed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ici.execution.cancellation import Cancellation, signal_cancels
from ici.execution.process import Outcome, TaskOutcome, TaskSpec, run_task
from ici.execution.tree import Cleanup, can_inspect_groups, members_of
from ici.execution.watchdog import Trigger, Watchdog

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
needs_proc = pytest.mark.skipif(
    not can_inspect_groups(), reason="process groups cannot be enumerated here"
)


def python_task(source: str, **kwargs: object) -> TaskSpec:
    return TaskSpec(argv=(sys.executable, "-c", source), **kwargs)  # type: ignore[arg-type]


SLEEPER = "import time; time.sleep(60)"


# --- the token ------------------------------------------------------------


def test_a_cancellation_starts_unrequested() -> None:
    cancellation = Cancellation()

    assert not cancellation.requested
    assert not cancellation
    assert cancellation.reason == ""


def test_a_cancellation_cannot_be_taken_back() -> None:
    # A retractable cancellation would let a run be reported as complete after
    # something had already decided its answer was not wanted.
    cancellation = Cancellation()
    cancellation.cancel("the user pressed Ctrl-C")
    cancellation.cancel("something else, later")

    assert cancellation.requested
    assert cancellation.reason == "the user pressed Ctrl-C"


def test_a_waiter_is_released_when_another_thread_cancels() -> None:
    cancellation = Cancellation()
    threading.Timer(0.05, cancellation.cancel, args=("from elsewhere",)).start()

    assert cancellation.wait(timeout=5.0)
    assert cancellation.reason == "from elsewhere"


def test_waiting_on_a_cancellation_that_never_comes_gives_up_quietly() -> None:
    assert Cancellation().wait(timeout=0.05) is False


# --- before anything is started ------------------------------------------


def test_a_task_cancelled_before_it_starts_never_starts(tmp_path: Path) -> None:
    # The cheapest way to clean up after a process is not to have started it.
    marker = tmp_path / "it-ran"
    cancellation = Cancellation()
    cancellation.cancel("asked to stop before the batch began")

    outcome = run_task(
        python_task(f"open({str(marker)!r}, 'w').write('x')"), cancellation=cancellation
    )

    assert not marker.exists(), "the task ran despite already being cancelled"
    assert outcome.outcome is Outcome.CANCELLED
    assert "before the batch began" in outcome.detail
    assert outcome.cleaned_up


def test_a_task_cancelled_before_it_starts_is_not_an_answer() -> None:
    cancellation = Cancellation()
    cancellation.cancel()

    outcome = run_task(python_task("pass"), cancellation=cancellation)

    assert not outcome.outcome.ran_to_completion
    assert outcome.parseable == ""


# --- cancelling a running tool -------------------------------------------


@posix_only
def test_cancelling_a_running_tool_reports_a_cancellation_not_a_signal() -> None:
    # A cancelled process dies by SIGTERM or SIGKILL, so the naive reading is
    # "killed by signal 15" -- a crash report for something we did on purpose.
    cancellation = Cancellation()
    threading.Timer(0.3, cancellation.cancel, args=("consumer went away",)).start()

    started = time.monotonic()
    outcome = run_task(python_task(SLEEPER, timeout=60.0), cancellation=cancellation)

    assert outcome.outcome is Outcome.CANCELLED
    assert outcome.outcome is not Outcome.SIGNALLED
    assert "consumer went away" in outcome.detail
    assert time.monotonic() - started < 30.0, "the cancellation did not stop the run"


@posix_only
@needs_proc
def test_cancelling_a_tool_leaves_none_of_its_children_running() -> None:
    cancellation = Cancellation()
    spec = python_task(
        "import subprocess, sys, time\n"
        "for _ in range(3):\n"
        "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n",
        timeout=60.0,
    )
    threading.Timer(1.0, cancellation.cancel, args=("stop the batch",)).start()

    outcome = run_task(spec, cancellation=cancellation)

    assert outcome.outcome is Outcome.CANCELLED
    assert outcome.cleanup is not None
    assert outcome.cleanup.verified, "nobody checked what was left running"
    assert outcome.cleaned_up, f"children survived: {outcome.cleanup.survivors}"
    assert members_of(outcome.cleanup.group or 0) == frozenset()


@posix_only
@needs_proc
def test_cancelling_a_supervisor_does_not_lose_the_race_with_its_replacements() -> None:
    cancellation = Cancellation()
    spec = python_task(
        "import subprocess, sys\n"
        "while True:\n"
        "    subprocess.Popen(\n"
        "        [sys.executable, '-c', 'import time; time.sleep(0.2)']\n"
        "    ).wait()\n",
        timeout=60.0,
    )
    threading.Timer(1.0, cancellation.cancel, args=("stop respawning",)).start()

    outcome = run_task(spec, cancellation=cancellation)

    assert outcome.outcome is Outcome.CANCELLED
    assert outcome.cleaned_up, f"a replacement survived: {outcome.cleanup}"


@posix_only
def test_a_tool_that_ignores_sigterm_is_still_stopped_by_a_cancellation() -> None:
    cancellation = Cancellation()
    spec = python_task(
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n",
        timeout=60.0,
        grace=0.5,
    )
    threading.Timer(0.5, cancellation.cancel, args=("deaf tool",)).start()

    started = time.monotonic()
    outcome = run_task(spec, cancellation=cancellation)

    assert outcome.outcome is Outcome.CANCELLED
    assert outcome.cleanup is not None and outcome.cleanup.escalated
    assert time.monotonic() - started < 30.0


# --- a cancellation that arrives too late --------------------------------


def test_a_tool_that_already_finished_keeps_its_answer() -> None:
    # The race is decided by what was true, not by who called first: if the
    # leader had already exited, we did not stop anything, and throwing away a
    # real result would be inventing a cancellation that never happened.
    cancellation = Cancellation()
    spec = python_task("print('done')")

    outcome = run_task(spec, cancellation=cancellation)
    cancellation.cancel("too late")

    assert outcome.outcome is Outcome.FINISHED
    assert outcome.exit_code == 0
    assert outcome.parseable.strip() == "done"


# --- an upstream consumer giving up --------------------------------------


@posix_only
def test_a_consumer_that_stops_waiting_cancels_from_its_own_thread() -> None:
    # #205 asks for the upstream-consumer case: the thread that would notice
    # the cancellation is the one blocked inside the runner, so it cannot.
    cancellation = Cancellation()
    result: dict[str, TaskOutcome] = {}

    def worker() -> None:
        result["outcome"] = run_task(python_task(SLEEPER, timeout=60.0), cancellation=cancellation)

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.5)
    cancellation.cancel("the consumer stopped reading")
    thread.join(timeout=30.0)

    assert not thread.is_alive(), "the run outlived the consumer that wanted it"
    assert result["outcome"].outcome is Outcome.CANCELLED


# --- Ctrl-C ---------------------------------------------------------------


@posix_only
def test_an_interrupt_cancels_the_run_and_the_handler_is_put_back() -> None:
    before = signal.getsignal(signal.SIGINT)
    cancellation = Cancellation()

    with signal_cancels(cancellation, signals=(signal.SIGINT,)):
        assert signal.getsignal(signal.SIGINT) is not before
        threading.Timer(0.3, os.kill, args=(os.getpid(), signal.SIGINT)).start()
        outcome = run_task(python_task(SLEEPER, timeout=60.0), cancellation=cancellation)

    assert signal.getsignal(signal.SIGINT) is before, "the handler was left installed"
    assert outcome.outcome is Outcome.CANCELLED
    assert "SIGINT" in outcome.detail


def test_a_worker_thread_that_cannot_catch_signals_is_not_an_error() -> None:
    cancellation = Cancellation()
    seen: list[bool] = []

    def worker() -> None:
        with signal_cancels(cancellation):
            seen.append(True)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10.0)

    assert seen == [True]


# --- the watchdog's own deadline -----------------------------------------


@posix_only
def test_the_watchdog_stops_a_process_the_runner_never_came_back_for() -> None:
    # #205's risk note asks for this: a separate watchdog for the case where
    # the timeout handling is itself what is stuck. Driven directly, because
    # the whole point is that the runner is not going to return.
    proc = subprocess.Popen(
        [sys.executable, "-c", SLEEPER],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    watchdog = Watchdog(cancellation=None, grace=0.5, limit=0.2)
    try:
        watchdog.attach(proc)
        deadline = time.monotonic() + 20.0
        while watchdog.cleanup is None and time.monotonic() < deadline:
            time.sleep(0.02)

        assert watchdog.trigger == Trigger.OVERRAN
        assert watchdog.cleanup is not None
        assert watchdog.cleanup.interrupted
        assert "did not return within" in watchdog.cleanup.reason
        assert proc.wait(timeout=10.0) != 0
    finally:
        watchdog.finish()
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()


def test_a_watchdog_that_never_saw_a_process_has_nothing_to_report() -> None:
    watchdog = Watchdog(cancellation=Cancellation(), grace=0.1, limit=0.1)
    watchdog.finish()

    assert watchdog.cleanup is None
    assert watchdog.trigger == Trigger.NONE


def test_a_run_that_was_never_stopped_is_not_reported_as_cleaned_up() -> None:
    # "Nothing needed cleaning" and "the tree was cleaned" are different facts,
    # and only one of them is evidence about a process tree.
    outcome = run_task(python_task("pass"))

    assert outcome.cleanup is None
    assert not outcome.cleaned_up


def test_an_unverified_cleanup_never_reads_as_cleaned_up() -> None:
    outcome = TaskOutcome(
        spec=python_task("pass"),
        outcome=Outcome.CANCELLED,
        exit_code=-9,
        cleanup=Cleanup(group=99, signalled=True, interrupted=True, survivors=None),
    )

    assert outcome.cleanup is not None and outcome.cleanup.signalled
    assert not outcome.cleaned_up


# --- a pipe nobody closed ------------------------------------------------


@posix_only
@needs_proc
def test_a_child_holding_the_pipe_open_does_not_hold_the_run_open_forever() -> None:
    # #205 names the pipe-wait case. The leader exits at once, but a
    # grandchild inherited its stdout, so the reader sees no end of file. The
    # run must still come back, must not be reported as a clean exit 0, and
    # must not leave the grandchild behind.
    spec = python_task(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('leader done', flush=True)\n",
        timeout=2.0,
    )

    started = time.monotonic()
    outcome = run_task(spec)
    elapsed = time.monotonic() - started

    assert elapsed < 30.0, "the run waited on a pipe nobody was going to close"
    assert not outcome.outcome.ran_to_completion, (
        "a leader whose output never ended was reported as having finished"
    )
    assert outcome.parseable == ""


@posix_only
@needs_proc
def test_cancelling_a_run_stuck_on_a_pipe_stops_the_child_holding_it() -> None:
    cancellation = Cancellation()
    spec = python_task(
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n",
        timeout=60.0,
    )
    threading.Timer(1.0, cancellation.cancel, args=("stop waiting",)).start()

    outcome = run_task(spec, cancellation=cancellation)

    assert outcome.outcome is Outcome.CANCELLED
    assert outcome.cleaned_up, f"the pipe holder survived: {outcome.cleanup}"


# --- the runner hook this is all built on --------------------------------


@posix_only
def test_the_runner_hands_over_a_process_that_is_still_running() -> None:
    # A hook called after the process has been waited on would be useless: the
    # whole point is to have something to stop.
    from ici.core.runner import run_process

    seen: list[int | None] = []

    def observe(proc: subprocess.Popen[bytes]) -> None:
        seen.append(proc.poll())

    run_process([sys.executable, "-c", "import time; time.sleep(0.3)"], started=observe)

    assert seen == [None]


@posix_only
def test_an_observer_that_fails_does_not_leave_a_process_behind() -> None:
    from ici.core.runner import run_process

    captured: list[subprocess.Popen[bytes]] = []

    def observe(proc: subprocess.Popen[bytes]) -> None:
        captured.append(proc)
        raise RuntimeError("the observer could not cope")

    run_process([sys.executable, "-c", SLEEPER], started=observe, timeout=30.0)

    assert captured, "the observer was never called"
    assert captured[0].poll() is not None, "the process outlived the failed observer"
