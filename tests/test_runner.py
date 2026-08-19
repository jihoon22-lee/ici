"""Tests for bounded subprocess execution."""

import os
import sys
import time

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


def test_run_process_waits_for_finite_descendant_pipe_holder(tmp_path):
    child_code = "import time; time.sleep(0.1)"
    parent_code = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print('parent', flush=True)"
    )

    result = run_process([sys.executable, "-c", parent_code], cwd=tmp_path, timeout=0.8)

    assert result.timed_out is False
    assert result.returncode == 0
    assert "parent" in result.stdout


def test_run_process_deadline_kills_infinite_descendant_pipe_holder(tmp_path):
    marker = tmp_path / "descendant-survived.txt"
    child_code = (
        "import time; time.sleep(2); "
        f"open({str(marker)!r}, 'w', encoding='utf-8').write('alive')"
    )
    parent_code = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])"
    )
    started = time.monotonic()

    result = run_process([sys.executable, "-c", parent_code], cwd=tmp_path, timeout=0.1)

    elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert result.returncode == 124
    assert elapsed < 1.0
    time.sleep(0.2)
    assert not marker.exists()


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


def test_windows_job_is_attached_before_process_resume(monkeypatch):
    calls = []

    class FakeKernel32:
        def CreateJobObjectW(self, security, name):
            calls.append("create")
            return 101

        def SetInformationJobObject(self, job, info_class, info, info_size):
            calls.append("set_limits")
            return 1

        def AssignProcessToJobObject(self, job, process):
            calls.append("assign")
            return 1

        def ResumeThread(self, thread):
            calls.append("resume")
            return 1

        def CloseHandle(self, handle):
            calls.append("close")
            return 1

    class FakeProcess:
        _handle = 202
        _thread_handle = 303

    process = FakeProcess()
    monkeypatch.setattr(runner, "_get_windows_kernel32", lambda: FakeKernel32(), raising=False)

    runner._start_windows_job(process)

    assert calls == ["create", "set_limits", "assign", "resume"]
    assert process._ici_job_handle == 101


def test_windows_job_startup_failure_terminates_and_closes(monkeypatch):
    calls = []

    class FakeKernel32:
        def CreateJobObjectW(self, security, name):
            calls.append("create")
            return 101

        def SetInformationJobObject(self, job, info_class, info, info_size):
            calls.append("set_limits")
            return 1

        def AssignProcessToJobObject(self, job, process):
            calls.append("assign")
            return 0

        def TerminateJobObject(self, job, code):
            calls.append("terminate")
            return 1

        def CloseHandle(self, handle):
            calls.append("close")
            return 1

    class FakeProcess:
        _handle = 202
        _thread_handle = 303

        def __init__(self):
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        def wait(self):
            self.waited = True

    process = FakeProcess()
    monkeypatch.setattr(runner, "_get_windows_kernel32", lambda: FakeKernel32(), raising=False)

    with pytest.raises(OSError):
        runner._start_windows_job(process)

    assert calls == ["create", "set_limits", "assign", "terminate", "close"]
    assert process.killed is True
    assert process.waited is True
