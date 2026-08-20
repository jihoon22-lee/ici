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
            ProcessResult(0, "", "/tmp/test.cpp:8:5: runtime error: signed integer overflow", 0.01),
        ]
    )
    monkeypatch.setattr("ici.engines.sanitize.run_process", lambda *args, **kwargs: next(results))

    result = SanitizeEngine(tmp_path).run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.MEASURED
    assert any(target.target_name == "ASan/UBSan Error" for target in result.targets)


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("the test mentions AddressSanitizer but has no failure", False),
        ("ERROR: LeakSanitizer: detected memory leaks\nSUMMARY: AddressSanitizer", True),
        ("/tmp/test.cpp:8:5: runtime error: signed integer overflow", True),
    ],
)
def test_sanitizer_diagnostic_requires_a_real_report_signature(
    diagnostic, expected, tmp_path, monkeypatch
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

    expected_status = EngineStatus.FAIL if expected else EngineStatus.PASS
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
    monkeypatch.setattr("ici.engines.sanitize.detect_project_type", lambda root: "cpp")
    seen = {}

    def fake_includes(root, config=None):
        seen["config"] = config
        return [f"-I{include}"]

    monkeypatch.setattr("ici.engines.sanitize.get_all_cpp_includes", fake_includes)
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

    result = SanitizeEngine(tmp_path, config).run()

    assert result.status == EngineStatus.PASS
    assert seen["config"] is config
    assert f"-I{include}" in seen["command"]


def test_cpp_sanitizer_nonzero_diagnostic_is_measured_failure(tmp_path, monkeypatch):
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

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.MEASURED


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
            ProcessResult(-6, "", "ERROR: AddressSanitizer: heap-use-after-free", 0.01),
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
