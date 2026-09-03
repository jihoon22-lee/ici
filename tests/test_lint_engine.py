"""Tests for lint tool failure handling and execution evidence."""

from pathlib import Path

import pytest

from ici.core.findings import findings_for_result
from ici.core.models import (
    EngineStatus,
    EvidenceState,
    FindingCategory,
    FindingConfidence,
    InspectionTarget,
)
from ici.core.runner import ProcessResult
from ici.engines._cpp_diagnostics import CppDiagnostic
from ici.engines.lint import LintEngine


def _compiler_only_lint_config():
    return {"engines": {"lint": {"clang_tidy": "off", "clazy": "off"}}}


def _use_ruff(monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/ruff" if name == "ruff" else None,
    )


@pytest.mark.parametrize(
    ("family", "rule", "category"),
    [
        ("compiler", "cert-env33-c", FindingCategory.CORRECTNESS),
        ("unknown", "clang-analyzer-security.insecureapi", FindingCategory.CORRECTNESS),
        ("clang-analyzer", "clang-analyzer-core.NullDereference", FindingCategory.CORRECTNESS),
        ("clang-analyzer", "clang-analyzer-security.insecureAPI.strcpy", FindingCategory.SECURITY),
        ("clang-analyzer", "clang-analyzer-alpha.security.ArrayBoundV2", FindingCategory.SECURITY),
        ("clang-analyzer", "clang-analyzer-optin.taint.GenericTaint", FindingCategory.SECURITY),
        (
            "clang-analyzer",
            "clang-analyzer-alpha.core.UseAfterLifetimeEnd",
            FindingCategory.RESOURCE,
        ),
        ("clang-analyzer", "clang-analyzer-alpha.cplusplus.SmartPtr", FindingCategory.RESOURCE),
        ("clang-analyzer", "clang-analyzer-cplusplus.ArrayDelete", FindingCategory.RESOURCE),
        ("clang-analyzer", "clang-analyzer-cplusplus.NewDeleteLeaks", FindingCategory.RESOURCE),
        ("clang-analyzer", "clang-analyzer-fuchsia.HandleChecker", FindingCategory.RESOURCE),
        ("clang-analyzer", "clang-analyzer-unix.Malloc", FindingCategory.RESOURCE),
        ("clang-analyzer", "clang-analyzer-unix.Stream", FindingCategory.RESOURCE),
        (
            "clang-analyzer",
            "clang-analyzer-webkit.UncountedLambdaCapturesChecker",
            FindingCategory.RESOURCE,
        ),
        ("clang-tidy", "CERT-ENV33-C", FindingCategory.SECURITY),
        ("clang-tidy", "android-cloexec-open", FindingCategory.SECURITY),
        ("clang-tidy", "bugprone-command-processor", FindingCategory.SECURITY),
        ("clang-tidy", "concurrency-mt-unsafe", FindingCategory.SECURITY),
        ("clang-tidy", "bugprone-dangling-handle", FindingCategory.RESOURCE),
        ("clang-tidy", "bugprone-suspicious-realloc-usage", FindingCategory.RESOURCE),
        ("clang-tidy", "bugprone-unused-raii", FindingCategory.RESOURCE),
        ("clang-tidy", "bugprone-use-after-move", FindingCategory.RESOURCE),
        ("clang-tidy", "cppcoreguidelines-owning-memory", FindingCategory.RESOURCE),
        ("clang-tidy", "bugprone-infinite-loop", FindingCategory.CORRECTNESS),
        ("clang-tidy", "bugprone-branch-clone", FindingCategory.CORRECTNESS),
        ("clang-tidy", "concurrency-thread-canceltype-asynchronous", FindingCategory.CORRECTNESS),
        ("clang-tidy", "portability-simd-intrinsics", FindingCategory.COMPATIBILITY),
        ("clang-tidy", "modernize-deprecated-headers", FindingCategory.COMPATIBILITY),
        ("clang-tidy", "modernize-use-nullptr", FindingCategory.MAINTAINABILITY),
        ("clazy", "clazy-lifetime-issue", FindingCategory.RESOURCE),
        ("clazy", "clazy-connect-3arg-lambda", FindingCategory.RESOURCE),
        ("clazy", "clazy-ctor-missing-parent-argument", FindingCategory.RESOURCE),
        ("clazy", "clazy-returning-data-from-temporary", FindingCategory.RESOURCE),
        ("clazy", "clazy-qobject-cast", FindingCategory.RESOURCE),
        ("clazy", "clazy-qt6-deprecated-api-fixes", FindingCategory.COMPATIBILITY),
        ("clazy", "clazy-old-style-connect", FindingCategory.COMPATIBILITY),
        ("clazy", "clazy-qstring-ref", FindingCategory.COMPATIBILITY),
        ("clazy", "clazy-connect-non-signal", FindingCategory.CORRECTNESS),
        ("clazy", "clazy-child-event-qobject-cast", FindingCategory.CORRECTNESS),
        ("clazy", "clazy-install-event-filter", FindingCategory.CORRECTNESS),
        ("clazy", "clazy-not-lifetime", FindingCategory.MAINTAINABILITY),
        ("clazy", "clazy-range-loop-detach", FindingCategory.MAINTAINABILITY),
    ],
)
def test_cpp_diagnostic_categories_use_only_bounded_rule_names(
    family: str,
    rule: str,
    category: FindingCategory,
) -> None:
    diagnostic = CppDiagnostic(
        target=InspectionTarget(
            file_path="src/main.cpp",
            start_line=1,
            status=EngineStatus.WARN,
            message="free-form text must not affect the category",
        ),
        tool_rule_id=rule,
        family=family,
    )

    assert LintEngine._cpp_finding_category(diagnostic) is category


