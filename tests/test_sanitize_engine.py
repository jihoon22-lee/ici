"""Tests for sanitizer tool failure handling and evidence contracts."""

import os
import shutil

import pytest

from ici.core.cmake import BACKEND_CMAKE, BuildSession, TestCaseResult
from ici.core.findings import findings_for_result
from ici.core.models import EngineStatus, EvidenceState, ToolEvidence
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


def test_generic_cpp_clean_message_preserves_the_published_sanitizer_contract(
    tmp_path, monkeypatch
):
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.cpp").write_text("int clean() { return 7; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_clean.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter(
        (
            ProcessResult(0, "", "", 0.01),
            ProcessResult(0, "", "", 0.01),
        )
    )
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: next(results),
    )

    result = SanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.PASS
    assert result.targets[0].target_name == "ASan/UBSan"
    assert result.targets[0].message == "AddressSanitizer and UndefinedBehaviorSanitizer completed"


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


def test_python_resource_warning_check_selects_pytest_suffix_test_files(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "resource_test.py").write_text("def test_resource():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: ProcessResult(0, "1 passed in 0.01s\n", "", 0.01),
    )

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS


def test_python_resource_warning_check_does_not_treat_only_skips_as_measured_pass(
    tmp_path, monkeypatch
):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_resource.py").write_text("def test_resource():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: ProcessResult(0, "3 skipped, 2 deselected in 0.01s\n", "", 0.01),
    )

    required_result = SanitizeEngine(tmp_path).run()
    optional_result = SanitizeEngine(tmp_path, {"engines": {"sanitize": {"required": False}}}).run()

    assert required_result.status == EngineStatus.ERROR
    assert optional_result.status == EngineStatus.SKIP
    assert optional_result.evidence == EvidenceState.ESTIMATED


