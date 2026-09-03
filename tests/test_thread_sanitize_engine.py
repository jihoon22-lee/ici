"""Contracts for the deep-profile ThreadSanitizer engine."""

import os
import shutil
from pathlib import Path

import pytest

from ici.core.cmake import BuildSession, ConfigureOptions, TestCaseResult
from ici.core.context import BuildVariant
from ici.core.models import EngineStatus, EvidenceState, ToolEvidence
from ici.core.runner import ProcessResult
from ici.engines.thread_sanitize import ThreadSanitizeEngine


def _write_cpp_project(root: Path) -> tuple[Path, Path]:
    source = root / "src/race.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int shared_value = 0;\n", encoding="utf-8")
    test = root / "tests/test_race.cpp"
    test.parent.mkdir(parents=True)
    test.write_text("int main() { return 0; }\n", encoding="utf-8")
    return source, test


def test_thread_sanitize_does_not_run_python_resource_checks(tmp_path: Path) -> None:
    source = tmp_path / "src/app.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    test = tmp_path / "tests/test_app.py"
    test.parent.mkdir(parents=True)
    test.write_text("def test_app():\n    assert True\n", encoding="utf-8")

    result = ThreadSanitizeEngine(tmp_path).run()

    assert result.engine_name == "thread_sanitize"
    assert result.status is EngineStatus.SKIP
    assert result.evidence is EvidenceState.NOT_APPLICABLE
    assert result.tool_evidence == []
    assert result.targets[0].target_name == "ThreadSanitizer"


def test_generic_thread_sanitize_uses_isolated_flags_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, test = _write_cpp_project(tmp_path)
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("env")))
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.sanitize.run_process", fake_run)
    monkeypatch.setenv("TSAN_OPTIONS", "history_size=4")
    monkeypatch.delenv("ASAN_OPTIONS", raising=False)
    monkeypatch.delenv("UBSAN_OPTIONS", raising=False)

    result = ThreadSanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.PASS
    assert result.evidence is EvidenceState.MEASURED
    assert result.extra["thread_sanitize_issues"] == 0
    assert result.extra["sanitizer_diagnostics"] == []
    assert "-fsanitize=thread" in calls[0][0]
    assert "-pthread" in calls[0][0]
    assert "-fsanitize=address,undefined" not in calls[0][0]
    assert calls[0][0][-2:] == ["-o", calls[0][0][-1]]
    assert calls[1][0][0].endswith("test_race_tsan")
    assert calls[1][1] is not None
    assert calls[1][1]["TSAN_OPTIONS"] == "history_size=4:halt_on_error=1"
    assert "ASAN_OPTIONS" not in calls[1][1]
    assert "UBSAN_OPTIONS" not in calls[1][1]
    assert result.targets[0].file_path == str(test.relative_to(tmp_path))
    assert result.targets[0].target_name == "TSan"
    assert result.targets[0].message == "ThreadSanitizer completed without diagnostics"


def test_thread_sanitize_publishes_normalized_data_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _test = _write_cpp_project(tmp_path)
    transcript = f"""WARNING: ThreadSanitizer: data race (pid=7)
  Write of size 4 at 0x7b0400000800 by thread T1:
    #0 write_value {source}:1:1 (race+0x1234)
SUMMARY: ThreadSanitizer: data race {source}:1 in write_value
"""
    results = iter(
        (
            ProcessResult(0, "", "", 0.01),
            ProcessResult(66, "", transcript, 0.01),
        )
    )
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = ThreadSanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.FAIL
    assert result.evidence is EvidenceState.MEASURED
    assert result.summary == "1 Thread Safety Defect(s) Detected"
    assert result.extra["thread_sanitize_issues"] == 1
    assert result.extra["sanitizer_diagnostics"][0]["kind"] == "tsan"
    assert result.targets[0].target_name == "TSan Error"
    assert result.targets[0].file_path == "src/race.cpp"
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "ici.legacy.thread-sanitize.target"
    assert finding.tool_rule_id == "tsan.data-race"
    assert finding.tool_name == "ThreadSanitizer"
    assert finding.primary_location.path == "src/race.cpp"
    assert finding.primary_location.start_line == 1


def test_thread_sanitize_nonzero_without_a_report_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cpp_project(tmp_path)
    results = iter(
        (
            ProcessResult(0, "", "", 0.01),
            ProcessResult(66, "", "FATAL: ThreadSanitizer: unexpected memory mapping", 0.01),
        )
    )
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = ThreadSanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["sanitizer_diagnostics"] == []
    assert result.tool_evidence[-1].error


@pytest.mark.parametrize("returncode", [0, -6])
def test_thread_sanitize_complete_report_is_measured_even_with_unusual_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    source, _test = _write_cpp_project(tmp_path)
    transcript = (
        "WARNING: ThreadSanitizer: data race\n"
        f"    #0 write_value {source}:1\n"
        f"SUMMARY: ThreadSanitizer: data race {source}:1 in write_value\n"
    )
    results = iter(
        (
            ProcessResult(0, "", "", 0.01),
            ProcessResult(returncode, "", transcript, 0.01),
        )
    )
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = ThreadSanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.FAIL
    assert result.evidence is EvidenceState.MEASURED


