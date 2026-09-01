"""Integration contracts for C++ linting with a shared compilation context."""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationDefine,
    CompilationDiagnostic,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_lint import run_cpp_lint
from ici.engines.lint import LintEngine


def _write_project(root: Path, sources: tuple[str, ...] = ("src/main.cpp",)) -> None:
    (root / "build").mkdir(parents=True, exist_ok=True)
    (root / "include").mkdir(parents=True, exist_ok=True)
    (root / "external").mkdir(parents=True, exist_ok=True)
    for relative in sources:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int value() { return 0; }\n", encoding="utf-8")


def _toolchain(tmp_path: Path) -> dict[str, Path]:
    tool_dir = tmp_path / "toolchain"
    tool_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for name in ("gcc", "g++"):
        path = tool_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o700)
        paths[name] = path.resolve(strict=True)
    return paths


def _inventory(paths: dict[str, Path]) -> CapabilityInventory:
    return CapabilityInventory(
        capabilities={
            name: ToolCapability(
                name=name,
                path=str(path),
                available=True,
                version=f"{name} (GCC) 14.2.0",
                version_tuple=(14, 2, 0),
                complete=True,
                details={"target_triple": "x86_64-linux-gnu"},
                probe_argv=(str(path), "--version"),
                returncode=0,
            )
            for name, path in paths.items()
        }
    )


def _single_compiler_inventory(
    name: str,
    path: Path,
    *,
    version: str,
    version_tuple: tuple[int, ...],
) -> CapabilityInventory:
    return CapabilityInventory(
        capabilities={
            name: ToolCapability(
                name=name,
                path=str(path),
                available=True,
                version=version,
                version_tuple=version_tuple,
                complete=True,
                details={"target_triple": "x86_64-linux-gnu"},
                probe_argv=(str(path), "--version"),
                returncode=0,
            )
        }
    )


def _project(root: Path, sources: tuple[str, ...]) -> ProjectModel:
    return ProjectModel(
        root=root,
        name="lint-context",
        version="1.0.0",
        project_type="cpp",
        source_dirs=("src",),
        cpp_sources=sources,
        compilable_cpp_sources=sources,
        cpp_include_flags=("-I", "include"),
        backend="cmake",
        backend_descriptor="CMakeLists.txt",
        backend_reason="CMakeLists.txt at the project root selected the CMake backend",
    )


def _unit(
    root: Path,
    compiler: Path | str,
    *,
    standard: str = "c++20",
    define: str = "FEATURE=twenty",
    include_option: tuple[str, str] = ("-I", "../include"),
    target: str = "demo",
    configuration: str = "twenty",
    diagnostics: tuple[CompilationDiagnostic, ...] = (),
) -> CompilationUnit:
    source = root / "src/main.cpp"
    output = f"build/CMakeFiles/{target}.dir/src/main.cpp.o"
    include_kind, include_path = include_option
    define_args = (f"-D{define}",) if include_kind == "-I" else ("-D", define)
    return CompilationUnit(
        source="src/main.cpp",
        directory="build",
        argv=(
            str(compiler),
            f"-std={standard}",
            *define_args,
            include_kind,
            include_path,
            "-MMD",
            "-MF",
            "main.d",
            "-c",
            str(source),
            "-o",
            "main.o",
        ),
        output=output,
        compiler=Path(str(compiler)).name,
        language="c++",
        standard=standard,
        target=target,
        defines=(CompilationDefine(*define.split("=", 1)),),
        include_paths=(
            CompilationSearchPath(
                path="include" if include_path == "../include" else "external",
                kind="include" if include_kind == "-I" else "system",
                scope="project",
                exists=True,
            ),
        ),
        configuration=canonical_digest({"configuration": configuration}),
        diagnostics=diagnostics,
    )


def _context(
    root: Path,
    inventory: CapabilityInventory,
    units: tuple[CompilationUnit, ...] = (),
    *,
    database: bool = True,
    sources: tuple[str, ...] = ("src/main.cpp",),
    diagnostics: tuple[CompilationDiagnostic, ...] = (),
) -> AnalysisContext:
    compilation = CompilationContext(
        units=units,
        database_path="build/compile_commands.json" if database else None,
        database_digest=canonical_digest({"units": len(units)}) if database else "",
        origin="cmake" if database else "",
        generator="Ninja" if database else "",
        unity_build=False if database else None,
        diagnostics=diagnostics,
    )
    return AnalysisContext(
        project=_project(root, sources),
        capabilities=inventory,
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({"lint": "fixture"}),
            toolchain_digest=canonical_digest(sorted(str(path) for path in inventory.capabilities)),
        ),
        compilation=compilation,
    )


