"""Required-policy propagation tests for engines and the suite orchestrator."""

from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines.complexity import ComplexityEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.line import LineCountEngine
from ici.engines.verify import VerifyOrchestrator


@pytest.mark.parametrize(
    ("engine_cls", "name"),
    [
        (LineCountEngine, "line"),
        (ComplexityEngine, "complexity"),
        (DuplicateEngine, "dup"),
    ],
)
def test_direct_engine_result_honors_optional_required_policy(tmp_path, engine_cls, name):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def work():\n    return 1\n", encoding="utf-8")

    result = engine_cls(tmp_path, {"engines": {name: {"required": False}}}).run()

    assert result.required is False


def test_verify_orchestrator_preserves_required_policy_for_line_complexity_and_dup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def work():\n    return 1\n", encoding="utf-8")
    names = ("line", "complexity", "dup")
    config = {
        "engines": {
            name: {"enabled": name in names, "required": False}
            for name in (
                "line",
                "cmake_lint",
                "lint",
                "test",
                "type",
                "complexity",
                "sanitize",
                "dead",
                "dup",
                "exception",
            )
        }
    }
    monkeypatch.setattr("ici.engines.verify.print_suite_dashboard", lambda suite, root: None)

    suite = VerifyOrchestrator(tmp_path, config).run_all()

    assert [result.engine_name for result in suite.results] == list(names)
    assert all(result.required is False for result in suite.results)
    assert suite.suite_status in {EngineStatus.PASS, EngineStatus.WARN}
