"""Policy, replay, and diagnostic contracts for the clang-tidy adapter."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.findings import findings_for_result
from ici.core.models import EngineStatus, EvidenceState, FindingCategory
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._clang_tidy import run_clang_tidy
from ici.engines.lint import LintEngine


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve(strict=True)


def _include_search_output(*paths: Path) -> str:
    body = "\n".join(f" {path}" for path in paths)
    return f"COLLECT_GCC=g++\n#include <...> search starts here:\n{body}\nEnd of search list.\n"


def _project_context(
    tmp_path: Path, *, include_clang_tidy: bool = True
) -> tuple[Path, Path, AnalysisContext, Path | None]:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True, exist_ok=True)
    (root / "build").mkdir(parents=True, exist_ok=True)
    (root / "include").mkdir(parents=True, exist_ok=True)
    (root / "toolchain" / "cxx").mkdir(parents=True, exist_ok=True)
    (root / "toolchain" / "common").mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"int main() { return 0; }\n")

    compiler = _executable(tmp_path / "tools" / "g++")
    capabilities = {
        "g++": ToolCapability(
            name="g++",
            path=str(compiler),
            available=True,
            version="g++ (GCC) 14.2.0",
            version_tuple=(14, 2, 0),
            complete=True,
            returncode=0,
        )
    }
    tidy: Path | None = None
    if include_clang_tidy:
        tidy = _executable(tmp_path / "tools" / "clang-tidy")
        capabilities["clang-tidy"] = ToolCapability(
            name="clang-tidy",
            path=str(tidy),
            available=True,
            version="clang-tidy version 18.1.0",
            version_tuple=(18, 1, 0),
            complete=True,
            returncode=0,
        )

    unit = CompilationUnit(
        source="src/main.cpp",
        directory="build",
        argv=(
            str(compiler),
            "-std=c++20",
            "-D",
            "FEATURE=1",
            "-I",
            "../include",
            "-MMD",
            "-MF",
            "main.d",
            "-c",
            str(source),
            "-o",
            "main.o",
        ),
        output="build/main.o",
        compiler="g++",
        language="c++",
        standard="c++20",
        configuration=canonical_digest({"configuration": "test"}),
    )
    context = AnalysisContext(
        project=ProjectModel(
            root=root,
            name="clang-tidy-project",
            version="1.0.0",
            project_type="cpp",
            source_dirs=("src",),
            cpp_sources=("src/main.cpp",),
            compilable_cpp_sources=("src/main.cpp",),
        ),
        capabilities=CapabilityInventory(capabilities=capabilities),
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({"test": "config"}),
            toolchain_digest=canonical_digest({"test": "toolchain"}),
        ),
        compilation=CompilationContext(
            units=(unit,),
            database_path="build/compile_commands.json",
            database_digest=canonical_digest({"unit": unit.source}),
            origin="cmake",
            generator="Ninja",
            unity_build=False,
        ),
    )
    return root, source, context, tidy


def _run(
    root: Path,
    source: Path,
    context: AnalysisContext,
    config: dict[str, object],
    *,
    tidy_output: ProcessResult | None = None,
) -> tuple[object, list[tuple[list[str], dict[str, object]]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        calls.append((command, kwargs))
        return tidy_output or ProcessResult(0, "", "", 0.01)

    outcome = run_clang_tidy(
        root,
        [source],
        context,
        config,
        runner=runner,
    )
    return outcome, calls


def _with_compiler_arguments(context: AnalysisContext, *arguments: str) -> AnalysisContext:
    unit = context.compilation.units[0]
    compile_index = unit.argv.index("-c")
    updated = replace(
        unit,
        argv=(
            *unit.argv[:compile_index],
            *arguments,
            *unit.argv[compile_index:],
        ),
    )
    return replace(context, compilation=replace(context.compilation, units=(updated,)))


def test_off_mode_makes_no_command_and_no_evidence(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_clang_tidy(
        tmp_path,
        [],
        None,
        {"clang_tidy": "off"},
        runner=runner,
    )

    assert outcome.mode == "off"
    assert outcome.targets == []
    assert outcome.evidence == []
    assert calls == []


@pytest.mark.parametrize(
    ("mode", "status"),
    [("auto", EngineStatus.WARN), ("required", EngineStatus.ERROR)],
)
def test_missing_clang_tidy_observes_auto_or_required_policy(
    tmp_path: Path, mode: str, status: EngineStatus
) -> None:
    root, source, context, _tidy = _project_context(tmp_path, include_clang_tidy=False)

    outcome, calls = _run(root, source, context, {"clang_tidy": mode})

    assert outcome.mode == "unavailable"
    assert calls == []
    assert len(outcome.evidence) == 1
    assert outcome.evidence[0].error
    assert len(outcome.targets) == 1
    assert outcome.targets[0].target_name == "ClangTidyUnavailable"
    assert outcome.targets[0].status is status
    if status is EngineStatus.ERROR:
        assert outcome.errors
        assert outcome.warnings == []
    else:
        assert outcome.warnings
        assert outcome.errors == []


def test_approved_executable_receives_exact_sanitized_context_command(
    tmp_path: Path,
) -> None:
    root, source, context, tidy = _project_context(tmp_path)
    assert tidy is not None
    parent_config = root.parent / ".clang-tidy"
    parent_config.write_text("Checks: '-*,readability-*'\n", encoding="utf-8")
    before = source.read_bytes()
    config = {
        "clang_tidy": "auto",
        "clang_tidy_checks": ["-*", "bugprone-*", "performance-*"],
    }

    outcome, calls = _run(root, source, context, config)

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert outcome.sources_checked == 1
    stdlib_probes = [item for item in outcome.evidence if item.name == "g++ stdlib include search"]
    assert len(stdlib_probes) == 2
    assert [item.argv[item.argv.index("-x") + 1] for item in stdlib_probes] == ["c++", "c"]
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        str(tidy),
        "--use-color=false",
        "--config={}",
        "--checks=-*,bugprone-*,performance-*",
        str(source),
        "--",
        "-std=c++20",
        "-D",
        "FEATURE=1",
        "-I",
        "../include",
        "-fdiagnostics-color=never",
        "-nostdinc++",
        "-isystem",
        str(root / "toolchain" / "cxx"),
    ]
    assert "-p" not in command
    assert "compile_commands.json" not in command
    assert "--fix" not in command
    assert f"--config-file={parent_config}" not in command
    assert kwargs["cwd"] == root / "build"
    assert kwargs["input_text"] == ""
    assert kwargs["replace_env"] is True
    assert source.read_bytes() == before


def test_c_translation_unit_does_not_probe_or_project_cpp_standard_library(
    tmp_path: Path,
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    c_units = tuple(replace(unit, language="c") for unit in context.compilation.units)
    c_context = replace(context, compilation=replace(context.compilation, units=c_units))

    outcome, calls = _run(root, source, c_context, {"clang_tidy": "required"})

    assert outcome.mode == "exact"
    assert len(calls) == 1
    assert all(item.name != "g++ stdlib include search" for item in outcome.evidence)
    assert "-nostdinc++" not in calls[0][0]


def test_clang_tidy_demotes_build_warning_policy_but_preserves_selected_checks(
    tmp_path: Path,
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)

    outcome, calls = _run(
        root,
        source,
        _with_compiler_arguments(
            context,
            "-Werror",
            "-Werror=return-type",
            "-pedantic-errors",
            "--pedantic-errors",
            "-Werror-implicit-function-declaration",
            "-Wno-error=deprecated-declarations",
        ),
        {"clang_tidy": "auto"},
    )

    assert outcome.mode == "exact"
    assert len(calls) == 1
    command = calls[0][0]
    assert "-Werror" not in command
    assert "-Werror=return-type" not in command
    assert "-pedantic-errors" not in command
    assert "--pedantic-errors" not in command
    assert "-Werror-implicit-function-declaration" not in command
    assert "-Wreturn-type" in command
    assert "-pedantic" in command
    assert "-Wimplicit-function-declaration" in command
    assert "-Wno-error=deprecated-declarations" in command


@pytest.mark.parametrize(
    "argument",
    [
        "-Werror=p,-MD,/tmp/ici-tooling-dependency.d",
        "-Werror=l,-Map,/tmp/ici-tooling-link.map",
        "-Werror=a,-o,/tmp/ici-tooling-object.o",
    ],
)
def test_clang_tidy_rejects_unsafe_warning_projection_without_invocation(
    tmp_path: Path, argument: str
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)

    outcome, calls = _run(
        root,
        source,
        _with_compiler_arguments(context, argument),
        {"clang_tidy": "auto"},
    )

    assert calls == []
    assert outcome.mode == "error"
    assert any("unsafe-tooling-warning-policy" in error for error in outcome.errors)
    assert any(target.target_name == "ClangTidyReplayError" for target in outcome.targets)


def test_explicit_config_and_checks_take_precedence_in_command(tmp_path: Path) -> None:
    root, source, context, tidy = _project_context(tmp_path)
    assert tidy is not None
    discovered = root / ".clang-tidy"
    discovered.write_text("Checks: '-*,bugprone-*'\n", encoding="utf-8")
    explicit = root / "config" / "explicit.yaml"
    explicit.parent.mkdir(parents=True)
    explicit.write_text("Checks: '-*,readability-*'\n", encoding="utf-8")
    config = {
        "clang_tidy": "auto",
        "clang_tidy_checks": ["-*", "readability-*"],
        "clang_tidy_config": "config/explicit.yaml",
    }

    outcome, calls = _run(root, source, context, config)

    assert outcome.mode == "exact"
    assert len(calls) == 1
    command = calls[0][0]
    assert command[2] == f"--config-file={explicit.resolve(strict=True)}"
    assert command[3] == "--checks=-*,readability-*"
    assert "--config={}" not in command
    assert f"--config-file={discovered}" not in command
    assert "--checks=-*,bugprone-*,clang-analyzer-*,performance-*" not in command


def test_discovered_clang_tidy_config_suppresses_default_checks(tmp_path: Path) -> None:
    root, source, context, tidy = _project_context(tmp_path)
    assert tidy is not None
    (root / ".clang-tidy").write_text("Checks: '-*,readability-*'\n", encoding="utf-8")

    outcome, calls = _run(root, source, context, {"clang_tidy": "auto"})

    assert outcome.mode == "exact"
    assert len(calls) == 1
    command = calls[0][0]
    assert not any(argument.startswith("--checks=") for argument in command)
    assert command[2] == f"--config-file={(root / '.clang-tidy').resolve(strict=True)}"
    assert "--config={}" not in command


def test_clang_analyzer_and_clang_tidy_diagnostics_keep_distinct_families(
    tmp_path: Path,
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    diagnostic_output = (
        "src/main.cpp:3:5: warning: possible null dereference "
        "[clang-analyzer-core.NullDereference]\n"
        "src/main.cpp:4:5: warning: prefer nullptr [modernize-use-nullptr]\n"
    )

    outcome, calls = _run(
        root,
        source,
        context,
        {"clang_tidy": "auto"},
        tidy_output=ProcessResult(0, "", diagnostic_output, 0.01),
    )

    assert outcome.mode == "exact"
    assert len(calls) == 1
    assert [
        (diagnostic.family, diagnostic.tool_rule_id, diagnostic.target.target_name)
        for diagnostic in outcome.diagnostics
    ] == [
        (
            "clang-analyzer",
            "clang-analyzer-core.NullDereference",
            "ClangAnalyzer:clang-analyzer-core.NullDereference",
        ),
        ("clang-tidy", "modernize-use-nullptr", "ClangTidy:modernize-use-nullptr"),
    ]


@pytest.mark.parametrize(
    ("process_result", "error_fragment"),
    [
        (ProcessResult(0, "", "", 0.01, timed_out=True), "timed out"),
        (ProcessResult(0, "", "", 0.01, truncated=True), "truncated"),
        (ProcessResult(7, "", "", 0.01), "exit code 7"),
        (
            ProcessResult(
                0,
                "src/main.cpp:3:5: warning: valid [modernize-use-nullptr]\n"
                "not a compiler diagnostic\n",
                "",
                0.01,
            ),
            "not parseable",
        ),
    ],
    ids=["timeout", "truncated", "nonzero", "malformed-atomic"],
)
def test_process_failures_and_malformed_output_are_atomic_errors(
    tmp_path: Path,
    process_result: ProcessResult,
    error_fragment: str,
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)

    outcome, calls = _run(
        root,
        source,
        context,
        {"clang_tidy": "auto"},
        tidy_output=process_result,
    )

    assert outcome.mode == "error"
    assert len(calls) == 1
    assert outcome.diagnostics == []
    assert any(error_fragment in error for error in outcome.errors)
    tidy_evidence = next(item for item in outcome.evidence if item.name == "clang-tidy")
    assert tidy_evidence.error
    assert any(target.target_name == "ClangTidyExecutionError" for target in outcome.targets)


def test_explicit_config_outside_project_is_rejected_without_command(tmp_path: Path) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("Checks: '-*,bugprone-*'\n", encoding="utf-8")

    outcome, calls = _run(
        root,
        source,
        context,
        {"clang_tidy": "auto", "clang_tidy_config": str(outside)},
    )

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert any(target.target_name == "ClangTidyConfigError" for target in outcome.targets)
    assert any("outside" in error for error in outcome.errors)


def test_discovered_symlink_config_escaping_project_is_rejected(tmp_path: Path) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    outside = tmp_path / "outside.yaml"
    outside.write_text("Checks: '-*,bugprone-*'\n", encoding="utf-8")
    link = root / ".clang-tidy"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    outcome, calls = _run(root, source, context, {"clang_tidy": "auto"})

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert any(target.target_name == "ClangTidyConfigError" for target in outcome.targets)
    assert any("outside" in error for error in outcome.errors)


def test_lint_engine_publishes_native_clang_diagnostic_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, context, tidy = _project_context(tmp_path)
    assert tidy is not None
    before = source.read_bytes()
    tidy_output = (
        f"{source}:3:5: warning: possible null dereference "
        "[clang-analyzer-core.NullDereference]\n"
        f"{source}:4:5: warning: prefer nullptr [modernize-use-nullptr]\n"
        f"{source}:5:7: note: replace the macro expansion here\n"
        f'fix-it:"{source}":{{5:7-5:11}}:"nullptr"\n'
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    compiler = Path(context.compilation.units[0].argv[0]).resolve(strict=True)

    def fake_run(command: list[str], **kwargs: object) -> ProcessResult:
        calls.append((command, kwargs))
        executable = Path(command[0]).resolve(strict=True)
        if executable == compiler:
            if "-x" in command and "-v" in command:
                language = command[command.index("-x") + 1]
                common = root / "toolchain" / "common"
                paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
                return ProcessResult(0, "", _include_search_output(*paths), 0.01)
            return ProcessResult(0, "", "", 0.01)
        if executable == tidy:
            return ProcessResult(0, "", tidy_output, 0.01)
        raise AssertionError(f"unexpected executable: {command[0]}")

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {
            "lint": {
                "mode": "pass_warn_fail",
                "ruff_required": False,
                "clang_tidy": "auto",
                "clazy": "off",
                "clang_tidy_checks": ["-*", "bugprone-*", "performance-*"],
            }
        },
    }

    result = LintEngine(root, config, analysis_context=context).run()

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert len(calls) == 4
    assert result.extra["cpp_diagnostic_families"] == {
        "compiler": 0,
        "clang-tidy": 1,
        "clang-analyzer": 1,
        "clazy": 0,
    }
    assert result.extra["violations_count"] == 2
    assert result.extra["cpp_related_notes"] == 1
    assert result.extra["cpp_fixits_total"] == 1
    assert result.extra["cpp_fixits"][0]["family"] == "clang-tidy"
    assert result.extra["cpp_fixits"][0]["rule"] == "modernize-use-nullptr"
    assert result.summary == "0 Errors, 2 Warnings Found"
    assert len(result.findings) == 2
    findings_by_rule = {finding.tool_rule_id: finding for finding in result.findings}
    assert findings_by_rule["clang-analyzer-core.NullDereference"].category is (
        FindingCategory.CORRECTNESS
    )
    assert findings_by_rule["modernize-use-nullptr"].category is FindingCategory.MAINTAINABILITY
    assert len(findings_by_rule["modernize-use-nullptr"].related_locations) == 1
    note = findings_by_rule["modernize-use-nullptr"].related_locations[0]
    assert note.path == "src/main.cpp"
    assert note.start_line == 5
    assert note.start_column == 7
    assert note.label == "note: replace the macro expansion here"
    assert "replace with 'nullptr'" in findings_by_rule["modernize-use-nullptr"].remediation
    assert all(finding.tool_name == "clang-tidy" for finding in result.findings)
    assert all(finding.tool_version == "clang-tidy version 18.1.0" for finding in result.findings)
    assert set(findings_by_rule) == {
        "clang-analyzer-core.NullDereference",
        "modernize-use-nullptr",
    }

    projected = findings_for_result(result, root)
    projected_diagnostic_findings = [
        finding for finding in projected if finding.tool_rule_id in findings_by_rule
    ]
    assert len(projected_diagnostic_findings) == 2
    assert {finding.tool_rule_id for finding in projected_diagnostic_findings} == set(
        findings_by_rule
    )
    assert source.read_bytes() == before


@pytest.mark.parametrize("field", ["ExtraArgs", "ExtraArgsBefore", "InheritParentConfig"])
def test_discovered_config_rejects_compiler_argument_overrides_without_invocation(
    tmp_path: Path, field: str
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    value = "true" if field == "InheritParentConfig" else "['-Wno-error']"
    (root / ".clang-tidy").write_text(f"{field}: {value}\n", encoding="utf-8")

    outcome, calls = _run(root, source, context, {"clang_tidy": "auto"})

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert any(target.target_name == "ClangTidyConfigError" for target in outcome.targets)
    assert any("compiler arguments or inherit parent config" in error for error in outcome.errors)


def test_source_outside_project_is_rejected_without_invocation(tmp_path: Path) -> None:
    root, _source, context, _tidy = _project_context(tmp_path)
    outside = tmp_path / "outside.cpp"
    outside.write_text("int outside() { return 0; }\n", encoding="utf-8")

    outcome, calls = _run(root, outside, context, {"clang_tidy": "auto"})

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert outcome.targets[0].target_name == "ClangTidyContextError"
    assert "outside the project" in outcome.errors[0]


def test_invalid_clang_tidy_mode_fails_closed_without_invocation(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_clang_tidy(
        tmp_path,
        [],
        None,
        {"clang_tidy": "sometimes"},
        runner=runner,
    )

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert outcome.targets[0].target_name == "ClangTidyConfigError"
    assert outcome.targets[0].status is EngineStatus.ERROR
    assert outcome.errors == ["clang-tidy mode must be auto, required, or off"]


def test_analysis_context_from_another_project_is_rejected(tmp_path: Path) -> None:
    _root, source, context, _tidy = _project_context(tmp_path)
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_clang_tidy(
        other_root,
        [source],
        context,
        {"clang_tidy": "auto"},
        runner=runner,
    )

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert outcome.targets[0].target_name == "ClangTidyContextError"
    assert outcome.targets[0].status is EngineStatus.ERROR
    assert "another project root" in outcome.targets[0].message


def test_project_contained_clang_tidy_is_unavailable_without_invocation(tmp_path: Path) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    contained = _executable(root / "tools" / "clang-tidy")
    capabilities = dict(context.capabilities.capabilities)
    original = capabilities["clang-tidy"]
    capabilities["clang-tidy"] = replace(original, path=str(contained))
    contained_context = replace(
        context,
        capabilities=CapabilityInventory(capabilities=capabilities),
    )

    outcome, calls = _run(root, source, contained_context, {"clang_tidy": "auto"})

    assert outcome.mode == "unavailable"
    assert calls == []
    assert len(outcome.evidence) == 1
    assert outcome.evidence[0].path == str(contained)
    assert "no command was executed" in outcome.evidence[0].error
    assert outcome.targets[0].target_name == "ClangTidyUnavailable"
    assert outcome.targets[0].status is EngineStatus.WARN


def test_exhausted_global_budget_reports_error_without_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, source, context, _tidy = _project_context(tmp_path)
    clock = iter((100.0, 701.0))
    monkeypatch.setattr("ici.engines._clang_tidy.time.monotonic", lambda: next(clock))

    outcome, calls = _run(root, source, context, {"clang_tidy": "auto"})

    assert outcome.mode == "error"
    assert calls == []
    assert outcome.evidence == []
    assert outcome.sources_checked == 0
    assert len(outcome.targets) == 1
    assert outcome.targets[0].target_name == "ClangTidyBudgetError"
    assert outcome.targets[0].status is EngineStatus.ERROR
    assert "global time budget" in outcome.errors[0]