def _run_lint(
    root: Path,
    context: AnalysisContext,
    monkeypatch: pytest.MonkeyPatch,
    *,
    process_result: ProcessResult | None = None,
) -> tuple[object, list[tuple[list[str], dict[str, object]]], list[str]]:
    calls: list[tuple[list[str], dict[str, object]]] = []
    which_calls: list[str] = []

    def fake_which(name: str) -> str | None:
        which_calls.append(name)
        if name == "g++":
            capability = context.capabilities.capabilities.get("g++")
            return capability.path if capability is not None else None
        return None

    def fake_run(command: list[str], **kwargs: object) -> ProcessResult:
        calls.append((command, kwargs))
        return process_result or ProcessResult(0, "", "", 0.01)

    # LintEngine injects these facades into the C++ helper; patching only the
    # helper would miss the production wiring contract under test.
    monkeypatch.setattr("ici.engines.lint.shutil.which", fake_which)
    monkeypatch.setattr("ici.engines.lint.run_process", fake_run)
    result = LintEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {
                "lint": {
                    "mode": "pass_warn_fail",
                    "clang_tidy": "off",
                    "clazy": "off",
                }
            },
        },
        analysis_context=context,
    ).run()
    return result, calls, which_calls


def test_exact_context_replays_duplicate_configurations_and_preserves_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    units = (
        _unit(root, paths["g++"], target="demo20", configuration="twenty"),
        _unit(
            root,
            paths["g++"],
            standard="c++23",
            define="FEATURE=twenty-three",
            include_option=("-isystem", "../external"),
            target="demo23",
            configuration="twenty-three",
        ),
    )
    context = _context(root, _inventory(paths), units)

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_analysis_mode"] == "exact"
    assert result.extra["cpp_configurations_checked"] == 2
    assert result.extra["cpp_sources_checked"] == 1
    assert result.extra["cpp_context_missing"] == 0
    assert result.extra["violations_count"] == 0
    assert len(result.targets) == 2
    assert all(target.status == EngineStatus.PASS for target in result.targets)
    assert all(target.target_name.startswith("C++Syntax:") for target in result.targets)
    assert len(calls) == 2
    assert which_calls == []

    command_by_standard = {
        next(argument for argument in command if argument.startswith("-std=")): command
        for command, _kwargs in calls
    }
    cxx20 = command_by_standard["-std=c++20"]
    cxx23 = command_by_standard["-std=c++23"]
    assert "-DFEATURE=twenty" in cxx20
    assert cxx23[cxx23.index("-D") : cxx23.index("-D") + 2] == ["-D", "FEATURE=twenty-three"]
    assert cxx20[cxx20.index("-I") : cxx20.index("-I") + 2] == ["-I", "../include"]
    assert cxx23[cxx23.index("-isystem") : cxx23.index("-isystem") + 2] == [
        "-isystem",
        "../external",
    ]
    for command, _kwargs in calls:
        assert "-c" not in command
        assert "-o" not in command
        assert "-MMD" not in command
        assert "-MF" not in command
        assert "main.d" not in command
        assert "main.o" not in command
        assert "-fsyntax-only" in command
        assert command[-1] == str(root / "src/main.cpp")


@pytest.mark.parametrize("compiler_name", ["gcc", "g++"])
def test_exact_replay_uses_gcc_json_diagnostics_for_supported_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiler_name: str,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths[compiler_name]),))

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status == EngineStatus.PASS
    assert len(calls) == 1
    assert which_calls == []
    command = calls[0][0]
    assert "-fdiagnostics-format=json" in command
    assert "-fdiagnostics-parseable-fixits" not in command
    assert command[-1] == str(root / "src/main.cpp")


@pytest.mark.parametrize(
    ("compiler_name", "version", "version_tuple"),
    [
        pytest.param("clang++", "clang version 18.1.0", (18, 1, 0), id="clang"),
        pytest.param("g++", "", (), id="unknown-version"),
    ],
)
def test_exact_replay_uses_text_fixit_diagnostics_without_gcc_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiler_name: str,
    version: str,
    version_tuple: tuple[int, ...],
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    compiler = paths.get(compiler_name)
    if compiler is None:
        compiler = paths["g++"].with_name(compiler_name)
        compiler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        compiler.chmod(0o700)
        compiler = compiler.resolve(strict=True)
    inventory = _single_compiler_inventory(
        compiler_name,
        compiler,
        version=version,
        version_tuple=version_tuple,
    )
    context = _context(root, inventory, (_unit(root, compiler),))

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status == EngineStatus.PASS
    assert len(calls) == 1
    assert which_calls == []
    command = calls[0][0]
    assert "-fdiagnostics-parseable-fixits" in command
    assert "-fdiagnostics-format=json" not in command
    assert command[-1] == str(root / "src/main.cpp")


