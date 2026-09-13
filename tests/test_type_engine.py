"""Tests for mypy tool failure handling and partial-language evidence."""

from types import SimpleNamespace

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines.type_check import TypeCheckEngine


def _patch_runner(monkeypatch, fake):
    """Patch wherever the engine's process actually gets started.

    Both names on purpose. #205 item 5 moved this engine off its own
    subprocess call and onto the common executor, so the seam these tests were
    written against is no longer where the process is started. Patching only
    the old name would leave the engine running mypy for real and the tests
    passing for the wrong reason; patching only the new one would stop them
    being usable against the wiring they were written to pin.
    """

    monkeypatch.setattr("ici.engines.type_check.run_process", fake, raising=False)
    monkeypatch.setattr("ici.execution.process.run_process", fake, raising=False)


def _use_mypy(monkeypatch):
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda name: "/usr/bin/mypy" if name == "mypy" else None,
    )


def test_mypy_stderr_diagnostic_is_failure(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(
        monkeypatch,
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
    _patch_runner(
        monkeypatch,
        lambda *args, **kwargs: ProcessResult(124, "", "Command timed out", 0.05, timed_out=True),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_mypy_unexpected_success_output_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(
        monkeypatch, lambda *args, **kwargs: ProcessResult(0, "unexpected tool output", "", 0.01)
    )

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    mypy_evidence = next(e for e in result.tool_evidence if e.name == "mypy")
    assert "not parseable" in mypy_evidence.error


def test_mypy_empty_success_output_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(monkeypatch, lambda *args, **kwargs: ProcessResult(0, "", "", 0.01))

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_mypy_success_line_with_junk_is_error(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(
        monkeypatch,
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
    _patch_runner(
        monkeypatch,
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


def test_mypy_diagnostic_preserves_source_column(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(
        monkeypatch,
        lambda *args, **kwargs: ProcessResult(
            1,
            "src/sample_pkg/core.py:3:17: error: incompatible type [assignment]\n",
            "",
            0.01,
        ),
    )

    result = TypeCheckEngine(tmp_python_project).run()

    finding = next(target for target in result.targets if target.target_name == "MypyError")
    assert finding.start_line == 3
    assert finding.start_column == 17


def test_mypy_repeated_identical_notes_fold_with_visible_count(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(
        monkeypatch,
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
    _patch_runner(
        monkeypatch,
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
    _patch_runner(monkeypatch, lambda *args, **kwargs: ProcessResult(0, output, "", 0.01))

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


def test_mypy_command_reuses_selected_interpreter_capability(tmp_python_project, monkeypatch):
    capability = ToolCapability(
        name="mypy",
        path="/selected/python",
        available=True,
        complete=True,
        details={"provider": "python-module", "module": "mypy"},
    )
    engine = TypeCheckEngine(tmp_python_project)
    engine.analysis_context = SimpleNamespace(
        capabilities=CapabilityInventory(capabilities={"mypy": capability})
    )
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda _name: pytest.fail("shared capability must prevent a PATH re-probe"),
    )

    assert engine._find_mypy_cmd() == ["/selected/python", "-m", "mypy"]


def test_mypy_unavailable_shared_capability_does_not_fall_back_to_path(
    tmp_python_project, monkeypatch
):
    capability = ToolCapability(name="mypy", path="", available=False, complete=False)
    engine = TypeCheckEngine(tmp_python_project)
    engine.analysis_context = SimpleNamespace(
        capabilities=CapabilityInventory(capabilities={"mypy": capability})
    )
    monkeypatch.setattr("ici.engines.type_check.shutil.which", lambda _name: "/other/mypy")

    assert engine._find_mypy_cmd() is None


def test_mypy_exit_two_is_tool_error_even_with_diagnostic(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    _patch_runner(
        monkeypatch,
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
    _patch_runner(
        monkeypatch,
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
    _patch_runner(
        monkeypatch,
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


def test_hybrid_mypy_receives_only_python_source_roots(tmp_path, monkeypatch):
    python_root = tmp_path / "python"
    cpp_root = tmp_path / "src"
    include_root = tmp_path / "include"
    python_root.mkdir()
    cpp_root.mkdir()
    include_root.mkdir()
    (python_root / "package.py").write_text("value: int = 1\n", encoding="utf-8")
    (cpp_root / "native.cpp").write_text("int native() { return 1; }\n", encoding="utf-8")
    (include_root / "native.hpp").write_text("int native();\n", encoding="utf-8")
    (tmp_path / "ici.toml").write_text(
        'name = "hybrid"\ntype = "hybrid"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    config = {"project": {"source_dirs": ["python", "src", "include"]}}
    commands: list[list[str]] = []
    monkeypatch.setattr(
        "ici.engines.type_check.shutil.which",
        lambda name: "/usr/bin/mypy" if name == "mypy" else None,
    )

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return ProcessResult(0, "Success: no issues found in 1 source file\n", "", 0.01)

    _patch_runner(monkeypatch, fake_run)

    result = TypeCheckEngine(tmp_path, config).run()

    assert result.status == EngineStatus.WARN
    assert commands == [["/usr/bin/mypy", "python"]]
    assert "src" not in commands[0]
    assert "include" not in commands[0]


def test_default_mypy_profile_preserves_project_configuration(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    commands: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return ProcessResult(0, "Success: no issues found in 1 source file\n", "", 0.01)

    _patch_runner(monkeypatch, fake_run)

    result = TypeCheckEngine(tmp_python_project).run()

    assert result.status == EngineStatus.PASS
    assert commands == [["/usr/bin/mypy", "src"]]
    assert "--ignore-missing-imports" not in commands[0]
    assert result.extra["mypy_profile"] == "project"
    assert result.extra["mypy_project_config_discovery"] is True


def test_ici_mypy_profile_is_an_explicit_argv_overlay(tmp_python_project, monkeypatch):
    _use_mypy(monkeypatch)
    commands: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        commands.append(argv)
        return ProcessResult(0, "Success: no issues found in 1 source file\n", "", 0.01)

    _patch_runner(monkeypatch, fake_run)
    config = {"engines": {"type": {"mypy_profile": "ici"}}}

    result = TypeCheckEngine(tmp_python_project, config).run()

    assert result.status == EngineStatus.PASS
    assert commands == [
        [
            "/usr/bin/mypy",
            "--check-untyped-defs",
            "--warn-redundant-casts",
            "--warn-unused-ignores",
            "src",
        ]
    ]
    assert result.extra["mypy_profile"] == "ici"


class TestEveryWayMypyCanEndIsPinned:
    """What the six hand-written branches in _run_mypy decide, recorded.

    #205 item 5 moves this provider onto the common executor, and the point of
    doing it is visible right here: these branches are Outcome and ExitContract
    written out by hand in one engine, which every other engine then has to
    write out again. Before the wiring changes, what it currently decides is
    pinned -- so the diff that moves it shows the behaviour holding rather than
    claiming it does, and a regression can be traced to this one adapter.
    """

    SUCCESS = "Success: no issues found in 1 source file"

    @staticmethod
    def _run(project, monkeypatch, result: ProcessResult, config=None):
        """Drive the engine with one process result, whichever layer runs it.

        Patched in both places on purpose. These tests were written against the
        old wiring, where the engine called run_process itself, and they have
        to keep meaning the same thing after #205 item 5 moves it onto the
        executor -- otherwise the before-and-after says nothing. Patching only
        the name the old path used would leave the new path running mypy for
        real and quietly passing.
        """

        _use_mypy(monkeypatch)
        _patch_runner(monkeypatch, lambda *a, **k: result)
        return TypeCheckEngine(project, config or {}).run()

    def test_a_clean_exit_is_a_pass(self, tmp_python_project, monkeypatch) -> None:
        result = self._run(
            tmp_python_project, monkeypatch, ProcessResult(0, self.SUCCESS, "", 0.01)
        )

        assert result.status == EngineStatus.PASS
        assert result.evidence == EvidenceState.MEASURED

    def test_exit_one_is_findings_not_a_broken_tool(self, tmp_python_project, monkeypatch) -> None:
        # The distinction #205 item 6 exists for. mypy exits 1 because it found
        # type errors, and that is the tool working: the run is MEASURED, and
        # what the findings mean for the gate is the mode's business.
        result = self._run(
            tmp_python_project,
            monkeypatch,
            ProcessResult(1, "src/sample_pkg/core.py:1: error: bad type\n", "", 0.01),
        )

        assert result.evidence == EvidenceState.MEASURED, "findings were read as a run that failed"
        assert result.status == EngineStatus.WARN
        assert result.targets

    def test_the_same_exit_one_fails_the_gate_under_pass_fail(
        self, tmp_python_project, monkeypatch
    ) -> None:
        result = self._run(
            tmp_python_project,
            monkeypatch,
            ProcessResult(1, "src/sample_pkg/core.py:1: error: bad type\n", "", 0.01),
            {"engines": {"type": {"mode": "pass_fail"}}},
        )

        assert result.status == EngineStatus.FAIL
        assert result.evidence == EvidenceState.MEASURED

    def test_exit_two_is_a_broken_tool_not_findings(self, tmp_python_project, monkeypatch) -> None:
        result = self._run(
            tmp_python_project, monkeypatch, ProcessResult(2, "", "usage: mypy [-h]", 0.01)
        )

        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN
        assert "exit code 2" in result.summary

    def test_a_timeout_is_not_a_pass(self, tmp_python_project, monkeypatch) -> None:
        result = self._run(
            tmp_python_project,
            monkeypatch,
            ProcessResult(124, "", "Command timed out", 0.05, timed_out=True),
        )

        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN
        assert result.summary == "Mypy timed out"

    def test_truncated_output_is_not_a_pass_even_at_exit_zero(
        self, tmp_python_project, monkeypatch
    ) -> None:
        # Exit 0 with the answer cut off: what was thrown away might be the
        # part with the errors in it.
        result = self._run(
            tmp_python_project,
            monkeypatch,
            ProcessResult(0, self.SUCCESS, "", 0.01, truncated=True),
        )

        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN
        assert result.summary == "Mypy output was truncated"

    def test_a_signal_is_not_a_pass(self, tmp_python_project, monkeypatch) -> None:
        result = self._run(tmp_python_project, monkeypatch, ProcessResult(-9, "", "", 0.01))

        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN
        assert result.summary == "Mypy terminated before producing a result"

    def test_a_clean_exit_that_says_something_on_stderr_is_not_trusted(
        self, tmp_python_project, monkeypatch
    ) -> None:
        result = self._run(
            tmp_python_project, monkeypatch, ProcessResult(0, self.SUCCESS, "warning: odd", 0.01)
        )

        assert result.status == EngineStatus.ERROR
        assert "unexpected stderr" in result.summary

    def test_a_clean_exit_whose_output_is_not_mypys_is_not_trusted(
        self, tmp_python_project, monkeypatch
    ) -> None:
        # Exit 0 saying something nobody can parse is not "no issues found".
        result = self._run(tmp_python_project, monkeypatch, ProcessResult(0, "surprise", "", 0.01))

        assert result.status == EngineStatus.ERROR
        assert "not parseable" in result.summary
