"""Stopping a process tree, and saying whether we could tell that it stopped.

#205's third acceptance criterion is to *verify* that children, locks and
temporary files were cleaned up after a cancellation. The word doing the work
is "verify". A cleanup routine that returns ``True`` because it ran without
raising is the same empty PASS the first criterion forbids, one layer down:
nothing was checked, and the absence of an error was read as the absence of
survivors.

So the result of stopping a tree is evidence, and it has three states, not two:
we looked and nothing is left, we looked and something is left, and **we could
not look**. Only the first is clean. :attr:`Cleanup.survivors` is ``None`` for
the third, and ``is_clean`` is false for it, so a platform where the group
cannot be inspected reports "unverified" instead of quietly reporting success.

Killing the group rather than the leader is what handles the case #205 names as
"자식 재생성" — a supervisor that starts a replacement whenever its child dies.
Signalling pids one at a time is a race against a process whose whole job is to
create more of them; signalling the group reaches the replacement too, because
a child inherits its parent's process group.
"""

from __future__ import annotations

import errno
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_GRACE",
    "Cleanup",
    "can_inspect_groups",
    "group_of",
    "members_of",
    "signal_group",
    "stop_group",
]

DEFAULT_GRACE = 2.0

_PROC = Path("/proc")


@dataclass(frozen=True)
class Cleanup:
    """What was done to a process tree and what was still there afterwards.

    ``survivors`` is ``None`` when this platform gives us no way to enumerate a
    process group. That is deliberately not an empty set: "nothing survived"
    and "we cannot see whether anything survived" are different answers, and
    only one of them means the cleanup worked.
    """

    group: int | None = None
    reason: str = ""
    #: Whether a signal was actually delivered to the group.
    signalled: bool = False
    #: Whether we stopped something that was still running. This asks about
    #: the whole group, not the leader: a tool whose leader has exited while a
    #: child still holds its output open has not finished saying what it
    #: found, and reading the leader's exit code there is how a cancelled run
    #: turns back into a clean pass.
    interrupted: bool = False
    #: Whether the grace period expired and the group had to be killed.
    escalated: bool = False
    survivors: frozenset[int] | None = None
    duration: float = 0.0

    @property
    def verified(self) -> bool:
        """Whether anybody actually looked at what was left."""

        return self.survivors is not None

    @property
    def is_clean(self) -> bool:
        """True only when we looked *and* found nothing running."""

        return self.survivors is not None and not self.survivors

    def __str__(self) -> str:
        if not self.signalled:
            return "no process tree was stopped"
        what = "killed" if self.escalated else "terminated"
        if not self.verified:
            return f"{what} process group {self.group}; survivors not verified"
        if self.survivors:
            listed = ", ".join(str(pid) for pid in sorted(self.survivors or ()))
            return f"{what} process group {self.group}; still running: {listed}"
        return f"{what} process group {self.group}; nothing left running"


def can_inspect_groups() -> bool:
    """Whether process groups can be enumerated here (Linux ``/proc``)."""

    return os.name == "posix" and _PROC.is_dir()


def group_of(pid: int) -> int | None:
    """The process group a pid belongs to, or None if it is already gone."""

    if not hasattr(os, "getpgid"):
        return None
    try:
        return os.getpgid(pid)
    except (OSError, AttributeError):
        return None


def _group_from_stat(text: str) -> int | None:
    # The second field is the executable name in parentheses and may itself
    # contain spaces and parentheses, so the fields after it are found from the
    # last ") " rather than by splitting the whole line.
    _, _, tail = text.rpartition(") ")
    fields = tail.split()
    if len(fields) < 3:
        return None
    # state, ppid, pgrp
    if fields[0] == "Z":
        # A zombie holds no resources and cannot spawn anything; counting one as
        # a survivor would report every cleanup as having failed.
        return None
    try:
        return int(fields[2])
    except ValueError:
        return None


def members_of(group: int) -> frozenset[int] | None:
    """Live pids currently in ``group``, or None where we cannot tell."""

    if not can_inspect_groups():
        return None
    found: set[int] = set()
    try:
        entries = list(_PROC.iterdir())
    except OSError:  # pragma: no cover - /proc disappearing under us
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            text = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            # The process exited between listing and reading. That is the
            # normal case for something we have just signalled.
            continue
        if _group_from_stat(text) == group:
            found.add(int(entry.name))
    return frozenset(found)


def signal_group(group: int, number: int) -> bool:
    """Send a signal to a whole process group. True if it was delivered."""

    if group <= 0:
        raise ValueError(f"{group} is not a process group")
    if hasattr(os, "getpgrp") and group == os.getpgrp():
        # ici's own group contains ici. Signalling it would take down the
        # process doing the cleanup, and in a test run, the test runner.
        raise ValueError("refusing to signal ici's own process group")
    if not hasattr(os, "killpg"):
        return False
    try:
        os.killpg(group, number)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - depends on privileges
        return False
    except OSError as error:  # pragma: no cover - platform dependent
        if error.errno == errno.ESRCH:
            return False
        raise
    return True


def _wait_for_exit(group: int, deadline: float) -> frozenset[int] | None:
    """Poll until the group empties or the deadline passes."""

    remaining = members_of(group)
    if remaining is None:
        return None
    while remaining and time.monotonic() < deadline:
        time.sleep(0.02)
        remaining = members_of(group)
        if remaining is None:  # pragma: no cover - /proc vanished mid-wait
            return None
    return remaining


def stop_group(group: int, grace: float = DEFAULT_GRACE, reason: str = "") -> Cleanup:
    """Terminate a process group, escalate after ``grace``, report what is left.

    SIGTERM first, because a tool that is given the chance to exit cleanly
    removes its own temporary files. SIGKILL after the grace period, because a
    tool that ignores SIGTERM must not be able to outlast the cancellation that
    asked it to stop.
    """

    started = time.monotonic()
    # Read this before signalling: afterwards there is no telling whether an
    # empty group means we stopped something or that there was never anything
    # there. Where the group cannot be enumerated, delivery is the best
    # evidence available.
    before = members_of(group)
    delivered = signal_group(group, signal.SIGTERM)
    interrupted = delivered and (before is None or bool(before))
    if not delivered:
        # Nothing there to signal. Still report what we can see, so a caller is
        # not told "clean" on a platform that never looked.
        return Cleanup(
            group=group,
            reason=reason,
            signalled=False,
            interrupted=False,
            survivors=members_of(group),
            duration=time.monotonic() - started,
        )

    remaining = _wait_for_exit(group, started + max(0.0, grace))
    escalated = False
    if remaining is None or remaining:
        # Either something is still running, or we cannot see whether anything
        # is. Both are reasons to kill: an unverifiable group left alive is how
        # a cancelled build keeps holding its lock.
        escalated = signal_group(group, signal.SIGKILL)
        remaining = _wait_for_exit(group, time.monotonic() + max(0.0, grace))

    return Cleanup(
        group=group,
        reason=reason,
        signalled=True,
        interrupted=interrupted,
        escalated=escalated,
        survivors=remaining,
        duration=time.monotonic() - started,
    )