def test_thread_sanitize_requests_its_own_adapter_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cpp_project(tmp_path)
    (tmp_path / "CMakeLists.txt").write_text("project(tsan)\n", encoding="utf-8")
    seen: list[ConfigureOptions] = []

    def fake_configure(root: Path, options: ConfigureOptions) -> BuildSession:
        seen.append(options)
        return BuildSession(
            root=root,
            shadow=root / "build/ici-cmake-tsan",
            variant=options.variant,
            descriptor="CMakeLists.txt",
        )

    monkeypatch.setattr("ici.engines.sanitize.adapter_configure", fake_configure)

    result = ThreadSanitizeEngine(tmp_path)._run_cpp_sanitizer_via_adapter([])

    assert result is False
    assert len(seen) == 1
    assert seen[0].variant is BuildVariant.THREAD_SANITIZE
    assert seen[0].coverage is False


@pytest.mark.parametrize(
    ("backend", "descriptor", "process_name"),
    [
        ("cmake", "CMakeLists.txt", "ctest"),
        ("qmake", "app.pro", "make check"),
    ],
)
def test_adapter_thread_sanitize_keeps_process_linked_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    descriptor: str,
    process_name: str,
) -> None:
    source, _test = _write_cpp_project(tmp_path)
    (tmp_path / descriptor).write_text("project(tsan)\n", encoding="utf-8")
    transcript = (
        "WARNING: ThreadSanitizer: data race\n"
        f"    #0 write_value {source}:1\n"
        f"SUMMARY: ThreadSanitizer: data race {source}:1 in write_value\n"
    )
    session = BuildSession(
        root=tmp_path,
        shadow=tmp_path / f"build/ici-{backend}-tsan",
        variant=BuildVariant.THREAD_SANITIZE,
        backend=backend,
        descriptor=descriptor,
        configured=True,
        tool_evidence=[ToolEvidence(name=f"{backend} configure", path=f"/usr/bin/{backend}")],
    )

    monkeypatch.setattr("ici.engines.sanitize.adapter_configure", lambda *_args: session)
    monkeypatch.setattr("ici.engines.sanitize.adapter_build", lambda _session: True)

    def fake_run_tests(_session, env=None):
        assert env is not None and "halt_on_error=1" in env["TSAN_OPTIONS"]
        session.tool_evidence.append(
            ToolEvidence(
                name=process_name,
                path=f"/usr/bin/{process_name.split()[0]}",
                argv=[f"/usr/bin/{process_name.split()[0]}"],
            )
        )
        return [
            TestCaseResult(
                "test_race",
                False,
                "ThreadSanitizer diagnostic",
                diagnostic_output=transcript,
            )
        ]

    monkeypatch.setattr("ici.engines.sanitize.adapter_run_tests", fake_run_tests)

    result = ThreadSanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.FAIL
    assert result.extra["sanitizer_diagnostics"][0]["process_evidence_index"] == 1
    assert result.tool_evidence[1].name == process_name
    assert result.findings[0].tool_rule_id == "tsan.data-race"


@pytest.mark.parametrize(
    ("required", "expected_status"),
    [(True, EngineStatus.ERROR), (False, EngineStatus.SKIP)],
)
def test_unexecuted_adapter_case_honors_thread_sanitize_required_policy(
    tmp_path: Path,
    required: bool,
    expected_status: EngineStatus,
) -> None:
    engine = ThreadSanitizeEngine(
        tmp_path,
        {"engines": {"thread_sanitize": {"required": required}}},
    )
    session = BuildSession(
        root=tmp_path,
        shadow=tmp_path / "build/ici-cmake-tsan",
        variant=BuildVariant.THREAD_SANITIZE,
        backend="cmake",
        descriptor="CMakeLists.txt",
    )
    targets = []

    failed = engine._record_adapter_cases(
        targets,
        session,
        [TestCaseResult("disabled_race", False, "disabled", executed=False)],
        process_index=0,
    )

    assert failed is False
    assert targets[0].status is expected_status
    assert engine._skipped_scopes == 1
    assert bool(engine._tool_errors) is required


def test_real_thread_sanitizer_publishes_project_owned_data_race(tmp_path: Path) -> None:
    if shutil.which("g++") is None:
        message = "g++ is unavailable for the real ThreadSanitizer integration test"
        if os.environ.get("ICI_REQUIRE_BUILD_ADAPTERS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    source = tmp_path / "src/race.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int shared_value = 0;\n"
        "void write_shared() {\n"
        "    for (int i = 0; i < 100000; ++i) { ++shared_value; }\n"
        "}\n",
        encoding="utf-8",
    )
    test = tmp_path / "tests/test_race.cpp"
    test.parent.mkdir(parents=True)
    test.write_text(
        "#include <thread>\n"
        "void write_shared();\n"
        "int main() {\n"
        "    std::thread first(write_shared);\n"
        "    std::thread second(write_shared);\n"
        "    first.join();\n"
        "    second.join();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    result = ThreadSanitizeEngine(tmp_path).run()

    if result.status is EngineStatus.ERROR and os.environ.get("ICI_REQUIRE_BUILD_ADAPTERS") != "1":
        pytest.skip(f"ThreadSanitizer runtime unavailable: {result.summary}")
    assert result.status is EngineStatus.FAIL, result.summary
    assert result.extra["sanitizer_diagnostics"][0]["kind"] == "tsan"
    assert result.extra["sanitizer_diagnostics"][0]["defect"] == "data-race"
    assert result.extra["sanitizer_diagnostics"][0]["primary_location"]["path"] == "src/race.cpp"
