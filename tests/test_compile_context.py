"""Contracts for safe, immutable compilation-database ingestion."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ici.core.compile_db import load_compilation_context
from ici.core.context import CompilationContext


def _write_database(
    root: Path, rows: list[object], relative: str = "build/compile_commands.json"
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _fixture_tree(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "include dir").mkdir()
    (root / "system").mkdir()
    (root / "src" / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    (root / "src" / "with space.cpp").write_text("int value = 1;\n", encoding="utf-8")
    return root


def test_arguments_win_and_structured_metadata_is_normalized(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    database = _write_database(
        root,
        [
            {
                "directory": str(root / "build"),
                "file": "../src/main.cpp",
                "arguments": [
                    "/usr/bin/g++",
                    "-std=c++20",
                    "-DNAME=1",
                    "-D",
                    "VALUE=two",
                    "-I",
                    "../include dir",
                    "-isystem",
                    "../system",
                    "--sysroot=/opt/sdk",
                    "-c",
                    "../src/main.cpp",
                    "-o",
                    "main.o",
                ],
                # If this string were selected it would fail to parse. Its
                # presence also makes precedence a security boundary.
                "command": "'unterminated $(touch should-never-run)",
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    assert isinstance(context, CompilationContext)
    assert context.database_path == database.relative_to(root).as_posix()
    assert context.database_digest.startswith("sha256:")
    assert context.diagnostics == ()
    assert len(context.units) == 1
    unit = context.units[0]
    assert unit.source == "src/main.cpp"
    assert unit.directory == "build"
    assert unit.output == "build/main.o"
    assert unit.compiler == "/usr/bin/g++"
    assert unit.language == "c++"
    assert unit.standard == "c++20"
    assert [(item.name, item.value) for item in unit.defines] == [
        ("NAME", "1"),
        ("VALUE", "two"),
    ]
    assert [(item.kind, item.path, item.scope, item.exists) for item in unit.include_paths] == [
        ("include", "include dir", "project", True),
        ("system", "system", "project", True),
    ]
    assert unit.sysroot == "/opt/sdk"
    assert unit.sysroot_scope == "external"
    assert unit.configuration.startswith("sha256:")
    assert unit.argv[0] == "/usr/bin/g++"


def test_command_only_row_handles_quoted_paths_without_using_a_shell(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    marker = root / "must-not-exist"
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/with space.cpp",
                "command": (
                    "g++ -std c++17 -I '../include dir' "
                    "-c '../src/with space.cpp' -o 'with space.o' "
                    f"'; touch {marker}'"
                ),
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    assert len(context.units) == 1
    unit = context.units[0]
    assert unit.source == "src/with space.cpp"
    assert unit.standard == "c++17"
    assert unit.output == "build/with space.o"
    assert not marker.exists()
    assert "; touch" in unit.argv[-1]


def test_same_source_configuration_variants_are_preserved_deterministically(
    tmp_path: Path,
) -> None:
    root = _fixture_tree(tmp_path)
    (root / "debug").mkdir()
    (root / "release").mkdir()
    rows = [
        {
            "directory": str(root / "release"),
            "file": "../src/main.cpp",
            "arguments": ["g++", "-std=c++20", "-DNDEBUG", "-c", "../src/main.cpp"],
            "output": "main.o",
        },
        {
            "directory": str(root / "debug"),
            "file": "../src/main.cpp",
            "arguments": ["g++", "-std=c++17", "-DDEBUG", "-c", "../src/main.cpp"],
            "output": "main.o",
        },
    ]
    _write_database(root, rows)

    first = load_compilation_context(root, {"project": {}})
    second = load_compilation_context(root, {"project": {}})

    assert first == second
    assert [unit.directory for unit in first.units] == ["debug", "release"]
    assert [unit.standard for unit in first.units] == ["c++17", "c++20"]
    assert len({unit.configuration for unit in first.units}) == 2


def test_malformed_rows_become_diagnostics_without_aborting_valid_rows(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "command": "'unterminated",
            },
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "-I"],
            },
            {"directory": ".", "arguments": ["g++", "-c", "missing.cpp"]},
            "not-an-object",
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    assert len(context.units) == 1
    assert context.units[0].diagnostics[0].code == "missing-flag-value"
    assert {item.code for item in context.diagnostics} == {
        "invalid-command",
        "missing-file",
        "invalid-entry",
    }
    assert {item.entry_index for item in context.diagnostics} == {0, 2, 3}


def test_stale_source_and_missing_include_are_retained_as_unit_evidence(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/generated.cpp",
                "arguments": ["g++", "-I", "../missing-include", "-c", "../src/generated.cpp"],
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    assert len(context.units) == 1
    unit = context.units[0]
    assert unit.source == "src/generated.cpp"
    assert unit.include_paths[0].path == "missing-include"
    assert unit.include_paths[0].exists is False
    assert {item.code for item in unit.diagnostics} == {
        "stale-source",
        "missing-include-dir",
    }


def test_source_and_database_symlink_escapes_are_rejected_with_evidence(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escaped.cpp").write_text("int escaped;\n", encoding="utf-8")
    (root / "src" / "escaped.cpp").symlink_to(outside / "escaped.cpp")
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/escaped.cpp",
                "arguments": ["g++", "-c", "../src/escaped.cpp"],
            }
        ],
    )

    source_context = load_compilation_context(root, {"project": {}})

    assert source_context.units == ()
    assert [item.code for item in source_context.diagnostics] == ["source-outside-project"]

    (root / "build" / "compile_commands.json").unlink()
    external_database = outside / "compile_commands.json"
    external_database.write_text("[]", encoding="utf-8")
    (root / "compile_commands.json").symlink_to(external_database)

    database_context = load_compilation_context(root, {"project": {}})

    assert database_context.units == ()
    assert database_context.database_path == "compile_commands.json"
    assert [item.code for item in database_context.diagnostics] == ["database-outside-project"]


def test_missing_database_is_optional_unless_explicitly_configured(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)

    implicit = load_compilation_context(root, {"project": {}})
    explicit = load_compilation_context(
        root,
        {"project": {"compile_database": "out/compile_commands.json"}},
    )

    assert implicit == CompilationContext()
    assert explicit.database_path == "out/compile_commands.json"
    assert [item.code for item in explicit.diagnostics] == ["database-missing"]


def test_context_and_nested_metadata_are_frozen(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "-DNAME=1", "-c", "../src/main.cpp"],
            }
        ],
    )
    context = load_compilation_context(root, {"project": {}})

    with pytest.raises(FrozenInstanceError):
        context.database_path = None
    with pytest.raises(FrozenInstanceError):
        context.units[0].standard = "c++23"
    with pytest.raises(FrozenInstanceError):
        context.units[0].defines[0].value = "changed"