def test_cpp_diagnostic_category_policy_participates_in_cache_identity() -> None:
    assert "ici.engines._cpp_diagnostic_categories" in LintEngine.CACHE_IMPLEMENTATION_MODULES


def test_cpp_source_scope_does_not_activate_python_lint(tmp_cpp_project, monkeypatch):
    """Python outside configured C++ sources must not trigger lint fallback."""
    benchmark = tmp_cpp_project / "benchmarks"
    benchmark.mkdir()
    (benchmark / "out.py").write_text("def broken(:\n", encoding="utf-8")
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {"lint": {"ruff_required": False, "clang_tidy": "off"}},
    }
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)
    monkeypatch.setattr(LintEngine, "_find_ruff_command", lambda _self: None)

    result = LintEngine(tmp_cpp_project, config).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["cpp_analysis_mode"] == "heuristic"
    assert result.extra["python_files_parsed"] == 0
    assert not any(e.name in {"ruff", "ruff check", "ruff format"} for e in result.tool_evidence)
    assert not any(
        target.target_name in {"SyntaxError", "ASTSyntaxFallback"} for target in result.targets
    )
    assert all("out.py" not in str(command) for command in calls)


def test_ruff_commands_receive_only_scoped_relative_python_paths(tmp_python_project, monkeypatch):
    """Ruff and syntax parsing must use the configured Python source inventory."""
    benchmark = tmp_python_project / "benchmarks"
    benchmark.mkdir()
    (benchmark / "out.py").write_text("def broken(:\n", encoding="utf-8")
    config = {"project": {"source_dirs": ["src"]}}
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "format" in cmd and "--help" in cmd:
            return ProcessResult(0, "--output-format <OUTPUT_FORMAT>\n", "", 0.01)
        if "format" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "[]\n", "", 0.01)

    _use_ruff(monkeypatch)
    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project, config).run()

    expected = {"src/sample_pkg/__init__.py", "src/sample_pkg/core.py"}
    check_commands = [
        command for command in calls if "check" in command and "format" not in command
    ]
    format_commands = [
        command for command in calls if "format" in command and "--help" not in command
    ]
    assert result.status == EngineStatus.PASS
    assert result.extra["python_files_parsed"] == len(expected)
    assert len(check_commands) == 1
    assert len(format_commands) == 1
    for command in [*check_commands, *format_commands]:
        python_paths = [argument for argument in command if argument.endswith(".py")]
        assert set(python_paths) == expected
        assert "." not in command
        assert "benchmarks/out.py" not in command
        assert all(not Path(path).is_absolute() for path in python_paths)


