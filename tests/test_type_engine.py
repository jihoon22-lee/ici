"""Tests for mypy tool failure handling."""

from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines.type_check import TypeCheckEngine


def _use_mypy(monkeypatch):
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda name: "/usr/bin/mypy" if name == "mypy" else None,
    )


def test_mypy_stderr_diagnostic_is_failure(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            1,
            "",
            "src/sample_pkg/core.py:1: error: incompatible type",
            0.01,
        ),
    )

    result = TypeCheckEngine(
        tmp_python_project,
        {"engines": {"type": {"mode": "pass_fail"}}},
    ).run()

    assert result.status == EngineStatus.FAIL
    assert any(target.target_name == "MypyError" for target in result.targets)


def test_mypy_timeout_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(124, "", "Command timed out", 0.05, timed_out=True),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_mypy_unexpected_success_output_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(0, "unexpected tool output", "", 0.01),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_mypy_empty_success_output_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", "", 0.01),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_mypy_success_line_with_junk_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            0,
            "Success: no issues found in 1 source file\nunexpected junk\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
