"""Subprocess runner and execution helper for ici."""

import os
import subprocess
import time
from pathlib import Path


def run_process(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    input_text: str | None = None,
) -> tuple[int, str, str, float]:
    """Runs a subprocess command safely with timeout and timing metrics.

    Returns:
        (returncode, stdout, stderr, duration_seconds)
    """
    exec_env = os.environ.copy()
    if env:
        exec_env.update(env)

    t0 = time.time()
    try:
        res = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=exec_env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            input=input_text,
        )
        duration = time.time() - t0
        return (res.returncode, res.stdout, res.stderr, duration)
    except subprocess.TimeoutExpired as e:
        duration = time.time() - t0
        stdout = (
            e.stdout.decode("utf-8", errors="replace")
            if isinstance(e.stdout, bytes)
            else (e.stdout or "")
        )
        stderr = (
            e.stderr.decode("utf-8", errors="replace")
            if isinstance(e.stderr, bytes)
            else (e.stderr or "")
        )
        return (-1, stdout, f"Command timed out after {timeout}s: {stderr}", duration)
    except Exception as e:
        duration = time.time() - t0
        return (-1, "", f"Failed to execute {cmd}: {e}", duration)
