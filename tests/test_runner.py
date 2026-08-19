"""Tests for bounded subprocess execution."""

import sys

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
