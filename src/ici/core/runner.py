"""Subprocess runner and execution helper for ici."""

import ctypes
import logging
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ici.core.runner_win import (
    _CREATE_SUSPENDED,
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _RESUME_THREAD_FAILED,
    _get_windows_kernel32,
    _JobObjectExtendedLimitInformation,
    _open_suspended_process_thread,
    _process_handle,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ProcessResult:
    """Result of a bounded subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False
    truncated: bool = False


def _limit(text: str, maximum: int) -> tuple[str, bool]:
    """Limit captured output to ``maximum`` characters."""

    if len(text) <= maximum:
        return text, False
    return text[:maximum], True


def _decode_output(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


class _BoundedCapture:
    """Byte-oriented capture buffer that drains a stream without growing unbounded."""

    def __init__(self, maximum: int):
        self._maximum = maximum
        self._buffer = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        remaining = self._maximum - len(self._buffer)
        if remaining > 0:
            self._buffer.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            self.truncated = True

    def value(self) -> str:
        return _decode_output(bytes(self._buffer))


def _drain_stream(stream, capture: _BoundedCapture) -> None:
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                return
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", errors="replace")
            capture.append(chunk)
    except (OSError, ValueError):
        return
    finally:
        with suppress(OSError, ValueError):
            stream.close()


def _write_input(stream, input_bytes: bytes) -> None:
    try:
        stream.write(input_bytes)
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        return


def _start_output_readers(
    proc: subprocess.Popen[bytes], max_output_chars: int
) -> tuple[_BoundedCapture, _BoundedCapture, threading.Thread, threading.Thread]:
    if proc.stdout is None or proc.stderr is None:
        raise RuntimeError("subprocess pipes were not created")
    stdout_capture = _BoundedCapture(max_output_chars)
    stderr_capture = _BoundedCapture(max_output_chars)
    stdout_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stdout, stdout_capture),
        name="ici-process-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain_stream,
        args=(proc.stderr, stderr_capture),
        name="ici-process-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    return stdout_capture, stderr_capture, stdout_thread, stderr_thread


def _start_input_writer(
    proc: subprocess.Popen[bytes], input_text: str | None
) -> threading.Thread | None:
    if input_text is None or proc.stdin is None:
        return None
    input_thread = threading.Thread(
        target=_write_input,
        args=(proc.stdin, input_text.encode("utf-8")),
        name="ici-process-stdin",
        daemon=True,
    )
    input_thread.start()
    return input_thread


def _spawn_process(
    cmd: list[str],
    cwd: Path | None,
    exec_env: dict[str, str],
    input_text: str | None,
) -> subprocess.Popen[bytes]:
    """Start a binary-pipe process with platform-specific containment flags."""

    stdin = subprocess.PIPE if input_text is not None else None
    if os.name == "posix":
        return subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=exec_env,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    if os.name == "nt":
        return subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=exec_env,
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_SUSPENDED,
        )
    return subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=exec_env,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _start_windows_job(proc) -> None:
    """Attach a suspended Windows process to a kill-on-close Job, then resume it."""

    kernel32 = _get_windows_kernel32()
    job_handle = None
    thread_handle = None
    close_thread = False
    try:
        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            raise OSError("CreateJobObjectW failed")

        info = _JobObjectExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            raise OSError("SetInformationJobObject failed")

        process_handle = _process_handle(proc)
        if process_handle is None:
            raise OSError("could not obtain Windows process handle")
        thread_handle, close_thread = _open_suspended_process_thread(proc, kernel32)
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise OSError("AssignProcessToJobObject failed")
        if kernel32.ResumeThread(thread_handle) == _RESUME_THREAD_FAILED:
            raise OSError("ResumeThread failed")

        proc._ici_job_handle = job_handle
        proc._ici_job_kernel32 = kernel32
    except Exception:
        if close_thread and thread_handle:
            with suppress(OSError):
                kernel32.CloseHandle(thread_handle)
        if job_handle:
            with suppress(OSError):
                kernel32.TerminateJobObject(job_handle, 1)
            with suppress(OSError):
                kernel32.CloseHandle(job_handle)
        with suppress(OSError):
            proc.kill()
        _wait_after_startup_failure(proc)
        raise
    else:
        if close_thread and thread_handle:
            with suppress(OSError):
                kernel32.CloseHandle(thread_handle)


def _close_windows_job(proc) -> None:
    job_handle = getattr(proc, "_ici_job_handle", None)
    kernel32 = getattr(proc, "_ici_job_kernel32", None)
    if job_handle and kernel32 is not None:
        with suppress(OSError):
            kernel32.CloseHandle(job_handle)
    for attr in ("_ici_job_handle", "_ici_job_kernel32"):
        with suppress(AttributeError):
            delattr(proc, attr)


def _log_cleanup_failure(action: str, error: BaseException) -> None:
    _LOGGER.debug("%s during subprocess cleanup: %s", action, error)


def _wait_after_startup_failure(proc) -> None:
    try:
        proc.wait(timeout=1.0)
    except TypeError:
        try:
            proc.wait()
        except OSError as error:
            _log_cleanup_failure("waiting for startup failure", error)
    except (OSError, subprocess.TimeoutExpired) as error:
        _log_cleanup_failure("waiting for startup failure", error)


def _remaining(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _wait_process(proc, deadline: float | None = None) -> None:
    remaining = _remaining(deadline)
    if remaining is not None and remaining <= 0:
        return
    try:
        proc.wait(timeout=remaining) if remaining is not None else proc.wait()
    except TypeError:
        # Small fakes used by the platform orchestration tests expose wait()
        # without subprocess' optional timeout parameter.
        if remaining is None:
            with suppress(OSError):
                proc.wait()
    except (OSError, subprocess.TimeoutExpired):
        return


def _terminate_windows_process(proc, deadline: float | None = None) -> None:
    """Terminate a Windows process tree through its startup-attached Job Object."""

    kernel32 = None
    job_handle = None
    attached_kernel32 = getattr(proc, "_ici_job_kernel32", None)
    attached_job = getattr(proc, "_ici_job_handle", None)
    if attached_job and attached_kernel32 is not None:
        try:
            if not attached_kernel32.TerminateJobObject(attached_job, 1):
                raise OSError("TerminateJobObject failed")
        except Exception:
            with suppress(OSError):
                proc.kill()
        finally:
            _close_windows_job(proc)
        _wait_process(proc, deadline)
        return

    try:
        kernel32 = _get_windows_kernel32()
        job_handle = kernel32.CreateJobObjectW(None, None)
        process_handle = _process_handle(proc)
        if not job_handle or process_handle is None:
            raise OSError("could not obtain Windows process or job handle")
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise OSError("AssignProcessToJobObject failed")
        if not kernel32.TerminateJobObject(job_handle, 1):
            raise OSError("TerminateJobObject failed")
    except Exception:
        # Job creation/assignment can fail for processes already in a job.
        # Preserve the old single-process fallback instead of leaking a handle.
        with suppress(OSError):
            proc.kill()
    finally:
        if kernel32 is not None and job_handle:
            with suppress(OSError):
                kernel32.CloseHandle(job_handle)
    _wait_process(proc, deadline)


def _terminate_process(proc: subprocess.Popen[bytes], deadline: float | None = None) -> None:
    """Terminate a process and its POSIX process group when possible."""

    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError as error:
            _log_cleanup_failure("SIGTERM process-group cleanup", error)
        except OSError:
            proc.terminate()

        grace = _remaining(deadline)
        grace = 0.2 if grace is None else min(grace, 0.2)
        if grace > 0:
            with suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=grace)

        # A child can exit on SIGTERM while descendants keep the pipe open.
        # Kill the group after the grace period so communicate() cannot hang.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError as error:
            _log_cleanup_failure("SIGKILL process-group cleanup", error)
        except OSError:
            proc.kill()
    else:
        _terminate_windows_process(proc, deadline)

    _wait_process(proc, deadline)


def _wait_for_process(proc: subprocess.Popen[bytes], deadline: float | None) -> bool:
    try:
        proc.wait(timeout=_remaining(deadline))
    except subprocess.TimeoutExpired:
        _terminate_process(proc, deadline)
        return True
    return False


def _join_until_deadline(thread: threading.Thread, deadline: float | None) -> bool:
    thread.join(timeout=_remaining(deadline))
    return thread.is_alive()


def _close_stream(stream: IO[bytes] | None) -> None:
    if stream is not None:
        with suppress(OSError, ValueError):
            stream.close()


def _close_process_pipes(proc: subprocess.Popen[bytes]) -> None:
    _close_stream(proc.stdin)
    _close_stream(proc.stdout)
    _close_stream(proc.stderr)


def _cleanup_process_threads(
    proc: subprocess.Popen[bytes],
    input_thread: threading.Thread | None,
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
    deadline: float | None,
    timed_out: bool,
) -> bool:
    if input_thread is not None and _join_until_deadline(input_thread, deadline):
        timed_out = True
        _terminate_process(proc, deadline)

    if _join_until_deadline(stdout_thread, deadline) or _join_until_deadline(
        stderr_thread, deadline
    ):
        timed_out = True
        # The leader may have exited while a descendant retained an inherited
        # pipe. Kill the group/job even in that case.
        _terminate_process(proc, deadline)

    if timed_out and (stdout_thread.is_alive() or stderr_thread.is_alive()):
        _close_stream(proc.stdout)
        _close_stream(proc.stderr)
        stdout_thread.join(timeout=_remaining(deadline))
        stderr_thread.join(timeout=_remaining(deadline))

    if input_thread is not None and input_thread.is_alive():
        _close_stream(proc.stdin)
    return timed_out


def _make_process_result(
    proc: subprocess.Popen[bytes],
    stdout_capture: _BoundedCapture,
    stderr_capture: _BoundedCapture,
    t0: float,
    timeout: float | None,
    timed_out: bool,
    max_output_chars: int,
) -> ProcessResult:
    stdout_text = stdout_capture.value()
    stderr_text = stderr_capture.value()
    if timed_out:
        stderr_text = f"Command timed out after {timeout}s: {stderr_text}"

    stdout_text, stdout_truncated = _limit(stdout_text, max_output_chars)
    stderr_text, stderr_truncated = _limit(stderr_text, max_output_chars)
    return ProcessResult(
        returncode=124 if timed_out else proc.returncode,
        stdout=stdout_text,
        stderr=stderr_text,
        duration=time.monotonic() - t0,
        timed_out=timed_out,
        truncated=(
            stdout_capture.truncated
            or stderr_capture.truncated
            or stdout_truncated
            or stderr_truncated
        ),
    )


def _cleanup_failed_process(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    if os.name == "nt":
        _close_windows_job(proc)
    _close_process_pipes(proc)


def run_process(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = 300.0,
    input_text: str | None = None,
    max_output_chars: int = 1_000_000,
) -> ProcessResult:
    """Run an argv command with bounded output and safe timeout handling.

    Commands are always passed directly to ``Popen``; no shell is involved.
    On POSIX, the child starts a new process group so timeout cleanup also
    terminates descendants.  On Windows, a suspended child is assigned to a
    kill-on-close Job Object before its primary thread is resumed.
    """

    if max_output_chars < 0:
        raise ValueError("max_output_chars must be non-negative")

    exec_env = os.environ.copy()
    if env:
        exec_env.update(env)

    t0 = time.monotonic()
    deadline = None if timeout is None else t0 + max(0.0, timeout)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = _spawn_process(cmd, cwd, exec_env, input_text)
        if os.name == "nt":
            _start_windows_job(proc)
        stdout_capture, stderr_capture, stdout_thread, stderr_thread = _start_output_readers(
            proc, max_output_chars
        )
        input_thread = _start_input_writer(proc, input_text)
        timed_out = _wait_for_process(proc, deadline)
        timed_out = _cleanup_process_threads(
            proc,
            input_thread,
            stdout_thread,
            stderr_thread,
            deadline,
            timed_out,
        )

        if os.name == "nt":
            _close_windows_job(proc)
        return _make_process_result(
            proc,
            stdout_capture,
            stderr_capture,
            t0,
            timeout,
            timed_out,
            max_output_chars,
        )
    except Exception as exc:
        _cleanup_failed_process(proc)
        stderr_text, truncated = _limit(f"Failed to execute {cmd}: {exc}", max_output_chars)
        return ProcessResult(
            returncode=-1,
            stdout="",
            stderr=stderr_text,
            duration=time.monotonic() - t0,
            truncated=truncated,
        )