def test_database_present_missing_source_is_error_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    sources = ("src/main.cpp", "src/missing.cpp")
    _write_project(root, ("src/main.cpp",))
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),), sources=sources)

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["cpp_analysis_mode"] == "exact"
    assert result.extra["cpp_configurations_checked"] == 1
    assert result.extra["cpp_sources_checked"] == 1
    assert result.extra["cpp_context_missing"] == 1
    missing = [
        target for target in result.targets if target.target_name == "C++SyntaxContextMissing"
    ]
    assert len(missing) == 1
    assert missing[0].file_path == "src/missing.cpp"
    assert missing[0].status == EngineStatus.WARN
    assert len(calls) == 1
    assert which_calls == []
    assert "missing 1 production source" in result.summary


def test_database_present_unsafe_driver_is_error_without_gxx_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    unsafe = _unit(root, "project-wrapper", configuration="unsafe")
    context = _context(root, _inventory(paths), (unsafe,))

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["cpp_analysis_mode"] == "exact"
    assert result.extra["cpp_configurations_checked"] == 0
    assert result.extra["cpp_sources_checked"] == 0
    assert calls == []
    assert which_calls == []
    assert "unsupported-compiler-driver" in result.summary
    assert "g++" not in result.summary


def test_absent_context_uses_cxx17_fallback_with_estimated_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), database=False)

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["cpp_analysis_mode"] == "heuristic"
    assert result.extra["cpp_configurations_checked"] == 1
    assert result.extra["cpp_sources_checked"] == 1
    assert result.extra["cpp_context_missing"] == 0
    assert len(result.targets) == 1
    assert result.targets[0].status == EngineStatus.PASS
    assert "c++17 heuristic fallback" in result.summary
    assert which_calls == []
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[0] == str(paths["g++"])
    assert command[1:5] == [
        "-std=c++17",
        "-I",
        "include",
        "-fdiagnostics-color=never",
    ]
    assert command[5:8] == [
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
    ]
    assert command[-1] == str(root / "src/main.cpp")
    assert kwargs["cwd"] == root
    assert kwargs["timeout"] == 120.0
    assert kwargs["max_output_chars"] == 1_000_000


def test_standalone_fallback_rejects_unsafe_include_flag_without_running_runner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_cpp_lint(
        root,
        [root / "src/main.cpp"],
        None,
        ["--specs=/tmp/project-controlled.specs"],
        runner=fake_run,
        which=lambda name: str(paths["g++"]) if name == "g++" else None,
    )

    assert calls == []
    assert outcome.configurations_checked == 0
    assert outcome.errors
    assert "unsafe-compiler-option" in outcome.errors[0]
    replay_errors = [target for target in outcome.targets if target.status == EngineStatus.ERROR]
    assert len(replay_errors) == 1
    assert replay_errors[0].file_path == "src/main.cpp"
    assert "unsafe-compiler-option" in replay_errors[0].message


def test_standalone_fallback_rejects_noncanonical_gxx_without_running_runner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    wrapper = _toolchain(tmp_path / "wrapper")["g++"]
    noncanonical = wrapper.with_name("g++-wrapper")
    wrapper.rename(noncanonical)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_cpp_lint(
        root,
        [root / "src/main.cpp"],
        None,
        [],
        runner=fake_run,
        which=lambda name: str(noncanonical) if name == "g++" else None,
    )

    assert calls == []
    assert outcome.errors == ["C++ fallback rejected a non-canonical g++ driver"]
    assert outcome.evidence[0].path == str(noncanonical)


