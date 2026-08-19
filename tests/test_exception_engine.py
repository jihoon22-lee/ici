"""Adversarial tests for exception-safety evidence."""

from ici.core.models import EngineStatus, EvidenceState
from ici.engines.exception import ExceptionSafetyEngine


def test_exception_engine_reports_lost_traceback(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        "try:\n    work()\nexcept Exception as exc:\n    raise exc\n", encoding="utf-8"
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    target = next(target for target in result.targets if target.target_name == "LostTraceback")
    assert target.file_path == "src/mod.py"
    assert target.start_line == 4
    assert target.status == EngineStatus.WARN


def test_exception_engine_distinguishes_bare_and_other_variable_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        """try:
    work()
except Exception as exc:
    raise RuntimeError("wrapped")

try:
    work()
except Exception as exc:
    raise
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "LostTraceback" for target in result.targets)
    assert result.status == EngineStatus.PASS


def test_exception_engine_ignores_raise_in_nested_function_scope(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        """def make_handler():
    try:
        work()
    except Exception as exc:
        def later():
            raise exc
        return later
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "LostTraceback" for target in result.targets)


def test_exception_engine_ignores_cpp_comments_and_strings(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "safe.cpp").write_text(
        """// catch(...) {}
const char* text = "catch(...) {} throw boom";
/* ~Type() { throw bad; } */
void f() { try { work(); } catch (...) { log(); } }
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(
        target.target_name in {"CatchAllSwallowed", "DestructorThrow"} for target in result.targets
    )


def test_exception_engine_reports_syntax_errors_as_not_run(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.py").write_text("try:\n    pass\nexcept (:\n    pass\n", encoding="utf-8")

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(target.target_name == "SyntaxError" for target in result.targets)


def test_exception_engine_without_sources_is_explicitly_skipped(tmp_path):
    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.targets[0].status == EngineStatus.SKIP
