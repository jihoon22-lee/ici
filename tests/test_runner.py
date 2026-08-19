"""Tests for bounded subprocess execution."""

import os
import sys

import pytest

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None

from ici.core import runner
from ici.core.runner import ProcessResult, run_process


def test_run_process_marks_timeout(tmp_path):
    result = run_process(
        [sys.executable, "-c", "import time; time.sleep(2)"],
        cwd=tmp_path,
        timeout=0.05,
    )

    assert isinstance(result, ProcessResult)
    assert result.timed_out is True
    assert result.returncode == 124


def test_run_process_truncates_output(tmp_path):
    result = run_process(
        [sys.executable, "-c", "print('x' * 1000)"],
        cwd=tmp_path,
        max_output_chars=100,
    )

    assert result.truncated is True
    assert len(result.stdout) <= 100


@pytest.mark.skipif(
    os.name != "posix" or resource is None, reason="RSS accounting is POSIX-specific"
)
def test_run_process_discards_high_volume_output_without_rss_growth(tmp_path):
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = run_process(
        [
            sys.executable,
            "-c",
            "import sys; [sys.stdout.write('x' * 65536) for _ in range(1024)]",
        ],
        cwd=tmp_path,
        max_output_chars=128,
    )
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert result.returncode == 0
    assert result.truncated is True
    assert len(result.stdout) == 128
    assert after - before < 48 * 1024


def test_windows_timeout_uses_job_object(monkeypatch):
    calls = []

    class FakeKernel32:
        def CreateJobObjectW(self, security, name):
            calls.append(("create", security, name))
            return 101

        def AssignProcessToJobObject(self, job, process):
            calls.append(("assign", job, process))
            return 1

        def TerminateJobObject(self, job, code):
            calls.append(("terminate", job, code))
            return 1

        def CloseHandle(self, handle):
            calls.append(("close", handle))
            return 1

    class FakeProcess:
        pid = 77
        _handle = 202

        def __init__(self):
            self.waited = False
            self.killed = False

        def wait(self):
            self.waited = True

        def kill(self):
            self.killed = True

    kernel = FakeKernel32()
    process = FakeProcess()
    monkeypatch.setattr(runner, "_get_windows_kernel32", lambda: kernel, raising=False)

    runner._terminate_windows_process(process)

    assert calls == [
        ("create", None, None),
        ("assign", 101, 202),
        ("terminate", 101, 1),
        ("close", 101),
    ]
    assert process.waited is True
    assert process.killed is False
