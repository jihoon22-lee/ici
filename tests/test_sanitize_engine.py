"""Tests for sanitizer tool failure handling."""

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