def test_standalone_fallback_rejects_project_contained_gxx_without_running_runner(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    compiler = root / "tools/g++"
    compiler.parent.mkdir(parents=True)
    compiler.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    compiler.chmod(0o700)
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_cpp_lint(
        root,
        [root / "src/main.cpp"],
        None,
        [],
        runner=fake_run,
        which=lambda name: str(compiler) if name == "g++" else None,
    )

    assert calls == []
    assert outcome.errors
    assert "project-compiler-rejected" in outcome.errors[0]
    replay_errors = [target for target in outcome.targets if target.status == EngineStatus.ERROR]
    assert len(replay_errors) == 1
    assert replay_errors[0].file_path == "src/main.cpp"


def test_context_fallback_uses_complete_probed_gxx_without_discovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    discovered = _toolchain(tmp_path / "discovered")["g++"]
    context = _context(root, _inventory(paths), database=False)
    calls: list[list[str]] = []
    which_calls: list[str] = []

    def fake_which(name: str) -> str | None:
        which_calls.append(name)
        return str(discovered) if name == "g++" else None

    def fake_run(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        return ProcessResult(0, "", "", 0.01)

    outcome = run_cpp_lint(
        root,
        [root / "src/main.cpp"],
        context,
        ["-I", "include"],
        runner=fake_run,
        which=fake_which,
    )

    assert outcome.errors == []
    assert len(calls) == 1
    assert calls[0][0] == str(paths["g++"])
    assert calls[0][0] != str(discovered)
    assert which_calls == []


@pytest.mark.parametrize(
    ("process_result", "message", "timed_out", "truncated"),
    [
        (ProcessResult(124, "", "", 0.01, timed_out=True), "timed out", True, False),
        (
            ProcessResult(0, "partial", "", 0.01, truncated=True),
            "output was truncated",
            False,
            True,
        ),
    ],
)
def test_exact_replay_timeout_and_truncation_are_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_result: ProcessResult,
    message: str,
    timed_out: bool,
    truncated: bool,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))

    result, calls, which_calls = _run_lint(
        root,
        context,
        monkeypatch,
        process_result=process_result,
    )

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["cpp_configurations_checked"] == 1
    assert result.extra["cpp_sources_checked"] == 1
    assert len(calls) == 1
    assert which_calls == []
    evidence = result.tool_evidence[0]
    assert evidence.timed_out is timed_out
    assert evidence.truncated is truncated
    assert message in result.summary


@pytest.mark.parametrize(
    ("kind", "returncode", "target_status", "engine_status"),
    [
        ("error", 1, EngineStatus.FAIL, EngineStatus.FAIL),
        ("warning", 0, EngineStatus.WARN, EngineStatus.WARN),
    ],
)
def test_exact_replay_preserves_compiler_diagnostics_as_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    returncode: int,
    target_status: EngineStatus,
    engine_status: EngineStatus,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))
    diagnostic = f"{root / 'src/main.cpp'}:7:3: {kind}: expected ';'\n"

    result, calls, which_calls = _run_lint(
        root,
        context,
        monkeypatch,
        process_result=ProcessResult(returncode, "", diagnostic, 0.01),
    )

    assert result.status == engine_status
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_configurations_checked"] == 1
    assert result.extra["cpp_sources_checked"] == 1
    assert len(calls) == 1
    assert which_calls == []
    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.file_path == "src/main.cpp"
    assert target.start_line == 7
    assert target.status == target_status
    assert target.target_name == "C++Syntax"


@pytest.mark.parametrize(("kind", "returncode"), [("warning", 1), ("error", 0)])
def test_compiler_exit_status_must_match_diagnostic_severity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    returncode: int,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))
    diagnostic = f"{root / 'src/main.cpp'}:7:3: {kind}: inconsistent result\n"

    result, calls, which_calls = _run_lint(
        root,
        context,
        monkeypatch,
        process_result=ProcessResult(returncode, "", diagnostic, 0.01),
    )

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert len(calls) == 1
    assert which_calls == []
    assert result.targets[0].target_name == "C++SyntaxExecutionError"
    assert result.tool_evidence[0].error


def test_error_only_empty_context_is_exact_error_without_heuristic_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(
        root,
        _inventory(paths),
        database=False,
        diagnostics=(
            CompilationDiagnostic(
                "qmake-capture-configure-failed",
                "capture failed",
                level="error",
                source="src/main.cpp",
            ),
        ),
    )

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["cpp_analysis_mode"] == "exact"
    assert calls == []
    assert which_calls == []
    assert any(
        target.status is EngineStatus.ERROR
        and target.file_path == "src/main.cpp"
        and "qmake-capture-configure-failed" in target.message
        for target in result.targets
    )


def test_context_error_prevents_replay_even_when_a_valid_unit_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(
        root,
        _inventory(paths),
        (_unit(root, paths["g++"]),),
        diagnostics=(
            CompilationDiagnostic(
                "compile-database-partial-read",
                "the compilation database could not be trusted",
                level="error",
                source="compile_commands.json",
            ),
        ),
    )

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert calls == []
    assert which_calls == []
    assert result.extra["cpp_configurations_checked"] == 0
    assert any("compile-database-partial-read" in target.message for target in result.targets)


