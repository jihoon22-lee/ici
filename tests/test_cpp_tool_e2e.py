"""Linux process-level E2E coverage for the C++ lint adapters."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory, collect_capability_inventory
from ici.core.compile_db import load_compilation_context
from ici.core.context import AnalysisContext, create_analysis_context, discover_project_model
from ici.core.models import ToolEvidence
from ici.core.runner import ProcessResult, run_process
from ici.core.toolchain import ToolProbe
from ici.engines._clang_tidy import run_clang_tidy
from ici.engines._clazy import run_clazy
from ici.engines._cpp_lint import run_cpp_lint

_PROBES = {
    "g++": ToolProbe("g++", ("g++",), ("-dumpfullversion", "-dumpversion")),
    "clang++": ToolProbe("clang++", ("clang++",), ("--version",)),
    "clang-tidy": ToolProbe("clang-tidy", ("clang-tidy",), ("--version",)),
    "clazy": ToolProbe("clazy", ("clazy-standalone", "clazy"), ("--version",)),
}
_REQUIRE_TOOLS_ENV = "ICI_REQUIRE_STATIC_ANALYSIS_TOOLS"


def _unavailable(reason: str) -> None:
    if os.environ.get(_REQUIRE_TOOLS_ENV) == "1":
        pytest.fail(f"{reason}; required by {_REQUIRE_TOOLS_ENV}=1")
    pytest.skip(reason)


def _required_inventory(*names: str) -> CapabilityInventory:
    if sys.platform != "linux":
        _unavailable("these process-level checks cover the Linux toolchain")
    missing = [
        name
        for name in names
        if not any(shutil.which(candidate) for candidate in _PROBES[name].candidates)
    ]
    if missing:
        _unavailable(f"required Linux tool(s) unavailable: {', '.join(missing)}")

    inventory = collect_capability_inventory(
        probes=tuple(_PROBES[name] for name in names),
    )
    incomplete = [
        name
        for name in names
        if not (
            inventory.capabilities[name].available
            and inventory.capabilities[name].complete
            and inventory.capabilities[name].path
        )
    ]
    if incomplete:
        _unavailable(f"required Linux tool probe incomplete: {', '.join(incomplete)}")
    gxx = inventory.capabilities.get("g++")
    if gxx is not None and gxx.version_tuple < (9,):
        _unavailable("g++ 9 or newer is required for GCC JSON diagnostics")
    return inventory


@pytest.fixture
def real_cpp_project(tmp_path: Path) -> Path:
    """Create a tiny project whose diagnostics are emitted by real tools."""

    root = tmp_path / "project"
    build = root / "build"
    include = root / "include"
    source_dir = root / "src"
    build.mkdir(parents=True)
    include.mkdir()
    source_dir.mkdir()
    (include / "marker.hpp").write_text(
        "#pragma once\n#define ICI_E2E_VALUE 1\n",
        encoding="utf-8",
    )
    (source_dir / "compiler.cpp").write_text(
        '#include "marker.hpp"\n'
        "int compiler_diagnostic() {\n"
        "    int value = -1;\n"
        '    if (value < sizeof("ici")) {\n'
        "        return ICI_E2E_VALUE;\n"
        "    }\n"
        "    return value;\n"
        "}\n",
        encoding="utf-8",
    )
    (source_dir / "tidy.cpp").write_text(
        '#include "marker.hpp"\n'
        "int tidy_diagnostic() {\n"
        "    int* pointer = 0;\n"
        "    return pointer == nullptr ? ICI_E2E_VALUE : 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (source_dir / "tidy_swappable.cpp").write_text(
        "using qsizetype = long long;\n"
        "\n"
        "int tidy_swappable(qsizetype snapshotEntryIndex, int column, int role) {\n"
        "    if (snapshotEntryIndex < 0) {\n"
        "        return 1;\n"
        "    }\n"
        "    if (column < 0) {\n"
        "        return 2;\n"
        "    }\n"
        "    if (role < 0) {\n"
        "        return 3;\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    compile_commands = [
        {
            "directory": str(build),
            "file": "../src/compiler.cpp",
            "arguments": [
                "g++",
                "-std=c++17",
                "-D",
                "ICI_E2E_VALUE=1",
                "-I",
                "../include",
                "-MMD",
                "-MF",
                "compiler.d",
                "-c",
                "../src/compiler.cpp",
                "-o",
                "compiler.o",
            ],
            "output": "compiler.o",
        },
        {
            "directory": str(build),
            "file": "../src/tidy.cpp",
            "arguments": [
                "g++",
                "-std=c++17",
                "-Werror",
                "-D",
                "ICI_E2E_VALUE=1",
                "-I",
                "../include",
                "-MMD",
                "-MF",
                "tidy.d",
                "-c",
                "../src/tidy.cpp",
                "-o",
                "tidy.o",
            ],
            "output": "tidy.o",
        },
        {
            "directory": str(build),
            "file": "../src/tidy_swappable.cpp",
            "arguments": [
                "g++",
                "-std=c++17",
                "-D",
                "ICI_E2E_VALUE=1",
                "-I",
                "../include",
                "-MMD",
                "-MF",
                "tidy_swappable.d",
                "-c",
                "../src/tidy_swappable.cpp",
                "-o",
                "tidy_swappable.o",
            ],
            "output": "tidy_swappable.o",
        },
    ]
    (root / "compile_commands.json").write_text(
        json.dumps(compile_commands),
        encoding="utf-8",
    )
    return root


def _analysis_context(
    root: Path,
    inventory: CapabilityInventory,
) -> AnalysisContext:
    config = {
        "type": "cpp",
        "project": {"source_dirs": ["src"]},
    }
    return create_analysis_context(
        root,
        config,
        inventory,
        project=discover_project_model(root, config),
        compilation=load_compilation_context(root, config),
    )


def _source_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted((root / "src").glob("*.cpp"))
    }


def _assert_sanitized_context(command: list[str], source: Path) -> None:
    assert command[-1] == str(source.resolve(strict=True))
    assert command[1:-1] == [
        "-std=c++17",
        "-D",
        "ICI_E2E_VALUE=1",
        "-I",
        "../include",
        "-fdiagnostics-color=never",
        "-Wall",
        "-Wextra",
        "-fsyntax-only",
        "-fdiagnostics-format=json",
    ]
    assert all(
        value not in command
        for value in (
            "-p",
            "--fix",
            "compile_commands.json",
            "-MMD",
            "-MF",
            "-c",
            "-o",
            "compiler.d",
            "tidy.d",
            "compiler.o",
            "tidy.o",
        )
    )


def _assert_gcc_projection_and_analyzer_evidence(
    evidence: list[ToolEvidence],
    inventory: CapabilityInventory,
    analyzer_name: str,
) -> ToolEvidence:
    probes = [item for item in evidence if item.name == "g++ stdlib include search"]
    assert len(probes) == 2
    assert [item.argv[item.argv.index("-x") + 1] for item in probes] == ["c++", "c"]
    expected_gxx = Path(inventory.capabilities["g++"].path).resolve(strict=True)
    for probe in probes:
        assert Path(probe.path).resolve(strict=True) == expected_gxx
        assert probe.returncode == 0
        assert probe.timed_out is False
        assert probe.truncated is False
        assert probe.error == ""

    analyzers = [item for item in evidence if item.name == analyzer_name]
    assert len(analyzers) == 1
    assert len(evidence) == 3
    return analyzers[0]


def test_run_cpp_lint_uses_real_gcc_json_diagnostics(
    real_cpp_project: Path,
) -> None:
    inventory = _required_inventory("g++")
    context = _analysis_context(real_cpp_project, inventory)
    source = real_cpp_project / "src" / "compiler.cpp"
    before = _source_snapshot(real_cpp_project)

    outcome = run_cpp_lint(
        real_cpp_project,
        [source],
        context,
        [],
        runner=run_process,
        which=shutil.which,
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert outcome.sources_checked == 1
    assert outcome.configurations_checked == 1
    diagnostic = next(item for item in outcome.diagnostics if item.tool_rule_id == "-Wsign-compare")
    assert diagnostic.family == "compiler"
    assert diagnostic.target.file_path == "src/compiler.cpp"
    assert diagnostic.target.start_line == 4
    assert diagnostic.target.target_name == "Compiler:-Wsign-compare"
    assert diagnostic.target.status.value == "WARN"

    assert len(outcome.evidence) == 1
    evidence = outcome.evidence[0]
    assert evidence.name == "g++"
    assert Path(evidence.path).resolve(strict=True) == Path(
        inventory.capabilities["g++"].path
    ).resolve(strict=True)
    assert evidence.returncode == 0
    assert evidence.error == ""
    assert evidence.argv is not None
    _assert_sanitized_context(evidence.argv, source)
    assert _source_snapshot(real_cpp_project) == before


def test_run_clang_tidy_uses_real_binary_and_exact_context(
    real_cpp_project: Path,
) -> None:
    inventory = _required_inventory("g++", "clang-tidy")
    context = _analysis_context(real_cpp_project, inventory)
    source = real_cpp_project / "src" / "tidy.cpp"
    before = _source_snapshot(real_cpp_project)

    outcome = run_clang_tidy(
        real_cpp_project,
        [source],
        context,
        {
            "clang_tidy": "required",
            "clang_tidy_checks": ["-*", "modernize-use-nullptr"],
        },
        runner=run_process,
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert outcome.sources_checked == 1
    assert outcome.configurations_checked == 1
    diagnostic = next(
        item for item in outcome.diagnostics if item.tool_rule_id == "modernize-use-nullptr"
    )
    assert diagnostic.family == "clang-tidy"
    assert diagnostic.target.file_path == "src/tidy.cpp"
    assert diagnostic.target.start_line == 3
    assert diagnostic.target.target_name == "ClangTidy:modernize-use-nullptr"
    assert diagnostic.target.status.value == "WARN"

    evidence = _assert_gcc_projection_and_analyzer_evidence(
        outcome.evidence,
        inventory,
        "clang-tidy",
    )
    assert Path(evidence.path).resolve(strict=True) == Path(
        inventory.capabilities["clang-tidy"].path
    ).resolve(strict=True)
    assert evidence.returncode == 0
    assert evidence.error == ""
    assert evidence.argv == [
        evidence.path,
        "--use-color=false",
        "--config={}",
        "--checks=-*,modernize-use-nullptr",
        str(source.resolve(strict=True)),
        "--",
        "-std=c++17",
        "-D",
        "ICI_E2E_VALUE=1",
        "-I",
        "../include",
        "-fdiagnostics-color=never",
    ]
    assert all(value not in evidence.argv for value in ("-p", "--fix", "compile_commands.json"))
    assert _source_snapshot(real_cpp_project) == before


def test_run_clazy_uses_real_qt_headers_and_exact_context(tmp_path: Path) -> None:
    inventory = _required_inventory("g++", "clang++", "clazy")
    pkg_config = shutil.which("pkg-config")
    if pkg_config is None:
        _unavailable("pkg-config is required for the Qt-backed clazy fixture")
    flags_result = run_process(
        [pkg_config, "--cflags", "Qt6Core"],
        timeout=10.0,
        max_output_chars=65_536,
    )
    if (
        flags_result.returncode != 0
        or flags_result.timed_out
        or flags_result.truncated
        or not flags_result.stdout.strip()
    ):
        _unavailable("Qt6Core development headers are unavailable")

    root = tmp_path / "clazy-project"
    source_dir = root / "src"
    build = root / "build"
    source_dir.mkdir(parents=True)
    build.mkdir()
    source = source_dir / "datetime.cpp"
    source.write_text(
        "#include <QDateTime>\n"
        "QDateTime inefficientUtc() { return QDateTime::currentDateTime().toUTC(); }\n",
        encoding="utf-8",
    )
    before = source.read_bytes()
    gxx = inventory.capabilities["g++"].path
    arguments = [
        gxx,
        "-std=c++17",
        "-Werror",
        *shlex.split(flags_result.stdout),
        "-fPIC",
        "-c",
        str(source),
        "-o",
        "datetime.o",
    ]
    (root / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(build),
                    "file": str(source),
                    "arguments": arguments,
                    "output": "datetime.o",
                }
            ]
        ),
        encoding="utf-8",
    )
    config = {"type": "cpp", "project": {"source_dirs": ["src"]}}
    context = create_analysis_context(
        root,
        config,
        inventory,
        project=discover_project_model(root, config),
        compilation=load_compilation_context(root, config),
    )

    outcome = run_clazy(
        root,
        [source],
        context,
        {"clazy": "required", "clazy_checks": ["qdatetime-utc"]},
        runner=run_process,
    )

    assert outcome.mode == "exact", outcome.errors
    assert outcome.provider == "standalone"
    assert outcome.errors == []
    assert outcome.sources_checked == 1
    assert outcome.configurations_checked == 1
    diagnostic = next(
        item for item in outcome.diagnostics if item.tool_rule_id == "clazy-qdatetime-utc"
    )
    assert diagnostic.family == "clazy"
    assert diagnostic.target.file_path == "src/datetime.cpp"
    assert diagnostic.target.start_line == 2
    assert diagnostic.target.target_name == "Clazy:clazy-qdatetime-utc"
    assert diagnostic.target.status.value == "WARN"
    evidence = _assert_gcc_projection_and_analyzer_evidence(
        outcome.evidence,
        inventory,
        "clazy",
    )
    assert evidence.returncode == 0
    assert evidence.error == ""
    assert evidence.argv is not None
    assert evidence.argv[1:3] == ["--checks=qdatetime-utc", "--only-qt"]
    assert "-Werror" not in evidence.argv
    assert "-p" not in evidence.argv
    assert not any(argument.startswith("--fix") for argument in evidence.argv)
    assert source.read_bytes() == before


def test_run_clang_tidy_accepts_real_llvm_swappable_parameter_notes(
    real_cpp_project: Path,
) -> None:
    inventory = _required_inventory("g++", "clang-tidy")
    context = _analysis_context(real_cpp_project, inventory)
    source = real_cpp_project / "src" / "tidy_swappable.cpp"
    before = _source_snapshot(real_cpp_project)
    process_results: list[ProcessResult] = []

    def recording_runner(*args, **kwargs) -> ProcessResult:
        result = run_process(*args, **kwargs)
        process_results.append(result)
        return result

    outcome = run_clang_tidy(
        real_cpp_project,
        [source],
        context,
        {
            "clang_tidy": "required",
            "clang_tidy_checks": ["-*", "bugprone-easily-swappable-parameters"],
        },
        runner=recording_runner,
    )

    assert outcome.mode == "exact", (
        outcome.errors,
        [(item.returncode, item.error) for item in outcome.evidence],
        [(item.stdout, item.stderr) for item in process_results],
    )
    assert outcome.errors == []
    assert outcome.sources_checked == 1
    assert outcome.configurations_checked == 1
    primary_candidates = [
        item
        for item in outcome.diagnostics
        if item.tool_rule_id == "bugprone-easily-swappable-parameters"
        and item.target.message.startswith("warning:")
    ]
    assert primary_candidates, (
        outcome.diagnostics,
        [(item.stdout, item.stderr) for item in process_results],
    )
    primary = primary_candidates[0]
    conversion_candidates = [
        item
        for item in outcome.diagnostics
        if item.tool_rule_id == primary.tool_rule_id
        and "may be implicitly converted:" in item.target.message
    ]
    assert conversion_candidates, (
        outcome.diagnostics,
        [(item.stdout, item.stderr) for item in process_results],
    )
    conversion = conversion_candidates[0]
    assert primary.target.file_path == "src/tidy_swappable.cpp"
    assert primary.family == "clang-tidy"
    assert conversion.target.file_path == primary.target.file_path
    assert conversion.family == primary.family

    evidence = _assert_gcc_projection_and_analyzer_evidence(
        outcome.evidence,
        inventory,
        "clang-tidy",
    )
    assert evidence.returncode == 0
    assert evidence.error == ""
    assert evidence.argv is not None
    assert evidence.argv[3] == "--checks=-*,bugprone-easily-swappable-parameters"
    assert evidence.argv[4] == str(source.resolve(strict=True))
    assert all(value not in evidence.argv for value in ("-p", "--fix", "compile_commands.json"))
    assert _source_snapshot(real_cpp_project) == before