def test_python_resource_warning_check_does_not_add_empty_pythonpath(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_resource.py").write_text("def test_resource():\n    pass\n", encoding="utf-8")
    monkeypatch.delenv("PYTHONPATH", raising=False)
    seen = {}

    def fake_run(command, **kwargs):
        seen["env"] = kwargs["env"]
        return ProcessResult(0, "1 passed in 0.01s\n", "", 0.01)

    monkeypatch.setattr("ici.engines.sanitize.run_process", fake_run)

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert seen["env"]["PYTHONPATH"] == str(src)
    assert not seen["env"]["PYTHONPATH"].endswith(":")


def test_python_resource_warning_check_reuses_wsl_temp_environment_policy(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_resource.py").write_text("def test_resource():\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "host-temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "host-temp"))
    monkeypatch.setenv("TEMP", str(tmp_path / "host-temp"))
    seen = {}

    def fake_run(command, **kwargs):
        seen["env"] = kwargs["env"]
        return ProcessResult(0, "1 passed in 0.01s\n", "", 0.01)

    monkeypatch.setattr("ici.engines.sanitize.run_process", fake_run)

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert all(seen["env"][key] == "/tmp" for key in ("TMPDIR", "TMP", "TEMP"))


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


def test_cpp_sanitizer_diagnostic_with_zero_exit_is_measured_failure(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter(
        [
            ProcessResult(0, "", "", 0.01),
            ProcessResult(
                0,
                "",
                f"{source}:1:5: runtime error: signed integer overflow",
                0.01,
            ),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.MEASURED
    assert any(target.target_name == "ASan/UBSan Error" for target in result.targets)


def test_cpp_sanitizer_publishes_kind_location_stack_and_process_evidence(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "worker.cpp"
    source.write_text("int one;\nint two;\nint worker() { return one; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    test_source = tests / "test_worker.cpp"
    test_source.write_text("int helper;\nint main() { return helper; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = f"""==7==ERROR: AddressSanitizer: heap-use-after-free on address 0x1234
    #0 0x1 in runtime /usr/lib/asan.cpp:1
    #1 0x2 in worker {source}:3:5
    #2 0x3 in main {test_source}:2
SUMMARY: AddressSanitizer: heap-use-after-free {source}:3 in worker
"""
    results = iter(
        [
            ProcessResult(0, "", "", 0.01),
            ProcessResult(-6, "", diagnostic, 0.01),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert result.targets[0].file_path == "src/worker.cpp"
    assert result.targets[0].start_line == 3
    assert result.targets[0].start_column == 5
    details = result.extra["sanitizer_diagnostics"]
    assert details == [
        {
            "kind": "asan",
            "tool_name": "AddressSanitizer",
            "defect": "heap-use-after-free",
            "rule_id": "ici.sanitize.asan.heap-use-after-free",
            "message": "AddressSanitizer detected heap use after free",
            "test_name": "test_worker.cpp",
            "process_evidence_index": 1,
            "frames_observed": 3,
            "project_frames": 2,
            "primary_location": {
                "path": "src/worker.cpp",
                "start_line": 3,
                "start_column": 5,
            },
            "related_locations": [
                {
                    "path": "[external]",
                    "start_line": 1,
                    "start_column": None,
                    "label": "frame #0: runtime",
                },
                {
                    "path": "tests/test_worker.cpp",
                    "start_line": 2,
                    "start_column": None,
                    "label": "frame #2: main",
                },
            ],
        }
    ]
    assert result.tool_evidence[1].name == "sanitizer execution"
    assert result.findings[0].tool_rule_id == "asan.heap-use-after-free"
    assert result.findings[0].tool_name == "AddressSanitizer"
    assert any(
        location.path == "tests/test_worker.cpp"
        for location in result.findings[0].related_locations
    )
    normalized = findings_for_result(result, project_root=tmp_path)
    assert len(normalized) == 1
    assert normalized[0].confidence.value == "exact"


def test_adapter_sanitizer_trace_is_normalized_with_ctest_process_evidence(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    source = src / "fault.cpp"
    source.write_text("int one;\nint fault() { return one; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fault.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    session = BuildSession(
        root=tmp_path,
        shadow=tmp_path / "build/ici-cmake-asan",
        backend=BACKEND_CMAKE,
        descriptor="CMakeLists.txt",
        reason="root CMakeLists.txt",
        configured=True,
    )
    output = f"""{source}:2:5: runtime error: signed integer overflow: 1 + 2
    #0 0x1 in fault {source}:2:5
"""

    def fake_tests(actual_session, env=None):
        actual_session.tool_evidence.append(
            ToolEvidence(name="ctest", path="/usr/bin/ctest", argv=["ctest"], returncode=1)
        )
        return [
            TestCaseResult(
                "test_fault",
                False,
                "UndefinedBehaviorSanitizer diagnostic",
                diagnostic_output=output,
            )
        ]

    monkeypatch.setattr("ici.engines.sanitize.adapter_configure", lambda *_args: session)
    monkeypatch.setattr("ici.engines.sanitize.adapter_build", lambda _session: True)
    monkeypatch.setattr("ici.engines.sanitize.adapter_run_tests", fake_tests)

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert result.extra["sanitizer_diagnostics"][0]["kind"] == "ubsan"
    assert result.extra["sanitizer_diagnostics"][0]["process_evidence_index"] == 0
    assert result.targets[0].file_path == "src/fault.cpp"
    assert result.targets[0].start_line == 2
    assert result.findings[0].tool_rule_id == "ubsan.signed-integer-overflow"


def test_adapter_sanitizer_rejects_truncated_private_diagnostic_transport(tmp_path, monkeypatch):
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    source = src / "fault.cpp"
    source.write_text("int fault() { return 1; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fault.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    session = BuildSession(
        root=tmp_path,
        shadow=tmp_path / "build/ici-cmake-asan",
        backend=BACKEND_CMAKE,
        descriptor="CMakeLists.txt",
        reason="root CMakeLists.txt",
        configured=True,
    )

    def fake_tests(actual_session, env=None):
        actual_session.tool_evidence.append(
            ToolEvidence(name="ctest", path="/usr/bin/ctest", argv=["ctest"], returncode=1)
        )
        return [
            TestCaseResult(
                "test_fault",
                False,
                "AddressSanitizer diagnostic",
                diagnostic_output=(
                    "ERROR: AddressSanitizer: heap-use-after-free\n"
                    f"    #0 0x1 in fault {source}:1:5"
                ),
                diagnostic_output_truncated=True,
            )
        ]

    monkeypatch.setattr("ici.engines.sanitize.adapter_configure", lambda *_args: session)
    monkeypatch.setattr("ici.engines.sanitize.adapter_build", lambda _session: True)
    monkeypatch.setattr("ici.engines.sanitize.adapter_run_tests", fake_tests)

    result = SanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["sanitizer_diagnostics"] == []
    assert "truncated" in result.summary


@pytest.mark.parametrize(
    ("diagnostic", "expected_status"),
    [
        ("the test mentions AddressSanitizer but has no failure", EngineStatus.PASS),
        (
            "ERROR: LeakSanitizer: detected memory leaks\nSUMMARY: AddressSanitizer",
            EngineStatus.ERROR,
        ),
        (
            "/tmp/test.cpp:8:5: runtime error: signed integer overflow",
            EngineStatus.ERROR,
        ),
    ],
)
def test_sanitizer_diagnostic_requires_a_real_report_signature(
    diagnostic, expected_status, tmp_path, monkeypatch
):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter(
        [
            ProcessResult(0, "", "", 0.01),
            ProcessResult(0, "", diagnostic, 0.01),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == expected_status


def test_cpp_sanitizer_passes_configured_include_flags_to_compile(tmp_path, monkeypatch):
    src = tmp_path / "custom-src"
    include = tmp_path / "custom-include"
    src.mkdir()
    include.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    seen = {}

    def fake_includes():
        seen["config"] = config
        return [f"-I{include}"]

    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda command, **kwargs: (
            (seen.setdefault("command", command) or True) and ProcessResult(0, "", "", 0.01)
        ),
    )
    config = {
        "project": {"source_dirs": ["custom-src"], "type": "cpp"},
        "engines": {"sanitize": {"required": False}},
    }

    engine = SanitizeEngine(tmp_path, config)
    monkeypatch.setattr(engine, "project_type", lambda: "cpp")
    monkeypatch.setattr(engine, "project_cpp_include_flags", fake_includes)

    result = engine.run()

    assert result.status == EngineStatus.PASS
    assert seen["config"] is config
    assert f"-I{include}" in seen["command"]


def test_cpp_sanitizer_nonzero_unlocated_diagnostic_is_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter(
        [
            ProcessResult(0, "", "", 0.01),
            ProcessResult(1, "", "ERROR: AddressSanitizer: heap-use-after-free", 0.01),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


@pytest.mark.parametrize(
    "run_result",
    [
        ProcessResult(124, "", "timed out", 0.01, timed_out=True),
        ProcessResult(0, "partial AddressSanitizer", "", 0.01, truncated=True),
    ],
)
def test_cpp_sanitizer_execution_timeout_or_truncation_is_error(tmp_path, monkeypatch, run_result):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter([ProcessResult(0, "", "", 0.01), run_result])
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_sanitizer_signal_with_complete_diagnostic_is_measured_failure(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "main.cpp"
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter(
        [
            ProcessResult(0, "", "", 0.01),
            ProcessResult(
                -6,
                "",
                f"ERROR: AddressSanitizer: heap-use-after-free\n    #0 0x1 in main {source}:1:5",
                0.01,
            ),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.MEASURED


def test_cpp_sanitizer_signal_without_diagnostic_is_error(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter([ProcessResult(0, "", "", 0.01), ProcessResult(-6, "", "", 0.01)])
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_resource_warning_preserves_windows_drive_and_spaces_in_path(tmp_path):
    engine = SanitizeEngine(tmp_path)
    targets = []

    found = engine._resource_warning_targets(
        r"C:\work dir\project\tests\test_resource.py:42: ResourceWarning: unclosed file\n",
        targets,
    )

    assert found
    assert targets[0].file_path == r"C:\work dir\project\tests\test_resource.py"
    assert targets[0].start_line == 42


def test_hybrid_partial_cpp_skip_is_warn_and_estimated_when_optional(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: ProcessResult(0, "1 passed in 0.01s\n", "", 0.01),
    )

    result = SanitizeEngine(
        tmp_path,
        {"engines": {"sanitize": {"required": False}}},
    ).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(target.target_name == "C++Sanitizer" for target in result.targets)


def test_hybrid_partial_python_skip_is_warn_and_estimated_when_optional(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    results = iter(
        [
            ProcessResult(0, "", "", 0.01),
            ProcessResult(0, "", "", 0.01),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(
        tmp_path,
        {"engines": {"sanitize": {"required": False}}},
    ).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(target.target_name == "PythonResourceWarnings" for target in result.targets)


def test_sanitize_default_mode_matches_pass_fail_policy(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    pass\n", encoding="utf-8")
    engine = SanitizeEngine(tmp_path)

    def fake_check(_tests_root, _targets):
        engine._measured_scopes = 1
        return False, True

    monkeypatch.setattr(engine, "_check_python_resource_warnings", fake_check)

    result = engine.run()

    assert result.status == EngineStatus.FAIL


def test_cpp_sanitizer_execution_sets_options_without_clobbering_environment(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setenv("ASAN_OPTIONS", "color=always")
    monkeypatch.setenv("UBSAN_OPTIONS", "print_stacktrace=1")
    seen_envs = []

    def fake_run(command, **kwargs):
        if command[0] != "/usr/bin/g++":
            seen_envs.append(kwargs.get("env", {}))
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.sanitize.run_process", fake_run)

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert seen_envs
    assert "color=always" in seen_envs[0]["ASAN_OPTIONS"]
    assert "detect_leaks=1" in seen_envs[0]["ASAN_OPTIONS"]
    assert "print_stacktrace=1" in seen_envs[0]["UBSAN_OPTIONS"]
    assert "halt_on_error=1" in seen_envs[0]["UBSAN_OPTIONS"]


def test_cpp_sanitizer_compile_spawn_failure_records_one_evidence_item(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_cpp.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )

    def fail_spawn(*args, **kwargs):
        raise OSError("compile spawn failed")

    monkeypatch.setattr("ici.engines.sanitize.run_process", fail_spawn)

    result = SanitizeEngine(tmp_path).run()

    compile_evidence = [e for e in result.tool_evidence if e.name == "sanitizer compile"]
    assert result.status == EngineStatus.ERROR
    assert len(compile_evidence) == 1
    assert compile_evidence[0].error == "OSError: compile spawn failed"


def test_hybrid_partial_scope_preserves_measured_resource_failure(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("value = 1\n", encoding="utf-8")
    (src / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("def test_app():\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(
        "ici.engines.sanitize.run_process",
        lambda *args, **kwargs: ProcessResult(
            1,
            "1 failed in 0.01s\n",
            "ResourceWarning: unclosed file\n",
            0.01,
        ),
    )

    result = SanitizeEngine(
        tmp_path,
        {"engines": {"sanitize": {"required": False}}},
    ).run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(target.target_name == "ResourceWarning" for target in result.targets)


def test_sanitizer_spawn_error_recording_is_idempotent(tmp_path):
    engine = SanitizeEngine(tmp_path)
    command = ["/usr/bin/g++", "-c", "test.cpp"]
    error = OSError("compile spawn failed")

    engine._record_tool_exception("sanitizer compile", command, error)
    engine._record_tool_exception("sanitizer compile", command, error)

    assert len(engine._tool_evidence) == 1
    assert engine._tool_errors == ["OSError: compile spawn failed"]


def test_cpp_test_sources_exclude_external_symlinks(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    inside = tests / "test_inside.cpp"
    inside.write_text("int main() { return 0; }\n", encoding="utf-8")
    outside = tmp_path.parent / "outside-test.cpp"
    outside.write_text("int main() { return 1; }\n", encoding="utf-8")
    (tests / "test_external.cpp").symlink_to(outside)

    sources = SanitizeEngine(tmp_path)._cpp_test_sources()

    assert sources == [inside.resolve()]


def test_adapter_build_failure_is_not_reported_as_inapplicable(tmp_path, monkeypatch):
    """A sanitizer build that failed is an unmeasured scope, not an absent one.

    Appending an ERROR target is not enough on its own: with no measured and no
    skipped scopes the status logic falls through to SKIP/NOT_APPLICABLE, and a
    failed build gets reported as "this engine does not apply here". That is the
    inverse of the §3.2 rule — a scope that existed and was not measured has to
    keep blocking the gate.
    """
    (tmp_path / "CMakeLists.txt").write_text("project(x)\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.cpp").write_text("int twice(int a) { return a * 2; }\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_lib.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    session = BuildSession(
        root=tmp_path,
        shadow=tmp_path / "build" / "ici-cmake-asan",
        backend=BACKEND_CMAKE,
        descriptor="CMakeLists.txt",
        reason="root CMakeLists.txt",
        configured=True,
    )
    session.errors.append("cmake build failed: cannot specify -static with -fsanitize=address")
    monkeypatch.setattr(
        "ici.engines.sanitize.adapter_configure", lambda _root, _options=None: session
    )
    monkeypatch.setattr("ici.engines.sanitize.adapter_build", lambda _s: False)

    result = SanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert any("-static" in t.message for t in result.targets)


@pytest.mark.parametrize(
    ("implementation", "test_body", "expected_kind", "expected_defect"),
    [
        (
            "int fault() { int* value = new int(7); delete value; return *value; }\n",
            "int fault(); int main() { return fault() == 7 ? 0 : 1; }\n",
            "asan",
            "heap-use-after-free",
        ),
        (
            "int overflow(int lhs, int rhs) { return lhs + rhs; }\n",
            "#include <climits>\nint overflow(int, int);\n"
            "int main() { return overflow(INT_MAX, 1); }\n",
            "ubsan",
            "signed-integer-overflow",
        ),
        (
            "int* leak() { return new int(7); }\n",
            "int* leak(); int main() { (void)leak(); return 0; }\n",
            "lsan",
            "memory-leak",
        ),
    ],
    ids=["asan-use-after-free", "ubsan-overflow", "lsan-leak"],
)
def test_real_sanitizers_publish_project_owned_diagnostics(
    tmp_path,
    implementation,
    test_body,
    expected_kind,
    expected_defect,
):
    if shutil.which("g++") is None:
        message = "g++ is unavailable for the real sanitizer integration test"
        if os.environ.get("ICI_REQUIRE_BUILD_ADAPTERS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "fault.cpp").write_text(implementation, encoding="utf-8")
    (tests / "test_fault.cpp").write_text(test_body, encoding="utf-8")

    result = SanitizeEngine(tmp_path).run()

    assert result.status is EngineStatus.FAIL, result.summary
    diagnostics = result.extra["sanitizer_diagnostics"]
    assert len(diagnostics) == 1
    assert diagnostics[0]["kind"] == expected_kind
    assert diagnostics[0]["defect"] == expected_defect
    assert diagnostics[0]["primary_location"]["path"] == "src/fault.cpp"
    evidence_index = diagnostics[0]["process_evidence_index"]
    assert result.tool_evidence[evidence_index].name == "sanitizer execution"
