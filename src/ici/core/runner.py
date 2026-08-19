"""Subprocess runner and execution helper for ici."""

import ctypes
import os
import signal
import subprocess
import threading
import time
from contextlib import suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


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


def _get_windows_kernel32():
    """Load the Windows kernel32 API used for process-tree termination."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _terminate_windows_process(proc) -> None:
    """Terminate a Windows process tree through a Job Object."""

    kernel32 = None
    job_handle = None
    try:
        kernel32 = _get_windows_kernel32()
        job_handle = kernel32.CreateJobObjectW(None, None)
        process_handle = getattr(proc, "_handle", None)
        if process_handle is None:
            process_handle = getattr(proc, "handle", None)
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
    with suppress(OSError):
        proc.wait()


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """Terminate a process and its POSIX process group when possible."""

    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            proc.terminate()

        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=0.2)

        # A child can exit on SIGTERM while descendants keep the pipe open.
        # Kill the group after the grace period so communicate() cannot hang.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            proc.kill()
    else:
        _terminate_windows_process(proc)

    with suppress(OSError):
        proc.wait()


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
    terminates descendants.  Windows uses the process ``kill`` fallback.
    """

    if max_output_chars < 0:
        raise ValueError("max_output_chars must be non-negative")

    exec_env = os.environ.copy()
    if env:
        exec_env.update(env)

    t0 = time.monotonic()
    popen_kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd else None,
        "env": exec_env,
        "stdin": subprocess.PIPE if input_text is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
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

        input_thread = None
        if input_text is not None and proc.stdin is not None:
            input_thread = threading.Thread(
                target=_write_input,
                args=(proc.stdin, input_text.encode("utf-8")),
                name="ici-process-stdin",
                daemon=True,
            )
            input_thread.start()

        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process(proc)

        if input_thread is not None:
            input_thread.join(timeout=1.0)
        join_timeout = 1.0 if timed_out else None
        stdout_thread.join(timeout=join_timeout)
        stderr_thread.join(timeout=join_timeout)
        if timed_out and (stdout_thread.is_alive() or stderr_thread.is_alive()):
            # Closing a pipe is a final no-deadlock guard for an escaped child.
            with suppress(OSError, ValueError):
                proc.stdout.close()
            with suppress(OSError, ValueError):
                proc.stderr.close()
            stdout_thread.join(timeout=0.5)
            stderr_thread.join(timeout=0.5)

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
    except Exception as exc:
        stderr_text, truncated = _limit(f"Failed to execute {cmd}: {exc}", max_output_chars)
        return ProcessResult(
            returncode=-1,
            stdout="",
            stderr=stderr_text,
            duration=time.monotonic() - t0,
            truncated=truncated,
        )
