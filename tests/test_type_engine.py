"""Tests for mypy tool failure handling and partial-language evidence."""

import pytest

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
    mypy_evidence = next(e for e in result.tool_evidence if e.name == "mypy")
    assert "not parseable" in mypy_evidence.error


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


def test_mypy_success_with_valid_notes_is_accepted_and_preserves_note_targets(
    tmp_python_project, monkeypatch
):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            0,
            "src/sample_pkg/core.py:2: note: checked overload\n"
            "src/sample_pkg/core.py:4: note: inferred type\n"
            "Success: no issues found in 1 source file\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    assert result.status != EngineStatus.ERROR
    notes = [target for target in result.targets if target.target_name == "MypyNote"]
    assert [(target.start_line, target.status) for target in notes] == [
        (2, EngineStatus.WARN),
        (4, EngineStatus.WARN),
    ]


def test_mypy_repeated_identical_notes_fold_with_visible_count(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            0,
            "src/sample_pkg/core.py:2: note: same repeated note\n"
            "src/sample_pkg/core.py:9: note: same repeated note\n"
            "src/sample_pkg/core.py:16: note: same repeated note\n"
            "Success: no issues found in 1 source file\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    notes = [target for target in result.targets if target.target_name == "MypyNote"]
    # Folded into a single target at the first occurrence's location, not
    # three separate rows -- but the repeat count must stay visible in the
    # rendered message (metrics["repeats"] alone was previously invisible
    # everywhere a target gets printed).
    assert len(notes) == 1
    assert notes[0].start_line == 2
    assert notes[0].metrics["repeats"] == 3
    assert notes[0].message == "same repeated note (x3)"


def test_mypy_success_with_error_diagnostic_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            0,
            "src/sample_pkg/core.py:2: error: incompatible type\n"
            "Success: no issues found in 1 source file\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert any(target.target_name == "MypyError" for target in result.targets)


@pytest.mark.parametrize(
    "output",
    [
        "Success: no issues found in 1 source file\n"
        "src/sample_pkg/core.py:2: note: emitted after summary\n",
        "Success: no issues found in 1 source file\nSuccess: no issues found in 1 source file\n",
    ],
)
def test_mypy_success_summary_must_be_unique_and_last(tmp_python_project, monkeypatch, output):
    _use_mypy(monkeypatch)
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(0, output, "", 0.01),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR


def test_mypy_zero_source_success_is_not_valid():
    assert not TypeCheckEngine._is_valid_mypy_success(
        "Success: no issues found in 0 source files\n"
    )


@pytest.mark.parametrize(
    "output",
    [
        "Success: no issues found in 1 source files\n",
        "Success: no issues found in 2 source file\n",
    ],
)
def test_mypy_success_summary_requires_matching_source_plurality(output):
    assert not TypeCheckEngine._is_valid_mypy_success(output)


def test_python_without_applicable_sources_skips_mypy(tmp_path, monkeypatch):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (tmp_path / "ici.toml").write_text(
        'name = "empty_python"\ntype = "python"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    engine = TypeCheckEngine(tmp_path)

    def fail_if_mypy_is_discovered():
        raise AssertionError("Mypy must not run when no Python sources are selected")

    monkeypatch.setattr(engine, "_find_mypy_cmd", fail_if_mypy_is_discovered)

    result = engine.run()

    # Nothing was type-checkable, so this is not applicable rather than a
    # warning: WARN would be a complaint about a situation nobody can act on.
    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert not any(e.name == "mypy" for e in result.tool_evidence)
    assert any(
        target.status == EngineStatus.SKIP and target.target_name == "Mypy"
        for target in result.targets
    )


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
    mypy_evidence = next(e for e in result.tool_evidence if e.name == "mypy")
    assert "exit code 2" in mypy_evidence.error


def test_cpp_type_check_is_explicitly_skipped(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)

    result = TypeCheckEngine(tmp_cpp_project).run()

    # A C++-only project has nothing mypy can read and C++ checking is not
    # implemented, so the engine does not apply. The per-file SKIP targets stay
    # so the report can still say which files went unchecked.
    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert any(target.status == EngineStatus.SKIP for target in result.targets)
    assert "C++" in result.summary


def test_cpp_project_without_applicable_sources_skips_type_check(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "ici.toml").write_text(
        'name = "empty_cpp"\ntype = "cpp"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    result = TypeCheckEngine(tmp_path).run()

    # An empty C++ project: no Python, no C++ files either. Nothing applies.
    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert not result.tool_evidence
    target = next(target for target in result.targets if target.target_name == "C++TypeCheck")
    assert target.status == EngineStatus.SKIP
    assert target.file_path == "."
    assert "no applicable c++ source files" in target.message.lower()


def test_hybrid_python_only_project_does_not_skip_absent_cpp_scope(tmp_python_project, monkeypatch):
    (tmp_python_project / "ici.toml").write_text(
        'name = "python_only"\ntype = "hybrid"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: None)

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert not any(target.target_name == "C++TypeCheck" for target in result.targets)


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


def test_hybrid_type_summary_does_not_call_cpp_skip_missing_annotations(
    tmp_python_project, monkeypatch
):
    source = tmp_python_project / "src" / "sample_pkg" / "native.cpp"
    source.write_text("int native() { return 1; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda name: "/usr/bin/mypy" if name == "mypy" else None,
    )
    monkeypatch.setattr(
        "ici.engines.type_check.run_process",
        lambda *args, **kwargs: ProcessResult(
            1,
            "src/sample_pkg/core.py:1: error: incompatible type\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    assert "Missing Annotations" not in result.summary
    assert "Type Findings" in result.summary
    assert "C++ type checking is skipped" in result.summary
