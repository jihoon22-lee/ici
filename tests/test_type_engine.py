"""Tests for mypy tool failure handling and partial-language evidence."""

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


def test_optional_mypy_absence_uses_estimated_ast_fallback(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)

    result = TypeCheckEngine(
        tmp_python_project,
        {"engines": {"type": {"mypy_required": False}}},
    ).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(e.name == "mypy" and e.returncode is None for e in result.tool_evidence)


def test_required_mypy_absence_is_error_and_not_run(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)

    result = TypeCheckEngine(
        tmp_python_project,
        {"engines": {"type": {"mypy_required": True}}},
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(e.name == "mypy" and e.returncode is None for e in result.tool_evidence)


def test_mypy_uvx_and_uv_only_are_treated_as_missing(tmp_python_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda name: "/usr/bin/uvx" if name == "uvx" else None,
    )

    assert TypeCheckEngine(tmp_python_project)._find_mypy_cmd() is None


def test_mypy_finds_windows_style_project_venv_candidate(tmp_python_project, monkeypatch):
    scripts = tmp_python_project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    mypy = scripts / "mypy"
    mypy.write_text("#!/bin/sh\n", encoding="utf-8")
    mypy.chmod(0o755)
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)

    assert TypeCheckEngine(tmp_python_project)._find_mypy_cmd() == [str(mypy)]


def test_mypy_exit_two_is_tool_error_even_with_diagnostic(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            2,
            "src/sample_pkg/core.py:1: error: incompatible type",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_type_check_is_explicitly_skipped(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)

    result = TypeCheckEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(target.status == EngineStatus.SKIP for target in result.targets)
    assert "C++" in result.summary and "skip" in result.summary.lower()


def test_hybrid_type_evidence_stays_estimated_when_cpp_is_skipped(tmp_python_project, monkeypatch):
    source = tmp_python_project / "src" / "sample_pkg" / "native.cpp"
    source.write_text("int native() { return 1; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda name: "/usr/bin/mypy" if name == "mypy" else None,
    )
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            0,
            "Success: no issues found in 1 source file\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.evidence == EvidenceState.ESTIMATED
    assert any(target.status == EngineStatus.SKIP for target in result.targets)
