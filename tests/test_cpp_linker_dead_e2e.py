"""Conditional real-tool coverage for the CMake GNU ELF dead-symbol fixture."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from ici.core.capabilities import collect_capability_inventory
from ici.core.context import CompilationContext, create_analysis_context, discover_project_model
from ici.core.runner import run_process
from ici.core.toolchain import DEFAULT_TOOL_PROBES
from ici.engines._cpp_linker_dead_symbols import run_cpp_linker_dead_symbols

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "cpp-fixtures" / "cmake_elf_dead"
_REQUIRED_TOOLS = ("g++", "cmake", "readelf", "addr2line")


def _inventory_or_skip(root: Path):
    if sys.platform != "linux":
        pytest.skip("the GNU ELF linker contract is Linux-only")
    missing = [name for name in _REQUIRED_TOOLS if shutil.which(name) is None]
    if missing:
        pytest.skip(f"required GNU ELF tool(s) unavailable: {', '.join(missing)}")
    probes = tuple(probe for probe in DEFAULT_TOOL_PROBES if probe.name in _REQUIRED_TOOLS)
    inventory = collect_capability_inventory(cwd=root, probes=probes)
    incomplete = [
        name
        for name in _REQUIRED_TOOLS
        if not (
            inventory.capabilities[name].available
            and inventory.capabilities[name].complete
            and inventory.capabilities[name].path
        )
    ]
    if incomplete:
        pytest.skip(f"required GNU ELF probe(s) incomplete: {', '.join(incomplete)}")
    return inventory


def test_cmake_elf_fixture_reports_only_the_two_discarded_dead_functions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "cmake_elf_dead"
    shutil.copytree(FIXTURE, root)
    inventory = _inventory_or_skip(root)
    config = {
        "type": "cpp",
        "project": {"source_dirs": ["src"]},
        "engines": {"dead": {"cpp_linker": "required"}},
    }
    project = discover_project_model(root, config)
    context = create_analysis_context(
        root,
        config,
        inventory,
        project=project,
        compilation=CompilationContext(origin="cmake", unity_build=False),
    )
    source_texts = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / "src").glob("*.cpp"))
    }

    outcome = run_cpp_linker_dead_symbols(
        root,
        context,
        source_texts=source_texts,
        policy="required",
        runner=run_process,
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert [(item.target.file_path, item.target.start_line) for item in outcome.symbols] == [
        ("src/reachability.cpp", 8),
        ("src/reachability.cpp", 17),
    ]
    assert all("live" not in item.symbol.casefold() for item in outcome.symbols)
    assert not any(item.target.file_path == "src/main.cpp" for item in outcome.symbols)
