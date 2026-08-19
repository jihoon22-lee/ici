"""Tests for sanitizer tool failure handling and evidence contracts."""

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.engines.sanitize import SanitizeEngine


def test_sanitizer_truncated_compile_output_is_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int add(int a, int b) { return a + b; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_add.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", "", 0.01, truncated=True),
    )

    result = SanitizeEngine(tmp_path, {"engines": {"sanitize": {"mode": "pass_fail"}}}).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_compile_failure_is_not_reported_as_pass(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int add(int a, int b) { return a + b; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_add.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", "compile error", 0.01),
    )

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert any(target.target_name == "SanitizerCompile" for target in result.targets)
    evidence = next(e for e in result.tool_evidence if e.name == "sanitizer compile")
    assert "compile error" in evidence.error


def test_cpp_without_test_sources_is_explicitly_skipped(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(target.status == EngineStatus.SKIP for target in result.targets)


def test_python_resource_warning_check_uses_resolved_python_module(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_resource.py").write_text("def test_resource():\n    pass\n", encoding="utf-8")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return ProcessResult(0, "1 passed in 0.01s\n", "", 0.01)

    monkeypatch.setattr("ici.engines.sanitize.run_process", fake_run)
    engine = SanitizeEngine(tmp_path)
    monkeypatch.setattr(engine, "_resolve_python", lambda: ["/project/python"])

    result = engine.run()

    assert result.status == EngineStatus.PASS
    assert seen["command"] == [
        "/project/python",
        "-W",
        "error::ResourceWarning",
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "tests",
    ]
    assert seen["kwargs"]["cwd"] == tmp_path
    assert seen["kwargs"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "no:cacheprovider" in seen["kwargs"]["env"]["PYTEST_ADDOPTS"]


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(124, "", "timed out", 0.01, timed_out=True), "timed out"),
        (ProcessResult(0, "1 passed", "", 0.01, truncated=True), "truncated"),
        (ProcessResult(-1, "", "spawn failed", 0.01), "terminated"),
        (ProcessResult(0, "success without test result", "", 0.01), "parseable"),
    ],
)
def test_python_resource_warning_tool_failures_are_errors(tmp_path, monkeypatch, result, message):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_resource.py").write_text("def test_resource():\n    pass\n", encoding="utf-8")
    engine = SanitizeEngine(tmp_path)
    monkeypatch.setattr(engine, "_resolve_python", lambda: ["/project/python"])
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: result)

    actual = engine.run()

    assert actual.status == EngineStatus.ERROR
    assert actual.evidence == EvidenceState.NOT_RUN
    assert message.lower() in actual.summary.lower() or any(
        message.lower() in evidence.error.lower() for evidence in actual.tool_evidence
    )


def test_python_resource_warning_without_tests_is_not_pass(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert "test" in result.summary.lower()
