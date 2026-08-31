"""Contract tests for compiler-exact C++ include tracing in ``CycleEngine``."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import ici.engines.cycle as cycle_module
from ici.core.capabilities import CapabilityInventory, collect_capability_inventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationDiagnostic,
    CompilationSearchPath,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolProbe
from ici.engines.cycle import CycleEngine

_CFG = {"engines": {"cycle": {"mode": "pass_warn_fail", "max_reported": 20}}}


@pytest.fixture(scope="module")
def probed_compiler() -> tuple[str, CapabilityInventory]:
    """Use a real, successfully parsed GCC/Clang capability for replay policy."""

    for name in ("g++", "clang++"):
        if shutil.which(name) is None:
            continue
        inventory = collect_capability_inventory(probes=(ToolProbe(name, (name,), ("--version",)),))
        capability = inventory.capabilities[name]
        if capability.available and capability.complete:
            return capability.path, inventory
    pytest.skip("a probed g++ or clang++ capability is required")


def _project(
    root: Path,
    *,
    sources: tuple[str, ...],
    headers: tuple[str, ...] = (),
) -> ProjectModel:
    return ProjectModel(
        root=root,
        name="compiler-cycle-fixture",
        version="1.0.0",
        project_type="cpp",
        source_dirs=("src",),
        cpp_sources=sources,
        cpp_headers=headers,
        compilable_cpp_sources=sources,
    )


def _context(
    root: Path,
    project: ProjectModel,
    inventory: CapabilityInventory,
    compilation: CompilationContext,
) -> AnalysisContext:
    return AnalysisContext(
        project=project,
        capabilities=inventory,
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({}),
            toolchain_digest=canonical_digest([]),
        ),
        compilation=compilation,
    )


def _unit(
    root: Path,
    compiler: str,
    *,
    source: str = "src/main.cpp",
    include_paths: tuple[CompilationSearchPath, ...] = (),
    include_args: tuple[str, ...] = (),
    diagnostics: tuple[CompilationDiagnostic, ...] = (),
    configuration: str | None = None,
) -> CompilationUnit:
    (root / "build").mkdir(parents=True, exist_ok=True)
    return CompilationUnit(
        source=source,
        directory="build",
        argv=(
            compiler,
            "-DTRACE_FIXTURE=1",
            *include_args,
            "-c",
            f"../{source}",
            "-o",
            "objects/main.o",
            "-MMD",
            "-MF",
            "objects/main.d",
        ),
        compiler=Path(compiler).name,
        language="c++",
        standard="c++17",
        include_paths=include_paths,
        configuration=configuration or canonical_digest({"source": source}),
        diagnostics=diagnostics,
    )


def _compilation(
    unit: CompilationUnit,
    *,
    database_path: str = "build/compile_commands.json",
    diagnostics: tuple[CompilationDiagnostic, ...] = (),
):
    return CompilationContext(
        units=(unit,),
        database_path=database_path,
        database_digest=canonical_digest([unit.source]),
        origin="configured",
        diagnostics=diagnostics,
    )


def _trace_result(
    stderr: str,
    *,
    returncode: int = 0,
    stdout: str = "",
    timed_out: bool = False,
    truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration=0.01,
        timed_out=timed_out,
        truncated=truncated,
    )


def _trace_lines(source: Path, nested: tuple[Path, ...]) -> str:
    return "\n".join(
        [f". {source}", *[f"{'.' * (index + 2)} {path}" for index, path in enumerate(nested)]]
    )


def _install_runner(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[ProcessResult],
) -> list[tuple[list[str], Path | None]]:
    calls: list[tuple[list[str], Path | None]] = []

    def fake_run_process(
        command: list[str], *, cwd: Path | None = None, **_kwargs
    ) -> ProcessResult:
        calls.append((list(command), cwd))
        return responses[len(calls) - 1]

    monkeypatch.setattr(cycle_module, "run_process", fake_run_process)
    return calls


def _run_exact(
    root: Path,
    project: ProjectModel,
    inventory: CapabilityInventory,
    compilation: CompilationContext,
    monkeypatch: pytest.MonkeyPatch,
    response: ProcessResult,
) -> tuple[object, list[tuple[list[str], Path | None]]]:
    calls = _install_runner(monkeypatch, [response])
    result = CycleEngine(
        root,
        _CFG,
        analysis_context=_context(root, project, inventory, compilation),
    ).run()
    return result, calls


def test_trace_uses_compiler_selected_same_basename_without_ambiguity(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    selected = root / "include" / "first" / "common.hpp"
    alternate = root / "include" / "second" / "common.hpp"
    back = root / "include" / "first" / "back.hpp"
    source = root / "src" / "main.cpp"
    for path in (selected, alternate, back, source):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// compiler trace fixture\n", encoding="utf-8")
    unit = _unit(
        root,
        compiler,
        include_args=("-I", "../include/first", "-I", "../include/second"),
        include_paths=(
            CompilationSearchPath(
                path="include/first", kind="include", scope="project", exists=True
            ),
            CompilationSearchPath(
                path="include/second", kind="include", scope="project", exists=True
            ),
        ),
    )
    project = _project(
        root,
        sources=("src/main.cpp",),
        headers=("include/first/common.hpp", "include/second/common.hpp", "include/first/back.hpp"),
    )
    response = _trace_result(_trace_lines(source, (selected, back, selected)))

    result, calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert result.extra["cpp_include_resolution"] == "compiler_trace"
    assert result.extra["ambiguous_cpp_includes"] == 0
    assert result.extra["unresolved_cpp_includes"] == 0
    assert result.extra["cpp_cycles"] == 1
    cycle_target = next(target for target in result.targets if target.target_name == "CppCycle:2")
    cycle_paths = {Path(value) for value in cycle_target.metrics["files"]}
    assert cycle_paths == {selected.resolve(), back.resolve()}

    assert len(calls) == 1
    command, cwd = calls[0]
    assert cwd == root / "build"
    assert command[0] == str(Path(compiler).resolve())
    assert "-c" not in command
    assert "-MMD" not in command
    assert "-MF" not in command
    assert "objects/main.o" not in command
    assert command.count("-o") == 1
    assert command[command.index("-o") + 1] == os.devnull
    assert command[-1] == str(source.resolve())
    assert result.tool_evidence[0].argv == command


def test_nested_compiler_trace_follows_real_edges_and_reports_cycle(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    headers = tuple(root / "include" / name for name in ("a.hpp", "b.hpp", "c.hpp"))
    for path in (source, *headers):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// nested trace fixture\n", encoding="utf-8")
    unit = _unit(root, compiler, include_args=("-I", "../include"))
    project = _project(
        root,
        sources=("src/main.cpp",),
        headers=tuple(path.relative_to(root).as_posix() for path in headers),
    )
    response = _trace_result(_trace_lines(source, (headers[0], headers[1], headers[2], headers[0])))

    result, _calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.extra["cpp_cycles"] == 1
    target = next(target for target in result.targets if target.target_name == "CppCycle:3")
    cycle_paths = [Path(value) for value in target.metrics["files"]]
    expected_edges = {
        (headers[0].resolve(), headers[1].resolve()),
        (headers[1].resolve(), headers[2].resolve()),
        (headers[2].resolve(), headers[0].resolve()),
    }
    actual_edges = {
        (cycle_paths[index], cycle_paths[(index + 1) % len(cycle_paths)])
        for index in range(len(cycle_paths))
    }
    assert actual_edges == expected_edges


def test_edges_from_different_configurations_do_not_create_a_false_cycle(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    sources = tuple(root / "src" / name for name in ("first.cpp", "second.cpp"))
    headers = tuple(root / "include" / name for name in ("a.hpp", "b.hpp"))
    for path in (*sources, *headers):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// disjoint configuration fixture\n", encoding="utf-8")
    units = tuple(
        _unit(root, compiler, source=path.relative_to(root).as_posix()) for path in sources
    )
    project = _project(
        root,
        sources=tuple(path.relative_to(root).as_posix() for path in sources),
        headers=tuple(path.relative_to(root).as_posix() for path in headers),
    )
    compilation = CompilationContext(
        units=units,
        database_path="build/compile_commands.json",
        database_digest=canonical_digest([unit.configuration for unit in units]),
        origin="configured",
    )
    calls = _install_runner(
        monkeypatch,
        [
            _trace_result(_trace_lines(sources[0], headers)),
            _trace_result(_trace_lines(sources[1], tuple(reversed(headers)))),
        ],
    )

    result = CycleEngine(
        root,
        _CFG,
        analysis_context=_context(root, project, inventory, compilation),
    ).run()

    assert result.status is EngineStatus.PASS
    assert result.extra["cpp_cycles"] == 0
    assert result.extra["resolved_cpp_includes"] == 4
    assert len(calls) == 2


def test_compiler_trace_counts_project_generated_system_and_third_party_scopes(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    project_header = root / "include" / "project.hpp"
    generated_header = root / "build" / "generated" / "config.hpp"
    third_party_root = tmp_path / "vendor-sdk" / "include"
    third_party_header = third_party_root / "vendor.hpp"
    system_root = tmp_path / "sysroot" / "usr" / "include"
    system_header = system_root / "system.hpp"
    for path in (
        source,
        project_header,
        generated_header,
        third_party_header,
        system_header,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// scope trace fixture\n", encoding="utf-8")
    unit = _unit(
        root,
        compiler,
        include_args=("-I", str(third_party_root), "-isystem", str(system_root)),
        include_paths=(
            CompilationSearchPath(
                path=str(third_party_root), kind="include", scope="external", exists=True
            ),
            CompilationSearchPath(
                path=str(system_root), kind="system", scope="external", exists=True
            ),
        ),
    )
    project = _project(root, sources=("src/main.cpp",), headers=("include/project.hpp",))
    response = _trace_result(
        _trace_lines(
            source,
            (project_header, generated_header, third_party_header, system_header),
        )
    )

    result, calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.status is EngineStatus.PASS
    assert result.extra["cpp_include_scope_counts"] == {
        "generated": 1,
        "project": 1,
        "system": 1,
        "third_party": 1,
    }
    assert result.extra["cpp_configurations_checked"] == 1
    assert len(calls) == 1


def test_active_missing_include_is_a_warning_with_compiler_trace_location(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text('#include "generated/config.hpp"\n', encoding="utf-8")
    unit = _unit(root, compiler)
    project = _project(root, sources=("src/main.cpp",))
    response = _trace_result(
        f"{source}:4: fatal error: generated/config.hpp: No such file or directory",
        returncode=1,
    )

    result, calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert result.extra["unresolved_cpp_includes"] == 1
    target = next(
        target for target in result.targets if target.target_name == "CppIncludeUnresolved"
    )
    assert target.status is EngineStatus.WARN
    assert target.file_path == "src/main.cpp"
    assert target.start_line == 4
    assert target.metrics["include"] == "generated/config.hpp"
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["malformed", "truncated", "timed_out", "nonzero_unknown"])
def test_unknown_or_incomplete_trace_is_error_not_run(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    unit = _unit(root, compiler)
    project = _project(root, sources=("src/main.cpp",))
    if failure == "malformed":
        response = _trace_result("compiler emitted an unrecognized trace line")
    elif failure == "truncated":
        response = _trace_result(". " + str(source), truncated=True)
    elif failure == "timed_out":
        response = _trace_result("", timed_out=True)
    else:
        response = _trace_result("compiler internal failure", returncode=7)

    result, calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert len(calls) == 1
    assert result.tool_evidence[0].error


def test_present_database_missing_translation_unit_never_uses_suffix_fallback(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    paths = (root / "src" / "main.cpp", root / "src" / "missing.cpp")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int value() { return 0; }\n", encoding="utf-8")
    unit = _unit(root, compiler)
    project = _project(root, sources=("src/main.cpp", "src/missing.cpp"))
    response = _trace_result(_trace_lines(paths[0], ()))

    result, calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["cpp_include_resolution"] == "compiler_trace"
    assert result.extra["cpp_configurations_checked"] == 1
    assert result.extra["ambiguous_cpp_includes"] == 0
    assert result.extra["unresolved_cpp_includes"] == 0
    missing = next(
        target for target in result.targets if target.target_name == "CppIncludeContextMissing"
    )
    assert missing.file_path == "src/missing.cpp"
    assert len(calls) == 1


def test_absent_database_retains_heuristic_estimated_cycle_mode(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    first = root / "include" / "first" / "same.hpp"
    second = root / "include" / "second" / "same.hpp"
    for path in (source, first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "same.hpp"\n' if path is source else "", encoding="utf-8")
    project = _project(
        root,
        sources=("src/main.cpp",),
        headers=("include/first/same.hpp", "include/second/same.hpp"),
    )
    calls = _install_runner(
        monkeypatch,
        [],
    )
    context = _context(root, project, inventory, CompilationContext())

    result = CycleEngine(root, _CFG, analysis_context=context).run()

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.ESTIMATED
    assert result.extra["cpp_include_resolution"] == "unique_project_path_suffix"
    assert result.extra["ambiguous_cpp_includes"] == 1
    assert result.tool_evidence == []
    assert calls == []


def test_error_only_empty_context_is_exact_error_without_heuristic_fallback(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",))
    diagnostic = CompilationDiagnostic(
        "qmake-capture-configure-failed",
        "capture failed",
        level="error",
        source="src/main.cpp",
    )
    _calls = _install_runner(monkeypatch, [_trace_result(_trace_lines(source, ()))])
    context = _context(
        root,
        project,
        inventory,
        CompilationContext(diagnostics=(diagnostic,)),
    )

    result = CycleEngine(root, _CFG, analysis_context=context).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["cpp_include_resolution"] == "compiler_trace"
    assert _calls == []
    assert any(
        target.status is EngineStatus.ERROR
        and target.file_path == "src/main.cpp"
        and "qmake-capture-configure-failed" in target.message
        for target in result.targets
    )


def test_stale_source_is_an_error_target_without_exception(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    (root / "build").mkdir(parents=True)
    project = _project(root, sources=("src/main.cpp",))
    unit = _unit(root, compiler)
    calls = _install_runner(monkeypatch, [_trace_result("")])

    result = CycleEngine(
        root,
        _CFG,
        analysis_context=_context(root, project, inventory, _compilation(unit)),
    ).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert calls == []
    assert any(
        target.status is EngineStatus.ERROR and target.file_path == "src/main.cpp"
        for target in result.targets
    )


def test_symlinked_header_escape_is_an_error_target_without_exception(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    escaped = root / "include" / "escaped.hpp"
    outside = tmp_path / "outside.hpp"
    source.parent.mkdir(parents=True, exist_ok=True)
    escaped.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    outside.write_text("#pragma once\n", encoding="utf-8")
    try:
        escaped.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    project = _project(root, sources=("src/main.cpp",), headers=("include/escaped.hpp",))
    unit = _unit(root, compiler)
    _calls = _install_runner(monkeypatch, [_trace_result(_trace_lines(source, ()))])

    result = CycleEngine(
        root,
        _CFG,
        analysis_context=_context(root, project, inventory, _compilation(unit)),
    ).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert any(
        target.status is EngineStatus.ERROR and target.file_path == "include/escaped.hpp"
        for target in result.targets
    )


def test_huge_line_and_nul_missing_include_diagnostics_fail_closed(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",))
    unit = _unit(root, compiler)
    diagnostics = (
        f"{source}:{'9' * 5000}: fatal error: missing.hpp: No such file or directory",
        "bad\x00source.cpp:1:1: fatal error: missing.hpp: No such file or directory",
    )

    for diagnostic in diagnostics:
        calls = _install_runner(monkeypatch, [_trace_result(diagnostic, returncode=1)])
        result = CycleEngine(
            root,
            _CFG,
            analysis_context=_context(root, project, inventory, _compilation(unit)),
        ).run()

        assert result.status is EngineStatus.ERROR
        assert result.evidence is EvidenceState.NOT_RUN
        assert len(calls) == 1
        assert calls[0][1] == root / "build"
        assert any(
            target.status is EngineStatus.ERROR and target.file_path == "src/main.cpp"
            for target in result.targets
        )


@pytest.mark.parametrize("diagnostic_scope", ["context", "unit"])
def test_exact_context_and_unit_warnings_are_warn_targets_with_measured_evidence(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
    diagnostic_scope: str,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",))
    warning = CompilationDiagnostic(
        "missing-include-dir",
        "include directory does not exist",
        level="warning",
        source="src/main.cpp",
    )
    unit = _unit(
        root,
        compiler,
        diagnostics=(warning,) if diagnostic_scope == "unit" else (),
    )
    compilation = _compilation(
        unit,
        diagnostics=(warning,) if diagnostic_scope == "context" else (),
    )
    result, calls = _run_exact(
        root,
        project,
        inventory,
        compilation,
        monkeypatch,
        _trace_result(_trace_lines(source, ())),
    )

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert len(calls) == 1
    warning_targets = [
        target
        for target in result.targets
        if target.status is EngineStatus.WARN and target.file_path == "src/main.cpp"
    ]
    assert warning_targets
    assert any("missing-include-dir" in target.message for target in warning_targets)


def test_exact_unresolved_includes_are_capped_and_count_truncation(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",))
    unit = _unit(root, compiler)
    stderr = "\n".join(
        f"{source}:{line}: fatal error: missing{line}.hpp: No such file or directory"
        for line in (4, 5, 6)
    )
    calls = _install_runner(monkeypatch, [_trace_result(stderr, returncode=1)])
    config = {"engines": {"cycle": {"mode": "pass_warn_fail", "max_reported": 1}}}

    result = CycleEngine(
        root,
        config,
        analysis_context=_context(root, project, inventory, _compilation(unit)),
    ).run()

    unresolved = [
        target for target in result.targets if target.target_name == "CppIncludeUnresolved"
    ]
    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert len(calls) == 1
    assert len(unresolved) == 1
    assert result.extra["unresolved_cpp_includes"] == 3
    assert result.extra["cpp_include_diagnostics_truncated"] == 2


def test_active_missing_include_does_not_mark_tool_evidence_as_execution_error(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text('#include "missing.hpp"\n', encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",))
    unit = _unit(root, compiler)
    response = _trace_result(
        f"{source}:4: fatal error: missing.hpp: No such file or directory",
        returncode=1,
    )

    result, calls = _run_exact(root, project, inventory, _compilation(unit), monkeypatch, response)

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert len(calls) == 1
    assert result.tool_evidence[0].error == ""


def test_runner_exception_counts_attempted_configuration(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",))
    unit = _unit(root, compiler)
    calls: list[tuple[list[str], Path | None]] = []

    def failing_runner(
        command: list[str], *, cwd: Path | None = None, **_kwargs: object
    ) -> ProcessResult:
        calls.append((list(command), cwd))
        raise RuntimeError("runner exploded")

    monkeypatch.setattr(cycle_module, "run_process", failing_runner)
    result = CycleEngine(
        root,
        _CFG,
        analysis_context=_context(root, project, inventory, _compilation(unit)),
    ).run()

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert result.extra["cpp_configurations_checked"] == 1
    assert len(calls) == 1


def test_same_configuration_merges_translation_unit_traces_into_a_cycle(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    sources = (root / "src" / "first.cpp", root / "src" / "second.cpp")
    headers = (root / "include" / "a.hpp", root / "include" / "b.hpp")
    for path in (*sources, *headers):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// shared configuration fixture\n", encoding="utf-8")
    project = _project(
        root,
        sources=tuple(path.relative_to(root).as_posix() for path in sources),
        headers=tuple(path.relative_to(root).as_posix() for path in headers),
    )
    shared_configuration = canonical_digest({"configuration": "debug"})
    units = tuple(
        _unit(
            root,
            compiler,
            source=path.relative_to(root).as_posix(),
            configuration=shared_configuration,
        )
        for path in sources
    )
    compilation = CompilationContext(
        units=units,
        database_path="build/compile_commands.json",
        database_digest=canonical_digest([unit.source for unit in units]),
        origin="configured",
    )
    calls = _install_runner(
        monkeypatch,
        [
            _trace_result(_trace_lines(sources[0], (headers[0], headers[1]))),
            _trace_result(_trace_lines(sources[1], (headers[1], headers[0]))),
        ],
    )

    result = CycleEngine(
        root,
        _CFG,
        analysis_context=_context(root, project, inventory, compilation),
    ).run()

    assert result.status is EngineStatus.WARN
    assert result.evidence is EvidenceState.MEASURED
    assert result.extra["cpp_include_resolution"] == "compiler_trace"
    assert result.extra["cpp_configurations_checked"] == 2
    assert result.extra["resolved_cpp_includes"] == 4
    assert result.extra["cpp_cycles"] == 1
    cycle_target = next(target for target in result.targets if target.target_name == "CppCycle:2")
    assert {Path(value) for value in cycle_target.metrics["files"]} == {
        headers[0].resolve(),
        headers[1].resolve(),
    }
    assert cycle_target.metrics["configurations"] == [shared_configuration]
    assert len(calls) == 2


@pytest.mark.parametrize("trace_path", ["stale", "invalid"])
def test_missing_include_with_invalid_trace_path_fails_closed(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
    trace_path: str,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    direct = root / "include" / "direct.hpp"
    source.parent.mkdir(parents=True, exist_ok=True)
    direct.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    direct.write_text("#pragma once\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",), headers=("include/direct.hpp",))
    unit = _unit(root, compiler)
    invalid_path = "../missing/stale.hpp" if trace_path == "stale" else "bad\x00trace.hpp"
    stderr = "\n".join(
        (
            f". {direct}",
            f".. {invalid_path}",
            f"{source}:4: fatal error: missing.hpp: No such file or directory",
        )
    )
    result, calls = _run_exact(
        root,
        project,
        inventory,
        _compilation(unit),
        monkeypatch,
        _trace_result(stderr, returncode=1),
    )

    assert result.status is EngineStatus.ERROR
    assert result.evidence is EvidenceState.NOT_RUN
    assert len(calls) == 1
    assert any(
        target.target_name == "CppIncludeTraceError" and target.status is EngineStatus.ERROR
        for target in result.targets
    )
    assert not any(target.target_name == "CppIncludeUnresolved" for target in result.targets)


@pytest.mark.parametrize("trailer_payload", ["actual", "diagnostic", "missing"])
def test_include_guard_trailer_only_accepts_existing_paths(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
    trailer_payload: str,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    direct = root / "include" / "direct.hpp"
    source.parent.mkdir(parents=True, exist_ok=True)
    direct.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    direct.write_text("#pragma once\n", encoding="utf-8")
    project = _project(root, sources=("src/main.cpp",), headers=("include/direct.hpp",))
    unit = _unit(root, compiler)
    if trailer_payload == "actual":
        payload = str(direct)
    elif trailer_payload == "diagnostic":
        payload = "compiler warning: this is not an include-guard path"
    else:
        payload = str(root / "include" / "does-not-exist.hpp")
    stderr = "\n".join((f". {direct}", "Multiple include guards may be useful for:", payload))

    result, calls = _run_exact(
        root,
        project,
        inventory,
        _compilation(unit),
        monkeypatch,
        _trace_result(stderr),
    )

    assert len(calls) == 1
    if trailer_payload == "actual":
        assert result.status is EngineStatus.PASS
        assert result.evidence is EvidenceState.MEASURED
        assert result.extra["resolved_cpp_includes"] == 1
    else:
        assert result.status is EngineStatus.ERROR
        assert result.evidence is EvidenceState.NOT_RUN
        assert any(
            target.target_name == "CppIncludeTraceError" and target.status is EngineStatus.ERROR
            for target in result.targets
        )


def test_pseudo_trace_frame_preserves_depth_without_pseudo_parent_edge(
    tmp_path: Path,
    probed_compiler: tuple[str, CapabilityInventory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compiler, inventory = probed_compiler
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    first = root / "include" / "first.hpp"
    second = root / "include" / "second.hpp"
    for path in (source, first, second):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// pseudo frame fixture\n", encoding="utf-8")
    project = _project(
        root,
        sources=("src/main.cpp",),
        headers=("include/first.hpp", "include/second.hpp"),
    )
    unit = _unit(root, compiler)
    stderr = "\n".join((". <built-in>", f".. {first}", f"... {second}"))

    result, calls = _run_exact(
        root,
        project,
        inventory,
        _compilation(unit),
        monkeypatch,
        _trace_result(stderr),
    )

    assert result.status is EngineStatus.PASS
    assert result.evidence is EvidenceState.MEASURED
    assert result.extra["resolved_cpp_includes"] == 1
    assert result.extra["cpp_include_scope_counts"] == {"project": 1}
    assert len(calls) == 1
