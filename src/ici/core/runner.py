"""Subprocess runner and execution helper for ici."""

import os
import signal
import subprocess
import time
from contextlib import suppress
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
        proc.kill()

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
        try:
            stdout, stderr = proc.communicate(
                input=input_text.encode("utf-8") if input_text is not None else None,
                timeout=timeout,
            )
            timed_out = False
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process(proc)
            stdout, stderr = proc.communicate()
            returncode = 124

        stdout_text = _decode_output(stdout)
        stderr_text = _decode_output(stderr)
        if timed_out:
            stderr_text = f"Command timed out after {timeout}s: {stderr_text}"

        stdout_text, stdout_truncated = _limit(stdout_text, max_output_chars)
        stderr_text, stderr_truncated = _limit(stderr_text, max_output_chars)
        return ProcessResult(
            returncode=returncode,
            stdout=stdout_text,
            stderr=stderr_text,
            duration=time.monotonic() - t0,
            timed_out=timed_out,
            truncated=stdout_truncated or stderr_truncated,
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
