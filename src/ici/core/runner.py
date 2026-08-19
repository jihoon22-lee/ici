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


_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_THREAD_SUSPEND_RESUME = 0x0002
_TH32CS_SNAPTHREAD = 0x00000004
_RESUME_THREAD_FAILED = 0xFFFFFFFF


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
    ]


def _get_windows_kernel32():
    """Load the Windows kernel32 APIs used for process-tree management."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry32)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _process_handle(proc):
    process_handle = getattr(proc, "_handle", None)
    if process_handle is None:
        process_handle = getattr(proc, "handle", None)
    return process_handle


def _thread_handle(proc):
    thread_handle = getattr(proc, "_thread_handle", None)
    if thread_handle is None:
        thread_handle = getattr(proc, "_thread", None)
    return thread_handle


def _invalid_windows_handle(handle) -> bool:
    value = handle.value if isinstance(handle, ctypes.c_void_p) else handle
    return value in (None, 0, ctypes.c_void_p(-1).value)


def _open_suspended_process_thread(proc, kernel32):
    """Open the only runnable thread while a CREATE_SUSPENDED process is stopped.

    CPython closes the primary thread handle before returning ``Popen``.  The
    process is still suspended, so enumerating its threads and opening the
    primary one is race-free: no user code can create another thread until the
    Job Object is assigned and this handle is resumed.
    """

    existing = _thread_handle(proc)
    if existing is not None:
        return existing, False

    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if _invalid_windows_handle(snapshot):
        raise OSError("CreateToolhelp32Snapshot failed")
    thread = None
    try:
        entry = _ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == proc.pid:
                thread = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if not thread:
                    raise OSError("OpenThread failed")
                return thread, True
            found = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        with suppress(OSError):
            kernel32.CloseHandle(snapshot)
    raise OSError("suspended process thread was not found")


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
        try:
            proc.wait(timeout=1.0)
        except TypeError:
            with suppress(OSError):
                proc.wait()
        except (OSError, subprocess.TimeoutExpired):
            pass
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
        except ProcessLookupError:
            pass
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
        except ProcessLookupError:
            pass
        except OSError:
            proc.kill()
    else:
        _terminate_windows_process(proc, deadline)

    _wait_process(proc, deadline)


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
    popen_kwargs: dict[str, object] = {
        "cwd": str(cwd) if cwd else None,
        "env": exec_env,
        "stdin": subprocess.PIPE if input_text is not None else None,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif os.name == "nt":
        popen_kwargs["creationflags"] = _CREATE_SUSPENDED

    proc = None
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
        if os.name == "nt":
            _start_windows_job(proc)
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
            remaining = _remaining(deadline)
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process(proc, deadline)

        def join_until_deadline(thread) -> bool:
            thread.join(timeout=_remaining(deadline))
            return thread.is_alive()

        if input_thread is not None and join_until_deadline(input_thread):
            timed_out = True
            _terminate_process(proc, deadline)

        if join_until_deadline(stdout_thread) or join_until_deadline(stderr_thread):
            timed_out = True
            # The leader may have exited while a descendant retained an
            # inherited pipe.  Kill the group/job even in that case.
            _terminate_process(proc, deadline)

        if timed_out and (stdout_thread.is_alive() or stderr_thread.is_alive()):
            # Closing a pipe is a final no-deadlock guard for an escaped child.
            with suppress(OSError, ValueError):
                proc.stdout.close()
            with suppress(OSError, ValueError):
                proc.stderr.close()
            stdout_thread.join(timeout=_remaining(deadline))
            stderr_thread.join(timeout=_remaining(deadline))

        if input_thread is not None and input_thread.is_alive():
            with suppress(OSError, ValueError):
                proc.stdin.close()

        if os.name == "nt":
            _close_windows_job(proc)

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
        if proc is not None:
            if os.name == "nt":
                _close_windows_job(proc)
            for stream in (
                getattr(proc, "stdin", None),
                getattr(proc, "stdout", None),
                getattr(proc, "stderr", None),
            ):
                with suppress(OSError, ValueError, AttributeError):
                    stream.close()
        stderr_text, truncated = _limit(f"Failed to execute {cmd}: {exc}", max_output_chars)
        return ProcessResult(
            returncode=-1,
            stdout="",
            stderr=stderr_text,
            duration=time.monotonic() - t0,
            truncated=truncated,
        )
