"""The thread that stops a run when the run will not stop itself.

Two different things need something outside the waiting thread to act.

A **cancellation** arrives while the caller is blocked inside the runner, so
the thread that would notice it is the one thread that cannot. And #205's own
risk note asks for the second: *"test harness가 멈추면 별도 watchdog과 강제
정리를 둔다"* — a hard deadline past the task's own timeout, for the case where
the timeout handling is itself what is stuck. A wait with no upper bound is how
a hung child becomes a hung CI job.

Both end the same way, so one thread does both: signal the process group,
escalate after the grace period, and record what was left behind.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import suppress

from ici.execution import tree
from ici.execution.cancellation import Cancellation

__all__ = ["WATCHDOG_MARGIN", "Trigger", "Watchdog"]

#: How long after a task's own timeout the watchdog stops waiting for the
#: runner to come back on its own.
WATCHDOG_MARGIN = 5.0

_TICK = 0.02


class Trigger:
    """Why the watchdog acted. Not an enum: these are only ever compared here."""

    NONE = ""
    CANCELLED = "cancelled"
    OVERRAN = "overran"


class Watchdog:
    """Watches one run, and can stop it from outside the thread that started it."""

    def __init__(
        self,
        cancellation: Cancellation | None = None,
        grace: float = tree.DEFAULT_GRACE,
        limit: float | None = None,
    ) -> None:
        self._cancellation = cancellation
        self._grace = grace
        self._limit = limit
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._group: int | None = None
        self._deadline: float | None = None
        self._lock = threading.Lock()
        self._cleanup: tree.Cleanup | None = None
        self._trigger = Trigger.NONE

    def attach(self, proc: subprocess.Popen[bytes]) -> None:
        """Start watching a live process. Suitable as ``run_process(started=...)``."""

        self._proc = proc
        self._group = _own_group_of(proc)
        self._deadline = None if self._limit is None else time.monotonic() + self._limit
        self._thread = threading.Thread(target=self._watch, name="ici-task-watchdog", daemon=True)
        self._thread.start()

    def finish(self) -> None:
        """Stop watching. Safe to call when nothing was ever attached."""

        self._finished.set()
        thread = self._thread
        if thread is not None:
            # Bounded: the watchdog's own work is bounded by the grace period,
            # and a join without a limit would reintroduce the hang this class
            # exists to prevent.
            thread.join(timeout=self._grace * 2 + WATCHDOG_MARGIN)

    @property
    def cleanup(self) -> tree.Cleanup | None:
        """What stopping the tree found, or None if the run was never stopped."""

        with self._lock:
            return self._cleanup

    @property
    def trigger(self) -> str:
        with self._lock:
            return self._trigger

    def _watch(self) -> None:
        while not self._finished.wait(_TICK):
            if self._cancellation is not None and self._cancellation.requested:
                self._stop(Trigger.CANCELLED, self._cancellation.reason or "cancelled")
                return
            if self._deadline is not None and time.monotonic() >= self._deadline:
                self._stop(Trigger.OVERRAN, f"the run did not return within {self._limit:g}s")
                return

    def _stop(self, trigger: str, reason: str) -> None:
        group = self._group
        if group is None:
            cleanup = _stop_one(self._proc, reason)
        else:
            cleanup = tree.stop_group(group, self._grace, reason=reason)
        with self._lock:
            self._cleanup = cleanup
            self._trigger = trigger


def _own_group_of(proc: subprocess.Popen[bytes]) -> int | None:
    """The child's process group, but only when it is genuinely its own.

    A child that shares ici's group cannot be stopped by group, because doing
    so would stop ici. That is not a failure to report later: it changes what
    the watchdog is allowed to do now.
    """

    group = tree.group_of(proc.pid)
    if group is None:
        return None
    if hasattr(os, "getpgrp") and group == os.getpgrp():
        return None
    return group


def _stop_one(proc: subprocess.Popen[bytes] | None, reason: str) -> tree.Cleanup:
    """Kill a single process where there is no group to work with.

    ``survivors`` stays None on purpose. Killing the process we know about says
    nothing about the children it started, and claiming otherwise would report
    a tree as cleaned up on the strength of not having looked at it.
    """

    if proc is None:
        return tree.Cleanup(reason=reason)
    running = proc.poll() is None
    if running:
        with suppress(OSError):
            proc.kill()
    return tree.Cleanup(
        reason=reason,
        signalled=running,
        interrupted=running,
        escalated=running,
        survivors=None,
    )