def test_compiler_global_budget_fails_closed_without_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))
    clock = iter((100.0, 701.0))
    monkeypatch.setattr("ici.engines._cpp_lint.time.monotonic", lambda: next(clock))

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert calls == []
    assert which_calls == []
    assert result.extra["cpp_configurations_checked"] == 0
    assert any(target.target_name == "C++SyntaxBudgetError" for target in result.targets)


def test_compiler_unit_limit_fails_closed_without_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    unit = _unit(root, paths["g++"])
    context = _context(root, _inventory(paths), (unit,) * 2_049)

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert calls == []
    assert which_calls == []
    assert result.extra["cpp_configurations_checked"] == 0
    assert any(target.target_name == "C++SyntaxBudgetError" for target in result.targets)


def test_stale_translation_unit_is_an_error_target_without_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / "build").mkdir(parents=True)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert calls == []
    assert which_calls == []
    assert any(
        target.status is EngineStatus.ERROR and target.file_path == "src/main.cpp"
        for target in result.targets
    )


def test_source_symlink_escape_is_an_error_target_without_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    (root / "build").mkdir(parents=True)
    outside = tmp_path / "outside.cpp"
    outside.write_text("int value() { return 0; }\n", encoding="utf-8")
    escaped = root / "src" / "main.cpp"
    escaped.parent.mkdir(parents=True, exist_ok=True)
    try:
        escaped.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert calls == []
    assert which_calls == []
    assert any(
        target.status is EngineStatus.ERROR and target.file_path == "src/main.cpp"
        for target in result.targets
    )


def test_relative_compiler_diagnostic_is_normalized_from_build_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))
    diagnostic = "../include/bad.hpp:7:3: error: expected primary-expression\n"

    result, calls, _which_calls = _run_lint(
        root,
        context,
        monkeypatch,
        process_result=ProcessResult(1, "", diagnostic, 0.01),
    )

    assert result.status is EngineStatus.FAIL
    assert result.evidence is EvidenceState.MEASURED
    target = next(target for target in result.targets if target.status is EngineStatus.FAIL)
    assert target.file_path == "include/bad.hpp"
    assert target.start_line == 7
    assert len(calls) == 1
    assert calls[0][1]["cwd"] == root / "build"


def test_huge_compiler_diagnostic_line_fails_closed_as_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))
    diagnostic = f"src/main.cpp:{'9' * 5000}: error: malformed location\n"

    result, calls, which_calls = _run_lint(
        root,
        context,
        monkeypatch,
        process_result=ProcessResult(1, "", diagnostic, 0.01),
    )

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert len(calls) == 1
    assert which_calls == []
    assert any(
        target.status is EngineStatus.ERROR and target.file_path == "src/main.cpp"
        for target in result.targets
    )


@pytest.mark.parametrize("diagnostic_scope", ["context", "unit"])
def test_exact_context_and_unit_warnings_are_warn_targets_with_measured_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    diagnostic_scope: str,
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    warning = CompilationDiagnostic(
        "missing-include-dir",
        "include directory does not exist",
        level="warning",
        source="src/main.cpp",
    )
    units = (
        _unit(root, paths["g++"], diagnostics=(warning,))
        if diagnostic_scope == "unit"
        else _unit(root, paths["g++"]),
    )
    context = _context(
        root,
        _inventory(paths),
        units,
        diagnostics=(warning,) if diagnostic_scope == "context" else (),
    )

    result, calls, which_calls = _run_lint(root, context, monkeypatch)

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert len(calls) == 1
    assert which_calls == []
    warning_targets = [
        target
        for target in result.targets
        if target.status is EngineStatus.WARN and target.file_path == "src/main.cpp"
    ]
    assert warning_targets
    assert any("missing-include-dir" in target.message for target in warning_targets)


def test_runner_exception_counts_attempted_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    _write_project(root)
    paths = _toolchain(tmp_path)
    context = _context(root, _inventory(paths), (_unit(root, paths["g++"]),))
    calls: list[list[str]] = []

    def failing_runner(command: list[str], **_kwargs: object) -> ProcessResult:
        calls.append(command)
        raise RuntimeError("runner exploded")

    monkeypatch.setattr("ici.engines.lint.run_process", failing_runner)
    result = LintEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {"lint": {"mode": "pass_warn_fail", "clang_tidy": "off"}},
        },
        analysis_context=context,
    ).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["cpp_configurations_checked"] == 1
    assert len(calls) == 1
