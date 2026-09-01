"""Policy, replay, provider, and finding contracts for the clazy adapter."""

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
from ici.core.models import EngineStatus, EvidenceState, FindingCategory
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._clazy import run_clazy
from ici.engines.lint import LintEngine


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve(strict=True)


def _context(
    tmp_path: Path,
    *,
    alias: str = "clazy-standalone",
    include_clazy: bool = True,
    include_clang: bool = True,
) -> tuple[Path, Path, AnalysisContext, Path | None, Path | None]:
    root = tmp_path / "project"
    source = root / "src" / "clock.cpp"
    source.parent.mkdir(parents=True)
    (root / "build").mkdir()
    (root / "include").mkdir()
    source.write_text(
        "#include <QDateTime>\nqint64 now() { return QDateTime::currentMSecsSinceEpoch(); }\n",
        encoding="utf-8",
    )
    compiler = _executable(tmp_path / "tools" / "clang++")
    capabilities: dict[str, ToolCapability] = {}
    if include_clang:
        capabilities["clang++"] = ToolCapability(
            name="clang++",
            path=str(compiler),
            available=True,
            version="Ubuntu clang version 21.1.8",
            version_tuple=(21, 1, 8),
            complete=True,
            returncode=0,
        )
    clazy: Path | None = None
    if include_clazy:
        clazy = _executable(tmp_path / "tools" / alias)
        capabilities["clazy"] = ToolCapability(
            name="clazy",
            path=str(clazy),
            available=True,
            version="clazy version 1.17",
            version_tuple=(1, 17),
            complete=True,
            returncode=0,
            details={"resolved_alias": alias},
        )
    unit = CompilationUnit(
        source="src/clock.cpp",
        directory="build",
        argv=(
            str(compiler),
            "-std=c++20",
            "-D",
            "QT_CORE_LIB",
            "-I",
            "../include",
            "-I",
            "/usr/include/qt6",
            "-fPIC",
            "-MMD",
            "-MF",
            "clock.d",
            "-c",
            str(source),
            "-o",
            "clock.o",
        ),
        output="build/clock.o",
        compiler="clang++",
        language="c++",
        standard="c++20",
        configuration=canonical_digest({"configuration": "clazy-test"}),
    )
    context = AnalysisContext(
        project=ProjectModel(
            root=root,
            name="clazy-project",
            version="1.0.0",
            project_type="cpp",
            source_dirs=("src",),
            cpp_sources=("src/clock.cpp",),
            compilable_cpp_sources=("src/clock.cpp",),
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
    return root, source, context, clazy, compiler if include_clang else None


def _run(
    root: Path,
    source: Path,
    context: AnalysisContext,
    config: dict[str, object],
    result: ProcessResult | None = None,
) -> tuple[object, list[tuple[list[str], dict[str, object]]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> ProcessResult:
        calls.append((command, kwargs))
        return result or ProcessResult(0, "", "", 0.01)

    outcome = run_clazy(root, [source], context, config, runner=runner)
    return outcome, calls


def _with_fatal_warning_policy(context: AnalysisContext) -> AnalysisContext:
    unit = context.compilation.units[0]
    compile_index = unit.argv.index("-c")
    updated = replace(
        unit,
        argv=(
            *unit.argv[:compile_index],
            "-Werror",
            "-Werror=return-type",
            "-pedantic-errors",
            "--pedantic-errors",
            "-Werror-implicit-function-declaration",
            "-Wno-error=deprecated-declarations",
            *unit.argv[compile_index:],
        ),
    )
    return replace(context, compilation=replace(context.compilation, units=(updated,)))


def test_off_mode_has_no_side_effects(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_clazy(tmp_path, [], None, {"clazy": "off"}, runner=runner)

    assert outcome.mode == "off"
    assert outcome.targets == []
    assert outcome.evidence == []
    assert calls == []


@pytest.mark.parametrize(
    ("mode", "status"),
    [("auto", EngineStatus.WARN), ("required", EngineStatus.ERROR)],
)
def test_unavailable_tool_observes_policy(tmp_path: Path, mode: str, status: EngineStatus) -> None:
    root, source, context, _clazy, _compiler = _context(tmp_path, include_clazy=False)

    outcome, calls = _run(root, source, context, {"clazy": mode})

    assert calls == []
    assert outcome.targets[0].target_name == "ClazyUnavailable"
    assert outcome.targets[0].status is status
    assert outcome.evidence[0].error


def test_standalone_receives_explicit_checks_and_sanitized_context(tmp_path: Path) -> None:
    root, source, context, clazy, _compiler = _context(tmp_path)
    assert clazy is not None
    before = source.read_bytes()

    outcome, calls = _run(
        root,
        source,
        context,
        {"clazy": "auto", "clazy_profile": "level1", "clazy_checks": ["qdatetime-utc"]},
    )

    assert outcome.mode == "exact"
    assert outcome.provider == "standalone"
    assert outcome.profile == "qdatetime-utc"
    assert outcome.sources_checked == 1
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        str(clazy),
        "--checks=qdatetime-utc",
        "--only-qt",
        str(source),
        "--",
        "-std=c++20",
        "-D",
        "QT_CORE_LIB",
        "-I",
        "../include",
        "-I",
        "/usr/include/qt6",
        "-fPIC",
        "-fdiagnostics-color=never",
    ]
    assert "-p" not in command
    assert not any(argument.startswith("--fix") for argument in command)
    assert kwargs["cwd"] == root / "build"
    assert kwargs["input_text"] == ""
    assert kwargs["replace_env"] is True
    assert kwargs["env"] == {"LANG": "C", "LC_ALL": "C", "TERM": "dumb", "PATH": "/bin:/usr/bin"}
    assert source.read_bytes() == before


@pytest.mark.parametrize("alias", ["clazy-standalone", "clazy"])
def test_clazy_demotes_build_warning_policy_for_every_provider(tmp_path: Path, alias: str) -> None:
    root, source, context, _clazy, _compiler = _context(tmp_path, alias=alias)

    outcome, calls = _run(
        root,
        source,
        _with_fatal_warning_policy(context),
        {"clazy": "auto", "clazy_profile": "level0"},
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
    if alias == "clazy":
        assert command[-4:] == ["-Wall", "-Wextra", "-fsyntax-only", str(source)]


def test_compiler_wrapper_pins_approved_clang_and_checks_in_replacement_env(
    tmp_path: Path,
) -> None:
    root, source, context, clazy, compiler = _context(tmp_path, alias="clazy")
    assert clazy is not None
    assert compiler is not None

    outcome, calls = _run(
        root,
        source,
        context,
        {"clazy": "auto", "clazy_profile": "level1"},
    )

    assert outcome.mode == "exact"
    assert outcome.provider == "compiler-wrapper"
    command, kwargs = calls[0]
    assert command[0] == str(clazy)
    assert command[-4:] == ["-Wall", "-Wextra", "-fsyntax-only", str(source)]
    assert kwargs["replace_env"] is True
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["CLANGXX"] == str(compiler)
    assert environment["CLAZY_CHECKS"] == "level1"


def test_clazy_diagnostic_is_normalized(tmp_path: Path) -> None:
    root, source, context, _clazy, _compiler = _context(tmp_path)
    output = (
        f"{source}:2:23: warning: QDateTime::currentMSecsSinceEpoch() is not UTC "
        "[-Wclazy-qdatetime-utc]\n"
    )

    outcome, calls = _run(
        root,
        source,
        context,
        {"clazy": "auto"},
        ProcessResult(0, "", output, 0.01),
    )

    assert len(calls) == 1
    assert outcome.mode == "exact"
    assert len(outcome.diagnostics) == 1
    diagnostic = outcome.diagnostics[0]
    assert diagnostic.family == "clazy"
    assert diagnostic.tool_rule_id == "clazy-qdatetime-utc"
    assert diagnostic.target.file_path == "src/clock.cpp"
    assert diagnostic.target.target_name == "Clazy:clazy-qdatetime-utc"


@pytest.mark.parametrize(
    ("result", "fragment"),
    [
        (ProcessResult(0, "", "", 0.01, timed_out=True), "timed out"),
        (ProcessResult(0, "", "", 0.01, truncated=True), "truncated"),
        (ProcessResult(3, "", "", 0.01), "exit code 3"),
        (ProcessResult(0, "", "arbitrary tool prose\n", 0.01), "not parseable"),
    ],
)
def test_failures_are_atomic(tmp_path: Path, result: ProcessResult, fragment: str) -> None:
    root, source, context, _clazy, _compiler = _context(tmp_path)

    outcome, calls = _run(root, source, context, {"clazy": "auto"}, result)

    assert len(calls) == 1
    assert outcome.mode == "error"
    assert outcome.diagnostics == []
    assert any(fragment in message for message in outcome.errors)
    assert outcome.evidence[0].error


def test_nonzero_clazy_return_summarizes_located_kinds_without_raw_output(
    tmp_path: Path,
) -> None:
    root, source, context, _clazy, _compiler = _context(tmp_path)
    external = "/opt/ci-secrets/qt/private_header.h"
    output = "\n".join(
        (
            f"{source}:2:1: fatal error: fatal diagnostic prose [-Wclazy-fatal-check]",
            f"{source}:2:2: error: error diagnostic prose [-Wclazy-error-check]",
            f"{source}:2:3: warning: warning diagnostic prose [-Wclazy-warning-check]",
            f"{source}:2:4: note: note diagnostic prose [-Wclazy-note-check]",
            f"{source}:2:5: remark: remark diagnostic prose [-Wclazy-remark-check]",
        )
    )

    outcome, calls = _run(
        root,
        source,
        context,
        {"clazy": "auto"},
        ProcessResult(2, output, f"Error while processing {external}\n", 0.01),
    )

    assert len(calls) == 1
    assert outcome.mode == "error"
    assert outcome.diagnostics == []
    assert outcome.configurations_checked == 0
    assert outcome.sources_checked == 0
    assert len(outcome.errors) == 1
    message = outcome.errors[0]
    assert "exit code 2" in message
    assert all(f"{kind}=1" in message for kind in ("fatal", "error", "warning", "note", "remark"))
    assert "processing_error=yes" in message
    assert len(message) <= 512
    assert "fatal diagnostic prose" not in message
    assert "error diagnostic prose" not in message
    assert "warning diagnostic prose" not in message
    assert "note diagnostic prose" not in message
    assert "remark diagnostic prose" not in message
    assert external not in message
    assert outcome.evidence[0].error == message


def test_nonzero_clazy_warning_is_still_an_atomic_error(tmp_path: Path) -> None:
    root, source, context, _clazy, _compiler = _context(tmp_path)
    output = f"{source}:2:23: warning: warning diagnostic prose [-Wclazy-qdatetime-utc]\n"

    outcome, calls = _run(
        root,
        source,
        context,
        {"clazy": "auto"},
        ProcessResult(1, output, "", 0.01),
    )

    assert len(calls) == 1
    assert outcome.mode == "error"
    assert outcome.diagnostics == []
    assert outcome.configurations_checked == 0
    assert outcome.sources_checked == 0
    assert len(outcome.errors) == 1
    message = outcome.errors[0]
    assert "exit code 1" in message
    assert "warning=1" in message
    assert len(message) <= 512
    assert "warning diagnostic prose" not in message
    assert outcome.evidence[0].error == message


def test_wrapper_without_approved_clang_never_executes(tmp_path: Path) -> None:
    root, source, context, _clazy, _compiler = _context(
        tmp_path,
        alias="clazy",
        include_clang=False,
    )

    outcome, calls = _run(root, source, context, {"clazy": "required"})

    assert calls == []
    assert outcome.mode == "error"
    assert outcome.targets[0].target_name == "ClazyUnavailable"
    assert "clang++" in outcome.errors[0]


def test_lint_engine_maps_qt_diagnostics_and_exact_clazy_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context, clazy, compiler = _context(tmp_path)
    assert clazy is not None
    assert compiler is not None
    diagnostics = "\n".join(
        (
            f"{source}:2:2: warning: connect syntax is unsafe [-Wclazy-connect-non-signal]",
            f"{source}:2:3: warning: QObject ownership is unclear [-Wclazy-lifetime-issue]",
            f"{source}:2:4: warning: API changed in Qt 6 [-Wclazy-qt6-deprecated-api-fixes]",
            f"{source}:2:5: warning: container detaches [-Wclazy-range-loop-detach]",
        )
    )

    def fake_run(command: list[str], **_kwargs: object) -> ProcessResult:
        executable = Path(command[0]).resolve(strict=True)
        if executable == compiler:
            return ProcessResult(0, "", "", 0.01)
        if executable == clazy:
            return ProcessResult(0, "", diagnostics, 0.01)
        raise AssertionError(f"unexpected executable: {command[0]}")

    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)
    config = {
        "project": {"source_dirs": ["src"]},
        "engines": {
            "lint": {
                "mode": "pass_warn_fail",
                "clang_tidy": "off",
                "clazy": "required",
                "clazy_profile": "level0",
            }
        },
    }

    result = LintEngine(root, config, analysis_context=context).run()

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert any(target.target_name == "QtCompatibility:Qt6" for target in result.targets)
    assert result.extra["clazy_mode"] == "exact"
    assert result.extra["clazy_provider"] == "standalone"
    assert result.extra["cpp_diagnostic_families"]["clazy"] == 4
    findings = {
        finding.tool_rule_id: finding
        for finding in result.findings
        if finding.tool_rule_id.startswith("clazy-")
    }
    assert findings["clazy-connect-non-signal"].category is FindingCategory.CORRECTNESS
    assert findings["clazy-lifetime-issue"].category is FindingCategory.RESOURCE
    assert findings["clazy-qt6-deprecated-api-fixes"].category is FindingCategory.COMPATIBILITY
    assert findings["clazy-range-loop-detach"].category is FindingCategory.MAINTAINABILITY
    assert all(finding.tool_name == "clazy" for finding in findings.values())
    assert all(finding.tool_version == "clazy version 1.17" for finding in findings.values())
