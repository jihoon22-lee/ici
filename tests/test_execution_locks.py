"""An exclusive lock, and the question #205 actually asks about it.

Item 3's criterion is *"취소 후 자식 process·lock·temporary 파일 정리 여부를
확인한다"* — after a cancellation, was the lock cleaned up? That question is what
rules out a lock file created with O_EXCL: it is exclusive and correct right up
until the holder is killed, which is what a cancellation does, and then nothing
ever takes the lock again.

So the test that matters here kills a holder the way a cancellation would and
watches somebody else take it. Nothing is cleaned up, because there was never
anything to clean up.
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

from ici.execution.locks import Held, Unlocked, exclusive

HOLDER = """
import sys, time
sys.path.insert(0, {source!r})
from pathlib import Path
from ici.execution.locks import exclusive

with exclusive(Path({lock!r})) as held:
    print("held", flush=True)
    time.sleep(120)
"""


def _holder(lock: Path) -> subprocess.Popen[str]:
    source = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER.format(source=source, lock=str(lock))],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held", "the holder never took the lock"
    return proc


def _reap(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
    if proc.stdout is not None:
        proc.stdout.close()
    proc.wait(timeout=10)


# --- it excludes ----------------------------------------------------------


def test_a_lock_is_held_for_the_block_and_let_go_after(tmp_path: Path) -> None:
    lock = tmp_path / "build.lock"
    with exclusive(lock) as held:
        assert isinstance(held, Held)
        assert held.enforced
        assert held.path == lock

    with exclusive(lock, timeout=1.0):
        pass


def test_another_process_cannot_take_a_held_lock(tmp_path: Path) -> None:
    lock = tmp_path / "build.lock"
    proc = _holder(lock)
    try:
        started = time.monotonic()
        with pytest.raises(Unlocked, match="held by another process"), exclusive(lock, timeout=0.5):
            pass
        assert time.monotonic() - started >= 0.4, "it gave up without waiting"
    finally:
        _reap(proc)


def test_waiting_gets_the_lock_once_the_holder_lets_go(tmp_path: Path) -> None:
    lock = tmp_path / "build.lock"
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with exclusive(lock):
            holding.set()
            release.wait(30)

    # Same process, so this is only about the waiting: flock is per open file
    # description, and each `exclusive` opens its own.
    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holding.wait(10)
        threading.Timer(0.3, release.set).start()
        with exclusive(lock, timeout=30.0) as held:
            assert held.waited >= 0.2, "it did not actually wait for the holder"
    finally:
        release.set()
        thread.join(timeout=30)


# --- the criterion: what a cancellation leaves behind ----------------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGKILL])
def test_killing_the_holder_releases_the_lock(tmp_path: Path, number: int) -> None:
    # SIGKILL is the case that decides the design: a process killed outright
    # runs no cleanup of its own. If the exclusion were a file the holder was
    # supposed to delete, this is where every later run starts waiting forever.
    lock = tmp_path / "build.lock"
    proc = _holder(lock)
    try:
        with pytest.raises(Unlocked), exclusive(lock, timeout=0.2):
            pass

        os.kill(proc.pid, number)
        proc.wait(timeout=30)

        with exclusive(lock, timeout=30.0) as held:
            assert held.enforced
    finally:
        _reap(proc)


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals")
def test_the_lock_file_left_behind_does_not_mean_the_lock_is_held(tmp_path: Path) -> None:
    # The file carries no state. Finding one says nothing, which is why nothing
    # deletes it: a second holder creating a *different* file with the same
    # name would lock that one instead, and both would think they had it.
    lock = tmp_path / "build.lock"
    proc = _holder(lock)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=30)
    _reap(proc)

    assert lock.exists(), "the file is expected to stay"
    with exclusive(lock, timeout=30.0):
        pass


# --- the lock is a real one -----------------------------------------------


def test_a_lock_in_a_directory_that_does_not_exist_yet_is_made(tmp_path: Path) -> None:
    with exclusive(tmp_path / "a" / "b" / "build.lock") as held:
        assert held.path.parent.is_dir()


def test_a_lock_reports_that_it_is_enforced_rather_than_leaving_it_to_be_assumed(
    tmp_path: Path,
) -> None:
    # A caller reading a fact instead of inferring one from the absence of an
    # exception is what stops a best-effort mechanism added later from quietly
    # passing for this one.
    with exclusive(tmp_path / "build.lock") as held:
        assert held.enforced is True
