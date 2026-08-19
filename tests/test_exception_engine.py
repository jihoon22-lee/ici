"""Adversarial tests for exception-safety evidence."""

import pytest

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


def test_exception_engine_returns_pass_target_for_clean_python_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.py").write_text("def work():\n    return 1\n", encoding="utf-8")

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert any(
        target.file_path == "src/clean.py" and target.status == EngineStatus.PASS
        for target in result.targets
    )


def test_exception_engine_supports_multiline_cpp_bodies(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "unsafe.cpp").write_text(
        """class Type {
public:
    ~Type()
    {
        throw 1;
    }
};

void run() {
    try {
        work();
    }
    catch (...)
    {
    }
}
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert any(target.target_name == "DestructorThrow" for target in result.targets)
    assert any(target.target_name == "CatchAllSwallowed" for target in result.targets)


def test_exception_engine_ignores_cpp_raw_string_false_positive(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "raw.cpp").write_text(
        'const char* text = R"tag(catch (...) { } ~Type() { throw 1; })tag";\n',
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(
        target.target_name in {"CatchAllSwallowed", "DestructorThrow"} for target in result.targets
    )


@pytest.mark.parametrize("prefix", ["u8R", "uR", "UR", "LR"])
def test_exception_engine_ignores_cpp_prefixed_raw_string_false_positive(tmp_path, prefix):
    src = tmp_path / "src"
    src.mkdir()
    (src / "raw.cpp").write_text(
        f'const char* text = {prefix}"tag(catch (...) {{ }} ~Type() {{ throw 1; }})tag";\n',
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(
        target.target_name in {"CatchAllSwallowed", "DestructorThrow"} for target in result.targets
    )


def test_exception_default_mode_matches_pass_fail_policy(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad.py").write_text(
        "try:\n    work()\nexcept Exception as exc:\n    raise exc\n", encoding="utf-8"
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL


def test_exception_engine_does_not_flag_explicit_raise_cause(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "wrapped.py").write_text(
        """try:
    work()
except Exception as exc:
    cause = RuntimeError("cause")
    raise exc from cause
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "LostTraceback" for target in result.targets)


@pytest.mark.parametrize(
    "handler",
    [
        "except BaseException:",
        "except (ValueError, BaseException):",
        "except builtins.BaseException:",
    ],
)
def test_exception_engine_flags_base_exception_handlers_with_location(tmp_path, handler):
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text(
        f"import builtins\n\ntry:\n    work()\n{handler}\n    log()\n",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    target = next(target for target in result.targets if target.target_name == "BaseException")
    assert target.file_path == "src/mod.py"
    assert target.start_line == 5
    assert target.status == EngineStatus.FAIL
    assert result.status == EngineStatus.FAIL


def test_exception_engine_cancels_pending_destructor_at_declaration_semicolon(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "unsafe.cpp").write_text(
        """class Type {
public:
    ~Type()
        noexcept;
};

void run() {
    throw 1;
}
""",
        encoding="utf-8",
    )

    result = ExceptionSafetyEngine(tmp_path).run()

    assert not any(target.target_name == "DestructorThrow" for target in result.targets)


def test_exception_engine_calculates_empty_catch_once_for_pass_target(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.cpp").write_text(
        "void run() { try { work(); } catch (...) { log(); } }\n", encoding="utf-8"
    )
    calls = []
    original = ExceptionSafetyEngine._empty_catch_all_lines

    def counted(masked):
        calls.append(masked)
        return original(masked)

    monkeypatch.setattr(ExceptionSafetyEngine, "_empty_catch_all_lines", staticmethod(counted))

    result = ExceptionSafetyEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert len(calls) == 1
