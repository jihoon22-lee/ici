"""Holding a resource exclusively, in a way that cannot outlive the holder.

#205 item 4 asks for an exclusive resource lock, and item 3's criterion asks
whether the lock was *cleaned up* after a cancellation. Those two together rule
out the obvious implementation. A lock file created with ``O_EXCL`` is exclusive
and is trivially correct right up to the moment the holder is killed — which is
exactly what cancellation does — and then the file is still there and every
later run waits for a process that does not exist. **A lock that survives the
process that took it is worse than no lock at all**: no lock loses a race
sometimes, a stale lock blocks everyone forever.

So the exclusion is the operating system's, on an open descriptor. The kernel
drops it when the process goes, whether it exited, was terminated, or was
killed outright, and there is nothing to clean up because there was never any
state outside the descriptor. That is also what makes the criterion answerable:
a test can take the lock in a child, kill the child the way a cancellation
would, and watch somebody else take it.

Where neither mechanism is available this raises rather than carrying on
unlocked. Pretending to hold a lock is the failure the lock exists to prevent,
and #205's whole first criterion is about not reporting something as done when
it did not happen.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_LOCK_TIMEOUT", "Held", "LockUnavailable", "Unlocked", "exclusive"]

#: Long enough for another run's task to finish, short enough that a wedged
#: holder is reported rather than waited on forever.
DEFAULT_LOCK_TIMEOUT = 120.0

_POLL = 0.02

try:  # pragma: no cover - one branch per platform
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # pragma: no cover - one branch per platform
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


class LockUnavailable(RuntimeError):
    """No mechanism here can enforce a lock, so none is claimed."""


class Unlocked(TimeoutError):
    """Somebody else is holding it and did not let go in time."""


@dataclass(frozen=True)
class Held:
    """Proof that the lock is held, and by what.

    ``enforced`` is always true. It is here so a caller reads a fact rather
    than inferring one from the absence of an exception, and so that adding a
    best-effort mechanism later cannot quietly pass for this one.
    """

    path: Path
    waited: float
    enforced: bool = True


def _take(handle: int) -> bool:
    """Try once to take the lock on an open descriptor. False if it is taken."""

    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    if msvcrt is not None:  # pragma: no cover - Windows
        try:
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    raise LockUnavailable(  # pragma: no cover - neither module exists
        "no file-locking mechanism on this platform, and an unenforced lock is not a lock"
    )


def _release(handle: int) -> None:
    if fcntl is not None:
        with suppress(OSError):  # already gone with the descriptor
            fcntl.flock(handle, fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover - Windows
        with suppress(OSError):
            os.lseek(handle, 0, os.SEEK_SET)
            msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)


@contextmanager
def exclusive(path: Path, timeout: float = DEFAULT_LOCK_TIMEOUT) -> Iterator[Held]:
    """Hold ``path`` exclusively for the duration of the block.

    The file is left behind on purpose. It carries no state — it is only
    something to hold a descriptor open on — so finding one says nothing about
    whether anyone holds the lock, and deleting it would let a second holder
    create a *different* file with the same name and lock that instead.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    started = time.monotonic()
    try:
        deadline = started + max(0.0, timeout)
        while not _take(handle):
            if time.monotonic() >= deadline:
                raise Unlocked(f"{path} was held by another process for more than {timeout:g}s")
            time.sleep(_POLL)
        try:
            yield Held(path=path, waited=time.monotonic() - started)
        finally:
            _release(handle)
    finally:
        os.close(handle)
