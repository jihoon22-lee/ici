"""Tests for verification engine isolation."""

from ici.core.models import EngineResult, EngineStatus, EvidenceState
from ici.engines.verify import VerifyOrchestrator


def test_run_all_records_engine_error_and_continues(monkeypatch, tmp_path):
    class CrashingEngine:
        def __init__(self, project_root, config):
            pass

        def run(self):
            raise RuntimeError("boom")

    class PassingEngine:
        def __init__(self, project_root, config):
            pass

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
            "cycle",
        )
    }
    suite = VerifyOrchestrator(tmp_path, {"engines": enabled}).run_all()

    assert [result.status for result in suite.results] == [EngineStatus.ERROR, EngineStatus.PASS]
    assert suite.results[0].engine_name == "line"
    assert suite.results[0].summary == "Engine crashed: RuntimeError: boom"
    assert suite.results[0].evidence == EvidenceState.NOT_RUN
    assert suite.results[1].engine_name == "lint"
