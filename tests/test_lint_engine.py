"""Tests for lint tool failure handling and execution evidence."""

from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines.lint import LintEngine


def _use_ruff(monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/ruff" if name == "ruff" else None,
    )


def test_ruff_truncated_json_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, '[{"filename": "src/core.py"', "", 0.01, truncated=True)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_timeout_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(124, "", "Command timed out", 0.05, timed_out=True)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_violation_exit_without_violations_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(1, "[]", "", 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_success_without_json_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "", "", 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    check_evidence = next(e for e in result.tool_evidence if e.name == "ruff check")
    assert "parseable JSON" in check_evidence.error


def test_ruff_clean_format_summary_is_success(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "2 files already formatted\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.PASS


def test_ruff_empty_format_success_is_accepted(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.PASS


def test_ruff_format_parse_failure_records_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "unexpected format output\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    format_evidence = next(e for e in result.tool_evidence if e.name == "ruff format")
    assert "not parseable" in format_evidence.error


def test_optional_ruff_absence_uses_estimated_ast_fallback(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    result = LintEngine(
        tmp_python_project,
        {"engines": {"lint": {"ruff_required": False}}},
    ).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(e.name == "ruff" and e.returncode is None for e in result.tool_evidence)


def test_required_ruff_absence_is_error_and_not_run(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    result = LintEngine(
        tmp_python_project,
        {"engines": {"lint": {"ruff_required": True}}},
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(e.name == "ruff" and e.returncode is None for e in result.tool_evidence)


def test_ruff_uvx_and_uv_only_are_treated_as_missing(tmp_python_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/uvx" if name == "uvx" else None,
    )

    assert LintEngine(tmp_python_project)._find_ruff_command() is None


def test_ruff_finds_windows_style_project_venv_candidate(tmp_python_project, monkeypatch):
    scripts = tmp_python_project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    ruff = scripts / "ruff"
    ruff.write_text("#!/bin/sh\n", encoding="utf-8")
    ruff.chmod(0o755)
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    assert LintEngine(tmp_python_project)._find_ruff_command() == [str(ruff)]


def test_ruff_spawn_exception_records_both_attempts(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.LintEngine._find_ruff_command", lambda _self: ["ruff"])

    def raise_spawn(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr("ici.engines.lint.run_process", raise_spawn)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert [e.name for e in result.tool_evidence] == ["ruff check", "ruff format"]
    assert all(e.error for e in result.tool_evidence)


def test_ruff_exit_two_is_tool_error_even_with_json_diagnostic(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(
                2,
                '[{"filename":"src/sample_pkg/core.py","location":{"row":1},"code":"E1","message":"bad"}]',
                "",
                0.01,
            )
        return ProcessResult(0, "2 files already formatted\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    check_evidence = next(e for e in result.tool_evidence if e.name == "ruff check")
    assert "exit code 2" in check_evidence.error


def test_missing_gxx_for_discovered_cpp_is_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(e.name == "g++" and e.returncode is None for e in result.tool_evidence)


def test_cpp_diagnostic_uses_reported_file_and_line(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    source = tmp_cpp_project / "src" / "main.cpp"
    diagnostic = f"{source}:12:7: error: expected ';'\n"
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.FAIL
    target = next(target for target in result.targets if target.target_name == "C++Syntax")
    assert target.file_path == "src/main.cpp"
    assert target.start_line == 12


def test_cpp_located_note_is_a_non_failure_diagnostic(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = (
        "src/main.cpp:12:7: error: expected ';'\n"
        "src/main.cpp:12:7: note: the declaration ends here\n"
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.FAIL
    cpp_targets = [target for target in result.targets if target.target_name == "C++Syntax"]
    assert any(target.status == EngineStatus.FAIL for target in cpp_targets)
    note = next(target for target in cpp_targets if target.message.startswith("note:"))
    assert note.status == EngineStatus.WARN
    assert note.file_path == "src/main.cpp"
    assert note.start_line == 12


def test_cpp_template_context_is_allowed_before_primary_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = (
        "src/main.cpp: In instantiation of \u2018void call(T) [with T = int]\u2019:\n"
        "src/main.cpp:12:5:   required from here\n"
        "src/main.cpp:5:10: error: invalid conversion\n"
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.MEASURED
    error = next(
        target
        for target in result.targets
        if target.target_name == "C++Syntax" and target.status == EngineStatus.FAIL
    )
    assert error.file_path == "src/main.cpp"
    assert error.start_line == 5


def test_cpp_unrecognized_context_line_is_not_accepted(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = "src/main.cpp:12:7: warning: unused value\nsrc/main.cpp: In arbitrary context:\n"
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_signal_failure_is_tool_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(-9, "", "src/main.cpp:2: error: crash", 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_exit_two_records_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = "src/main.cpp:2:5: error: compiler crashed\n"
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(2, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.ERROR
    compiler_evidence = next(e for e in result.tool_evidence if e.name == "g++")
    assert "exit code 2" in compiler_evidence.error


def test_cpp_malformed_success_output_is_tool_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(0, "unexpected compiler output\n", "", 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    compiler_evidence = next(e for e in result.tool_evidence if e.name == "g++")
    assert "not parseable" in compiler_evidence.error


def test_cpp_warning_context_is_kept_as_a_finding(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = (
        "src/main.cpp:3:5: warning: unused variable 'value'\n"
        "    3 | int value = 1;\n"
        "      |     ^~~~~\n"
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.MEASURED
    target = next(target for target in result.targets if target.target_name == "C++Syntax")
    assert target.status == EngineStatus.WARN
    assert target.start_line == 3
