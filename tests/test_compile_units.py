"""Compile-database input service — #211's coverage and honesty contracts.

The parser itself is the existing bounded reader; what these tests pin is the
service layer's promises: variants never collapse into one row, coverage is
expected-vs-covered rather than assumed, a missing database names its remedy
instead of running it, and headers are TU *inputs* with their consumers kept.
"""

from __future__ import annotations

import json
from pathlib import Path

from ici.domain.workspace import BuildUnit, Component
from ici.workspace.compile_units import load_compile_inputs

CXX = "int core() { return 1; }\n"


def _component(**kw) -> Component:
    return Component(id=kw.pop("id", "native"), root="native", languages=("cpp",), **kw)


def _database(root: Path, entries: list[dict], at: str = "native/build") -> Path:
    directory = root / at
    directory.mkdir(parents=True)
    path = directory / "compile_commands.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_an_absent_database_names_its_remedy_without_running_it(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text(CXX, encoding="utf-8")

    inputs = load_compile_inputs(tmp_path, _component(), ("native/core.cpp",))

    assert inputs.database_path == ""
    assert inputs.missing == ("native/core.cpp",)
    assert not inputs.complete
    assert any("builds.<id>" in item or "build" in item for item in inputs.prepare)
    assert any(d.code == "database-missing" for d in inputs.diagnostics)


def test_a_linked_build_units_directory_is_where_the_db_is_sought(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    source = tmp_path / "native" / "core.cpp"
    source.write_text(CXX, encoding="utf-8")
    _database(
        tmp_path,
        [
            {
                "directory": str(tmp_path / "out" / "native"),
                "file": str(source),
                "arguments": ["g++", "-c", str(source), "-o", "core.o"],
            }
        ],
        at="out/native",
    )
    builds = (BuildUnit(id="native", system="cmake", variant="debug", directory="out/native"),)

    inputs = load_compile_inputs(
        tmp_path, _component(build_ids=("native",)), ("native/core.cpp",), builds
    )

    assert inputs.database_path == "out/native/compile_commands.json"
    assert inputs.covered == ("native/core.cpp",)
    assert inputs.complete


def test_coverage_distinguishes_covered_missing_and_extra(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    for name in ("a.cpp", "b.cpp", "h.hpp"):
        (tmp_path / "native" / name).write_text(CXX, encoding="utf-8")
    outside = tmp_path / "other"
    outside.mkdir()
    (outside / "x.cpp").write_text(CXX, encoding="utf-8")
    entries = [
        {
            "directory": str(tmp_path / "native" / "build"),
            "file": str(tmp_path / "native" / "a.cpp"),
            "arguments": ["g++", "-c", "../a.cpp"],
        },
        {
            "directory": str(tmp_path / "native" / "build"),
            "file": str(outside / "x.cpp"),
            "arguments": ["g++", "-c", "../../other/x.cpp"],
        },
    ]
    _database(tmp_path, entries)

    inputs = load_compile_inputs(
        tmp_path,
        _component(),
        ("native/a.cpp", "native/b.cpp", "native/h.hpp"),
    )

    assert inputs.covered == ("native/a.cpp",)
    assert inputs.missing == ("native/b.cpp",)
    assert inputs.extra == ("other/x.cpp",)
    assert inputs.headers == ("native/h.hpp",)
    assert not inputs.complete


def test_debug_and_release_variants_of_one_source_do_not_overwrite(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text(CXX, encoding="utf-8")
    entries = [
        {
            "directory": str(tmp_path / "native" / "build"),
            "file": str(tmp_path / "native" / "core.cpp"),
            "arguments": ["g++", "-O0", "-DDEBUG", "-c", "../core.cpp", "-o", "core.dbg.o"],
        },
        {
            "directory": str(tmp_path / "native" / "build"),
            "file": str(tmp_path / "native" / "core.cpp"),
            "arguments": ["g++", "-O2", "-DNDEBUG", "-c", "../core.cpp", "-o", "core.o"],
        },
    ]
    _database(tmp_path, entries)

    inputs = load_compile_inputs(tmp_path, _component(), ("native/core.cpp",))

    assert len(inputs.units) == 2
    assert inputs.units[0].variant != inputs.units[1].variant
    assert inputs.covered == ("native/core.cpp",)


def test_a_wrapper_keeps_its_launch_chain_and_the_real_compiler(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text(CXX, encoding="utf-8")
    _database(
        tmp_path,
        [
            {
                "directory": str(tmp_path / "native" / "build"),
                "file": str(tmp_path / "native" / "core.cpp"),
                "arguments": ["ccache", "g++", "-c", "../core.cpp"],
            }
        ],
    )

    inputs = load_compile_inputs(tmp_path, _component(), ("native/core.cpp",))

    unit = inputs.units[0]
    assert unit.launchers == ("ccache",)
    assert unit.compiler == "g++"
    assert unit.argv[0] == "ccache"  # the original invocation is preserved


def test_headers_keep_which_units_consume_them(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "a.cpp").write_text(
        '#include "shared.hpp"\nint a();\n', encoding="utf-8"
    )
    (tmp_path / "native" / "b.cpp").write_text(
        '#include "shared.hpp"\nint b();\n', encoding="utf-8"
    )
    (tmp_path / "native" / "c.cpp").write_text("int c();\n", encoding="utf-8")
    (tmp_path / "native" / "shared.hpp").write_text("#pragma once\n", encoding="utf-8")
    _database(
        tmp_path,
        [
            {
                "directory": str(tmp_path / "native" / "build"),
                "file": str(tmp_path / "native" / f"{name}.cpp"),
                "arguments": ["g++", "-c", f"../{name}.cpp"],
            }
            for name in ("a", "b", "c")
        ],
    )
    files = tuple(f"native/{n}" for n in ("a.cpp", "b.cpp", "c.cpp", "shared.hpp"))

    inputs = load_compile_inputs(tmp_path, _component(), files)

    use = next(item for item in inputs.header_uses if item.header == "native/shared.hpp")
    assert use.consumers == ("native/a.cpp", "native/b.cpp")


def test_a_malformed_database_is_evidence_not_a_crash(tmp_path) -> None:
    (tmp_path / "native").mkdir()
    (tmp_path / "native" / "core.cpp").write_text(CXX, encoding="utf-8")
    directory = tmp_path / "native" / "build"
    directory.mkdir()
    (directory / "compile_commands.json").write_text("{not json", encoding="utf-8")

    inputs = load_compile_inputs(tmp_path, _component(), ("native/core.cpp",))

    assert inputs.database_path != ""
    assert inputs.missing == ("native/core.cpp",)
    assert any(d.code == "database-malformed" for d in inputs.diagnostics)
