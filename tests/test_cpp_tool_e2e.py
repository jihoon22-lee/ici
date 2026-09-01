"""Linux process-level E2E coverage for the C++ lint adapters."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory, collect_capability_inventory
from ici.core.compile_db import load_compilation_context
from ici.core.context import AnalysisContext, create_analysis_context, discover_project_model
from ici.core.runner import run_process
from ici.core.toolchain import ToolProbe
from ici.engines._clang_tidy import run_clang_tidy
from ici.engines._cpp_lint import run_cpp_lint

_PROBES = {
    "g++": ToolProbe("g++", ("g++",), ("-dumpfullversion", "-dumpversion")),
    "clang-tidy": ToolProbe("clang-tidy", ("clang-tidy",), ("--version",)),
}
_REQUIRE_TOOLS_ENV = "ICI_REQUIRE_STATIC_ANALYSIS_TOOLS"


def _unavailable(reason: str) -> None:
    if os.environ.get(_REQUIRE_TOOLS_ENV) == "1":
        pytest.fail(f"{reason}; required by {_REQUIRE_TOOLS_ENV}=1")
    pytest.skip(reason)


def _required_inventory(*names: str) -> CapabilityInventory:
    if sys.platform != "linux":
        _unavailable("these process-level checks cover the Linux toolchain")
    missing = [name for name in names if shutil.which(name) is None]
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

    assert len(outcome.evidence) == 1
    evidence = outcome.evidence[0]
    assert evidence.name == "clang-tidy"
    assert Path(evidence.path).resolve(strict=True) == Path(
        inventory.capabilities["clang-tidy"].path
    ).resolve(strict=True)
    assert evidence.returncode == 0
    assert evidence.error == ""
    assert evidence.argv == [
        evidence.path,
        "--quiet",
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