def test_ruff_truncated_json_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, '[{"filename": "src/core.py"', "", 0.01, truncated=True)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_timeout_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(124, "", "Command timed out", 0.05, timed_out=True)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_violation_exit_without_violations_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(1, "[]", "", 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_success_without_json_is_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "", "", 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    check_evidence = next(e for e in result.tool_evidence if e.name == "ruff check")
    assert "parseable JSON" in check_evidence.error


def test_ruff_clean_format_summary_is_success(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "2 files already formatted\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.PASS


def test_ruff_empty_format_success_is_accepted(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.PASS


def test_ruff_format_parse_failure_records_error(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "unexpected format output\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    format_evidence = next(e for e in result.tool_evidence if e.name == "ruff format")
    assert "not parseable" in format_evidence.error


def test_ruff_finding_with_format_warning_is_a_policy_failure(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    format_warning = (
        "warning: The following rule may cause conflicts when used with the formatter: COM812\n"
    )

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(
                1,
                (
                    '[{"filename":"src/sample_pkg/core.py",'
                    '"location":{"row":2},"code":"E501","message":"line too long"}]'
                ),
                "",
                0.01,
            )
        return ProcessResult(0, "1 file already formatted\n", format_warning, 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.FAIL
    finding = next(target for target in result.targets if target.target_name == "Ruff:E501")
    assert finding.file_path == "src/sample_pkg/core.py"
    assert finding.start_line == 2
    assert "Ruff format output was not parseable" not in result.summary
    assert all(
        "Ruff format output was not parseable" not in item.error for item in result.tool_evidence
    )


def test_ruff_01517_preview_only_format_json_flag_uses_legacy_output(
    tmp_python_project, monkeypatch
):
    _use_ruff(monkeypatch)
    format_warning = (
        "warning: The following rule may cause conflicts when used with the formatter: COM812\n"
    )
    help_excerpt = """      --output-format <OUTPUT_FORMAT>
          Output serialization format for violations, when used with `--check`.
          The default serialization format is \"full\".

          Note that this option is currently only respected in preview mode. A warning will be emitted if this flag is used on stable.
"""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "check" in cmd:
            return ProcessResult(
                1,
                (
                    '[{"filename":"src/sample_pkg/core.py",'
                    '"location":{"row":2},"code":"E501","message":"line too long"}]'
                ),
                "",
                0.01,
            )
        if "--help" in cmd:
            return ProcessResult(0, help_excerpt, "", 0.01)
        return ProcessResult(0, "1 file already formatted\n", format_warning, 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.FAIL
    assert any(target.target_name == "Ruff:E501" for target in result.targets)
    format_evidence = next(item for item in result.tool_evidence if item.name == "ruff format")
    assert "--output-format=json" not in format_evidence.argv
    assert "Ruff format output was not parseable" not in format_evidence.error
    assert any("--help" in cmd for cmd in calls)


def test_ruff_check_warning_is_preserved_as_warn(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "warning: check configuration notice\n", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "legacy format help\n", "", 0.01)
        return ProcessResult(0, "1 file already formatted\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    assert "warning: check configuration notice" in result.summary
    assert result.evidence == EvidenceState.ESTIMATED


def test_ruff_format_multiline_warning_is_preserved_as_warn(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)
    warning = "warning: formatter configuration notice\n  - first recommendation\n  - second recommendation\n"

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "legacy format help\n", "", 0.01)
        return ProcessResult(0, "1 file already formatted\n", warning, 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    assert "warning: formatter configuration notice\n  - first recommendation" in result.summary
    assert "second recommendation" in result.summary


@pytest.mark.parametrize(
    "stderr,command_part",
    [
        ("notice: unexpected stderr\n", "check"),
        ("warning: recognized warning\ncontinued without indentation\n", "check"),
        ("warning: recognized warning\ncontinued without indentation\n", "format"),
    ],
)
def test_ruff_unrecognized_stderr_is_tool_error(
    tmp_python_project, monkeypatch, stderr, command_part
):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "--help" in cmd:
            return ProcessResult(0, "legacy format help\n", "", 0.01)
        if command_part == "check" and "check" in cmd:
            return ProcessResult(0, "[]\n", stderr, 0.01)
        if command_part == "format" and "check" not in cmd:
            return ProcessResult(0, "1 file already formatted\n", stderr, 0.01)
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        return ProcessResult(0, "1 file already formatted\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_ruff_legacy_unformatted_output_keeps_format_target(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "legacy format help\n", "", 0.01)
        return ProcessResult(
            1,
            "Would reformat: src/sample_pkg/core.py\n1 file would be reformatted\n",
            "",
            0.01,
        )

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    target = next(target for target in result.targets if target.target_name == "Format:Style")
    assert target.file_path == "src/sample_pkg/core.py"
    assert target.start_line == 1


def test_ruff_01517_mixed_legacy_format_summary_is_a_policy_warning(
    tmp_python_project, monkeypatch
):
    _use_ruff(monkeypatch)
    help_excerpt = """      --output-format <OUTPUT_FORMAT>
          Output serialization format for violations, when used with `--check`.
          The default serialization format is "full".

          Note that this option is currently only respected in preview mode. A warning will be emitted if this flag is used on stable.
"""

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, help_excerpt, "", 0.01)
        return ProcessResult(
            1,
            "Would reformat: src/sample_pkg/core.py\n"
            "1 file would be reformatted, 1 file already formatted\n",
            "",
            0.01,
        )

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    format_targets = [target for target in result.targets if target.target_name == "Format:Style"]
    assert len(format_targets) == 1
    assert format_targets[0].file_path == "src/sample_pkg/core.py"
    format_evidence = next(item for item in result.tool_evidence if item.name == "ruff format")
    assert "Ruff format output was not parseable" not in format_evidence.error


@pytest.mark.parametrize(
    "paths,summary",
    [
        (["src/sample_pkg/core.py"], "1 file would be reformatted, 1 file already formatted"),
        (["src/sample_pkg/core.py"], "1 file would be reformatted, 2 files already formatted"),
        (
            ["src/sample_pkg/core.py", "src/sample_pkg/other.py"],
            "2 files would be reformatted, 1 file already formatted",
        ),
        (
            ["src/sample_pkg/core.py", "src/sample_pkg/other.py"],
            "2 files would be reformatted, 2 files already formatted",
        ),
    ],
)
def test_ruff_legacy_format_summary_accepts_exact_mixed_suffix(
    tmp_python_project, monkeypatch, paths, summary
):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "legacy format help\n", "", 0.01)
        output = "".join(f"Would reformat: {path}\n" for path in paths) + f"{summary}\n"
        return ProcessResult(1, output, "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    assert [
        target.file_path for target in result.targets if target.target_name == "Format:Style"
    ] == paths


@pytest.mark.parametrize(
    "stdout",
    [
        "Would reformat: src/sample_pkg/core.py\n0 files would be reformatted\n",
        "Would reformat: src/sample_pkg/core.py\n1 files would be reformatted\n",
        "Would reformat: src/sample_pkg/core.py\n1 file would be reformatted, 2 file already formatted\n",
        "Would reformat: src/sample_pkg/core.py\n2 files would be reformatted, 1 files already formatted\n",
        "Would reformat: src/sample_pkg/core.py\n2 files would be reformatted\n",
        "Would reformat: src/sample_pkg/core.py\n1 file would be reformatted\n"
        "1 file would be reformatted\n",
        "1 file would be reformatted\nWould reformat: src/sample_pkg/core.py\n",
        "Would reformat: src/sample_pkg/core.py\nunexpected output\n1 file would be reformatted\n",
        "Would reformat: \n1 file would be reformatted\n",
    ],
)
def test_ruff_legacy_format_summary_rejects_malformed_output_atomically(
    tmp_python_project, monkeypatch, stdout
):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "legacy format help\n", "", 0.01)
        return ProcessResult(1, stdout, "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert not any(target.target_name == "Format:Style" for target in result.targets)
    format_evidence = next(item for item in result.tool_evidence if item.name == "ruff format")
    assert "Ruff format output was not parseable" in format_evidence.error


def test_ruff_json_format_success_uses_supported_output_format(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "--output-format <OUTPUT_FORMAT>\n", "", 0.01)
        return ProcessResult(0, "[]\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.PASS
    assert any("--output-format=json" in call and "format" in call for call in calls)
    format_evidence = next(
        item
        for item in result.tool_evidence
        if item.name == "ruff format" and "--output-format=json" in item.argv
    )
    assert format_evidence.returncode == 0


def test_ruff_json_format_finding_is_a_format_warning(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "--output-format <OUTPUT_FORMAT>\n", "", 0.01)
        return ProcessResult(
            1,
            '[{"filename":"src/sample_pkg/core.py","code":"unformatted",'
            '"location":{"row":4,"column":1},"message":"File would be reformatted"}]\n',
            "",
            0.01,
        )

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.WARN
    target = next(target for target in result.targets if target.target_name == "Format:Style")
    assert target.file_path == "src/sample_pkg/core.py"
    assert target.start_line == 4


@pytest.mark.parametrize(
    "stdout,returncode",
    [
        ("{not json}\n", 1),
        ('[{"filename":"src/sample_pkg/core.py","code":"E501","location":{"row":1}}]\n', 1),
        ('[{"filename":"src/sample_pkg/core.py","code":"unformatted","location":{"row":0}}]\n', 1),
        ('[{"filename":"src/sample_pkg/core.py","code":"unformatted","location":"bad"}]\n', 1),
        ('[{"filename":"src/sample_pkg/core.py","code":"unformatted","location":{"row":1}}]\n', 0),
    ],
)
def test_ruff_json_format_invalid_output_is_tool_error(
    tmp_python_project, monkeypatch, stdout, returncode
):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return ProcessResult(0, "--output-format <OUTPUT_FORMAT>\n", "", 0.01)
        return ProcessResult(returncode, stdout, "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


@pytest.mark.parametrize(
    "probe_result",
    [
        ProcessResult(124, "", "timed out", 0.01, timed_out=True),
        ProcessResult(0, "partial", "", 0.01, truncated=True),
        ProcessResult(2, "", "unsupported", 0.01),
    ],
)
def test_ruff_format_capability_probe_failure_is_not_legacy_fallback(
    tmp_python_project, monkeypatch, probe_result
):
    _use_ruff(monkeypatch)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        if "--help" in cmd:
            return probe_result
        raise AssertionError("legacy/JSON format validation must not run after probe failure")

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any("--help" in item.argv for item in result.tool_evidence)
    assert len(calls) == 2


def test_ruff_format_capability_probe_spawn_failure_is_recorded(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(0, "[]\n", "", 0.01)
        raise OSError("spawn failed")

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    probe = next(item for item in result.tool_evidence if "--help" in item.argv)
    assert "spawn failed" in probe.error


def test_optional_ruff_absence_uses_estimated_ast_fallback(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    result = LintEngine(
        tmp_python_project,
        {"engines": {"lint": {"ruff_required": False}}},
    ).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert any(e.name == "ruff" and e.returncode is None for e in result.tool_evidence)


def test_required_ruff_absence_is_error_and_not_run(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    result = LintEngine(
        tmp_python_project,
        {"engines": {"lint": {"ruff_required": True}}},
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(e.name == "ruff" and e.returncode is None for e in result.tool_evidence)


def test_ruff_uvx_and_uv_only_are_treated_as_missing(tmp_python_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/uvx" if name == "uvx" else None,
    )

    assert LintEngine(tmp_python_project)._find_ruff_command() is None


def test_ruff_finds_windows_style_project_venv_candidate(tmp_python_project, monkeypatch):
    scripts = tmp_python_project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    ruff = scripts / "ruff"
    ruff.write_text("#!/bin/sh\n", encoding="utf-8")
    ruff.chmod(0o755)
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    assert LintEngine(tmp_python_project)._find_ruff_command() == [str(ruff)]


def test_ruff_spawn_exception_records_both_attempts(tmp_python_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.LintEngine._find_ruff_command", lambda _self: ["ruff"])

    def raise_spawn(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr("ici.engines.lint.run_process", raise_spawn)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert [e.name for e in result.tool_evidence] == ["ruff check", "ruff format capability"]
    assert all(e.error for e in result.tool_evidence)


def test_ruff_exit_two_is_tool_error_even_with_json_diagnostic(tmp_python_project, monkeypatch):
    _use_ruff(monkeypatch)

    def fake_run(cmd, **kwargs):
        if "check" in cmd:
            return ProcessResult(
                2,
                '[{"filename":"src/sample_pkg/core.py","location":{"row":1},"code":"E1","message":"bad"}]',
                "",
                0.01,
            )
        return ProcessResult(0, "2 files already formatted\n", "", 0.01)

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)

    result = LintEngine(tmp_python_project).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    check_evidence = next(e for e in result.tool_evidence if e.name == "ruff check")
    assert "exit code 2" in check_evidence.error


def test_missing_gxx_for_discovered_cpp_is_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr("ici.engines.lint.shutil.which", lambda _name: None)

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert any(e.name == "g++" and e.returncode is None for e in result.tool_evidence)


def test_cpp_diagnostic_uses_reported_file_and_line(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    source = tmp_cpp_project / "src" / "main.cpp"
    diagnostic = f"{source}:12:7: error: expected ';'\n"
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.FAIL
    target = next(target for target in result.targets if target.target_name == "C++Syntax")
    assert target.file_path == "src/main.cpp"
    assert target.start_line == 12


def test_cpp_located_note_is_a_non_failure_diagnostic(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = (
        "src/main.cpp:12:7: error: expected ';'\n"
        "src/main.cpp:12:7: note: the declaration ends here\n"
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.FAIL
    cpp_targets = [target for target in result.targets if target.target_name == "C++Syntax"]
    assert any(target.status == EngineStatus.FAIL for target in cpp_targets)
    note = next(target for target in cpp_targets if target.message.startswith("note:"))
    assert note.status == EngineStatus.WARN
    assert note.file_path == "src/main.cpp"
    assert note.start_line == 12


def test_cpp_template_context_is_allowed_before_primary_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = (
        "src/main.cpp: In instantiation of \u2018void call(T) [with T = int]\u2019:\n"
        "src/main.cpp:12:5:   required from here\n"
        "src/main.cpp:5:10: error: invalid conversion\n"
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(1, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.FAIL
    assert result.evidence == EvidenceState.ESTIMATED
    error = next(
        target
        for target in result.targets
        if target.target_name == "C++Syntax" and target.status == EngineStatus.FAIL
    )
    assert error.file_path == "src/main.cpp"
    assert error.start_line == 5


def test_cpp_unrecognized_context_line_is_not_accepted(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = "src/main.cpp:12:7: warning: unused value\nsrc/main.cpp: In arbitrary context:\n"
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_signal_failure_is_tool_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(-9, "", "src/main.cpp:2: error: crash", 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN


def test_cpp_exit_two_records_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = "src/main.cpp:2:5: error: compiler crashed\n"
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(2, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.ERROR
    compiler_evidence = next(e for e in result.tool_evidence if e.name == "g++")
    assert "exit code 2" in compiler_evidence.error


def test_cpp_malformed_success_output_is_tool_error(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(0, "unexpected compiler output\n", "", 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    compiler_evidence = next(e for e in result.tool_evidence if e.name == "g++")
    assert "not parseable" in compiler_evidence.error


def test_cpp_warning_context_is_kept_as_a_finding(tmp_cpp_project, monkeypatch):
    monkeypatch.setattr(
        "ici.engines.lint.shutil.which",
        lambda name: "/usr/bin/g++" if name == "g++" else None,
    )
    diagnostic = (
        "src/main.cpp:3:5: warning: unused variable 'value'\n"
        "    3 | int value = 1;\n"
        "      |     ^~~~~\n"
        'fix-it:"src/main.cpp":{3:5-3:10}:""\n'
    )
    monkeypatch.setattr(
        "ici.engines.lint.run_process",
        lambda *args, **kwargs: ProcessResult(0, "", diagnostic, 0.01),
    )

    result = LintEngine(tmp_cpp_project, _compiler_only_lint_config()).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    target = next(target for target in result.targets if target.target_name == "C++Syntax")
    assert target.status == EngineStatus.WARN
    assert target.start_line == 3
    assert result.extra["cpp_fixits_total"] == 1
    assert result.extra["cpp_fixits"][0]["replacement"] == ""
    assert result.extra["cpp_diagnostic_category_policy"] == "tool-rule-v1"
    assert result.extra["cpp_diagnostic_categories"]["correctness"] == 1
    assert sum(result.extra["cpp_diagnostic_categories"].values()) == 1
    findings = findings_for_result(result, tmp_cpp_project)
    assert len(findings) == 1
    assert findings[0].category is FindingCategory.CORRECTNESS
    assert findings[0].confidence is FindingConfidence.MEDIUM
    assert findings[0].tool_name == "g++"
    assert "replace with ''" in findings[0].remediation


def _fallback_engine(tmp_path, monkeypatch) -> LintEngine:
    """A lint engine with no ruff available, so the AST fallback is what runs."""
    engine = LintEngine(tmp_path, {"engines": {"lint": {"ruff_required": False}}})
    monkeypatch.setattr(engine, "_find_ruff_command", lambda: None)
    return engine


def test_ast_fallback_reports_how_much_it_inspected(tmp_path, monkeypatch):
    """A clean project and one it never looked at both produced zero targets.

    The fallback only speaks up on a SyntaxError, so "no findings" and "nothing
    was examined" were indistinguishable in the report — which is the shape of
    every silent-verification bug found while dogfooding this tool.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("value = 1\n", encoding="utf-8")
    (src / "b.py").write_text("other = 2\n", encoding="utf-8")

    result = _fallback_engine(tmp_path, monkeypatch).run()

    assert result.extra["python_files_parsed"] == 2
    scope = next(t for t in result.targets if t.target_name == "ASTSyntaxFallback")
    assert scope.metrics["files_parsed"] == 2
    # It also has to say what it did *not* check, or the reader assumes lint ran.
    assert "not checked" in scope.message.lower()


def test_ast_fallback_distinguishes_an_empty_project(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()

    result = _fallback_engine(tmp_path, monkeypatch).run()

    assert result.status == EngineStatus.SKIP
    assert result.evidence == EvidenceState.NOT_APPLICABLE
    assert result.extra["python_files_parsed"] == 0
    scope = next(t for t in result.targets if t.target_name == "LintScope")
    assert scope.status == EngineStatus.SKIP
    assert "not run" in scope.message


def test_ast_fallback_still_reports_syntax_errors(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "broken.py").write_text("def oops(:\n", encoding="utf-8")

    result = _fallback_engine(tmp_path, monkeypatch).run()

    assert any(t.target_name == "SyntaxError" for t in result.targets)
    assert result.extra["python_files_parsed"] == 1
