"""Asking a run to stop, as a fact rather than an exception.

Cancellation arrives from somewhere other than the code doing the waiting — a
Ctrl-C, a consumer that stopped needing the answer, a scheduler tearing a batch
down. So it is a value that is *set* and then *observed*, not an exception
raised into whichever thread happens to be looking.

The reason it is a value matters for #205's first acceptance criterion. A
cancellation delivered as an exception gets caught somewhere up the stack and
becomes "no findings", which is the empty PASS the criterion forbids. A
cancellation held as a fact travels with the result and stays readable after
the run is over, which is what a caller needs to tell "the tool found nothing"
from "we stopped the tool before it could say".
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from types import FrameType

__all__ = ["Cancellation", "signal_cancels"]


class Cancellation:
    """A request to stop, which outlives the moment it was made.

    Once requested it stays requested: a cancellation that could be taken back
    would let a run be reported as complete after something had already decided
    its answer was not wanted.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""

    def cancel(self, reason: str = "") -> None:
        """Request that the run stop. Only the first reason is kept."""

        with self._lock:
            if self._event.is_set():
                return
            self._reason = reason
            self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled, or until ``timeout`` passes. Never raises."""

        return self._event.wait(timeout)

    def __bool__(self) -> bool:
        return self.requested

    def __repr__(self) -> str:
        if not self.requested:
            return "Cancellation(pending)"
        return f"Cancellation(requested, reason={self._reason!r})"


@contextmanager
def signal_cancels(
    cancellation: Cancellation,
    signals: Sequence[int] = (signal.SIGINT, signal.SIGTERM),
) -> Iterator[Cancellation]:
    """Turn incoming signals into a cancellation for as long as the block runs.

    The handler does one thing — set a flag — because a signal handler runs
    between bytecodes in the main thread and anything more than that is done
    while the interpreter is in an arbitrary state. The killing is left to the
    watchdog thread, which is not standing in a signal handler.

    Handlers can only be installed from the main thread; asked from anywhere
    else this hands back the cancellation unchanged rather than failing, since
    a worker thread not being able to catch Ctrl-C is not an error in the run.
    """

    installed: dict[int, object] = {}
    if threading.current_thread() is threading.main_thread():
        for number in signals:

            def handler(signum: int, frame: FrameType | None) -> None:
                cancellation.cancel(f"received {signal.Signals(signum).name}")

            try:
                installed[number] = signal.signal(number, handler)
            except (OSError, ValueError):  # pragma: no cover - platform dependent
                continue
    try:
        yield cancellation
    finally:
        for number, previous in installed.items():
            with suppress(OSError, ValueError):  # pragma: no cover - platform dependent
                signal.signal(number, previous)  # type: ignore[arg-type]
