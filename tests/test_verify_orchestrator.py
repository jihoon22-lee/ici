"""Tests for verification engine isolation."""

import pytest

from ici.core.baseline import BaselineError
from ici.core.models import BaselineComparison, EngineResult, EngineStatus, EvidenceState
from ici.engines.verify import VerifyOrchestrator
from ici.reporters.issue_view import ConsoleGroupBy, ConsoleOptions


def test_run_all_records_engine_error_and_continues(monkeypatch, tmp_path):
    class CrashingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            raise RuntimeError("boom")

    class PassingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            return EngineResult(
                engine_name="lint",
                status=EngineStatus.PASS,
                summary="ok",
            )

    monkeypatch.setattr("ici.engines.verify.LineCountEngine", CrashingEngine)
    monkeypatch.setattr("ici.engines.verify.LintEngine", PassingEngine)
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda suite, root: None)

    enabled = {
        name: {"enabled": name in {"line", "lint"}}
        for name in (
            "line",
            "cmake_lint",
            "pyproject_lint",
            "file_hygiene",
            "toolchain",
            "python_compat",
            "build_definition",
            "static_hygiene",
            "compile_db",
            "lint",
            "test",
            "type",
            "complexity",
            "sanitize",
            "dead",
            "dup",
            "exception",
            "cognitive",
            "security",
            "cycle",
            "resource",
        )
    }
    suite = VerifyOrchestrator(tmp_path, {"engines": enabled}).run_all()

    assert [result.status for result in suite.results] == [EngineStatus.ERROR, EngineStatus.PASS]
    assert suite.results[0].engine_name == "line"
    assert suite.results[0].summary == "Engine crashed: RuntimeError: boom"
    assert suite.results[0].evidence == EvidenceState.NOT_RUN
    assert suite.results[1].engine_name == "lint"
    assert suite.support_matrix is not None
    assert len(suite.support_matrix.entries) == 26
    assert suite.analysis_metadata is not None
    assert suite.analysis_metadata.fingerprint_version == "ici-fingerprint/v1"


def _only_lint_enabled():
    return {
        "engines": {
            name: {"enabled": name == "lint"}
            for name in (
                "line",
                "lint",
                "test",
                "type",
                "complexity",
                "sanitize",
                "dead",
                "dup",
                "exception",
                "cognitive",
                "security",
                "cycle",
                "resource",
            )
        }
    }


def test_baseline_gate_changes_suite_verdict_without_inventing_an_engine(monkeypatch, tmp_path):
    class PassingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            return EngineResult("lint", EngineStatus.PASS, "clean")

    comparison = BaselineComparison(
        source_path="baseline.json",
        fail_on_new=True,
        gate_failed=True,
    )
    monkeypatch.setattr("ici.engines.verify.LintEngine", PassingEngine)
    monkeypatch.setattr(
        "ici.engines.verify.compare_suite_to_baseline", lambda *args, **kwargs: comparison
    )
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda suite, root: None)

    suite = VerifyOrchestrator(tmp_path, _only_lint_enabled()).run_all(
        baseline_path=tmp_path / "baseline.json",
        fail_on_new=True,
    )

    assert suite.suite_status == EngineStatus.FAIL
    assert suite.baseline_comparison == comparison
    assert [result.engine_name for result in suite.results] == ["lint"]


def test_write_baseline_is_root_contained_and_excludes_transient_delta(monkeypatch, tmp_path):
    class PassingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            return EngineResult("lint", EngineStatus.PASS, "clean")

    saved = {}
    monkeypatch.setattr("ici.engines.verify.LintEngine", PassingEngine)
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda suite, root: None)
    monkeypatch.setattr(
        "ici.engines.verify.save_json_report",
        lambda suite, path, project_root: saved.update(
            suite=suite, path=path, project_root=project_root
        ),
    )

    suite = VerifyOrchestrator(tmp_path, _only_lint_enabled()).run_all(
        write_baseline=".ici/baseline.json"
    )

    assert suite.suite_status == EngineStatus.PASS
    assert saved["path"] == tmp_path / ".ici/baseline.json"
    assert saved["project_root"] == tmp_path
    assert saved["suite"].baseline_comparison is None
    assert saved["suite"].analysis_metadata is not None


def test_write_baseline_normalizes_filesystem_errors(monkeypatch, tmp_path):
    class PassingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            return EngineResult("lint", EngineStatus.PASS, "clean")

    monkeypatch.setattr("ici.engines.verify.LintEngine", PassingEngine)
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda suite, root: None)
    monkeypatch.setattr(
        "ici.engines.verify.save_json_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(BaselineError, match="could not write baseline"):
        VerifyOrchestrator(tmp_path, _only_lint_enabled()).run_all(
            write_baseline=".ici/baseline.json"
        )


def test_failed_delta_gate_cannot_overwrite_its_input_baseline(monkeypatch, tmp_path):
    class PassingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            return EngineResult("lint", EngineStatus.PASS, "clean")

    comparison = BaselineComparison(
        source_path=".ici/baseline.json",
        fail_on_new=True,
        gate_failed=True,
    )
    monkeypatch.setattr("ici.engines.verify.LintEngine", PassingEngine)
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda suite, root: None)
    monkeypatch.setattr(
        "ici.engines.verify.compare_suite_to_baseline", lambda *args, **kwargs: comparison
    )
    monkeypatch.setattr(
        "ici.engines.verify.save_json_report",
        lambda *args, **kwargs: pytest.fail("failed gate must preserve its input baseline"),
    )

    with pytest.raises(BaselineError, match="refusing to overwrite"):
        VerifyOrchestrator(tmp_path, _only_lint_enabled()).run_all(
            baseline_path=".ici/baseline.json",
            fail_on_new=True,
            write_baseline=".ici/baseline.json",
        )


def test_console_options_are_forwarded_only_to_console_reporter(monkeypatch, tmp_path):
    class PassingEngine:
        def __init__(self, project_root, config, analysis_context=None):
            del project_root, config, analysis_context

        def run(self):
            return EngineResult("lint", EngineStatus.PASS, "clean")

    captured = {}
    monkeypatch.setattr("ici.engines.verify.LintEngine", PassingEngine)
    monkeypatch.setattr(
        "ici.engines.verify.print_suite_dashboard",
        lambda suite, root, *, options=None: captured.update(
            suite=suite, root=root, options=options
        ),
    )

    options = ConsoleOptions(
        verbose=True,
        max_findings=3,
        group_by=ConsoleGroupBy.FILE,
    )
    suite = VerifyOrchestrator(tmp_path, _only_lint_enabled()).run_all(
        console_options=options,
    )

    assert captured["suite"] is suite
    assert captured["root"] == tmp_path.resolve()
    assert captured["options"] is options
