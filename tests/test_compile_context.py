"""Contracts for safe, immutable compilation-database ingestion."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ici.core import _compile_db_commands as compile_db_commands_module
from ici.core import _compile_db_paths as compile_db_paths_module
from ici.core import compile_db as compile_db_module
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
    assert unit.compiler == "g++"
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


def test_language_quote_sysroot_and_output_precedence_matrix(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    (root / "src" / "legacy.c").write_text("int legacy;\n", encoding="utf-8")
    (root / "quote").mkdir()
    (root / "sdk").mkdir()
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/legacy.c",
                "arguments": [
                    "clang++",
                    "-x",
                    "c++",
                    "-std",
                    "c++23",
                    "-D",
                    "FLAG",
                    "-iquote../quote",
                    "-isysroot",
                    "../sdk",
                    "-c",
                    "../src/legacy.c",
                    "-oargv.o",
                ],
                "output": "declared.o",
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    unit = context.units[0]
    assert unit.language == "c++"
    assert unit.standard == "c++23"
    assert unit.defines == (compile_db_module.CompilationDefine("FLAG"),)
    assert [(item.kind, item.path) for item in unit.include_paths] == [("quote", "quote")]
    assert (unit.sysroot, unit.sysroot_scope) == ("sdk", "project")
    assert unit.output == "build/declared.o"
    assert [item.code for item in unit.diagnostics] == ["output-mismatch"]


def test_cmake_subdirectory_output_is_relative_to_database_parent(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    working_directory = root / "build" / "src" / "gui"
    working_directory.mkdir(parents=True)
    output = "src/gui/CMakeFiles/gui.dir/main.cpp.o"
    _write_database(
        root,
        [
            {
                "directory": str(working_directory),
                "file": str(root / "src" / "main.cpp"),
                "arguments": [
                    "g++",
                    "-c",
                    str(root / "src" / "main.cpp"),
                    "-o",
                    "CMakeFiles/gui.dir/main.cpp.o",
                ],
                "output": output,
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    unit = context.units[0]
    assert unit.output == f"build/{output}"
    assert unit.target == "gui"
    assert unit.diagnostics == ()


def test_invalid_arguments_never_fall_back_to_command(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": "g++ -c ../src/main.cpp",
                "command": "g++ -c ../src/main.cpp",
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    assert context.units == ()
    assert [item.code for item in context.diagnostics] == ["invalid-arguments"]


def test_root_database_has_deterministic_precedence_over_build_database(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "-c", "../src/main.cpp"],
            }
        ],
    )
    root_database = _write_database(root, [], relative="compile_commands.json")

    context = load_compilation_context(root, {"project": {}})

    assert context.database_path == root_database.relative_to(root).as_posix()
    assert context.units == ()


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ("{", "database-malformed"),
        ("{}", "database-not-array"),
        ("[NaN]", "database-malformed"),
        ("[" + "9" * 5000 + "]", "database-malformed"),
    ],
)
def test_malformed_database_shapes_are_diagnostic(tmp_path: Path, payload: str, code: str) -> None:
    root = _fixture_tree(tmp_path)
    path = root / "compile_commands.json"
    path.write_text(payload, encoding="utf-8")

    context = load_compilation_context(root, {"project": {}})

    assert context.units == ()
    assert [item.code for item in context.diagnostics] == [code]


def test_database_size_and_entry_count_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(root, [], relative="compile_commands.json")
    monkeypatch.setattr(compile_db_module, "MAX_COMPILE_DATABASE_BYTES", 1)

    oversized = load_compilation_context(root, {"project": {}})

    assert [item.code for item in oversized.diagnostics] == ["database-too-large"]

    monkeypatch.setattr(compile_db_module, "MAX_COMPILE_DATABASE_BYTES", 1024)
    monkeypatch.setattr(compile_db_module, "MAX_COMPILE_DATABASE_ENTRIES", 0)
    _write_database(root, [{}], relative="compile_commands.json")

    too_many = load_compilation_context(root, {"project": {}})

    assert [item.code for item in too_many.diagnostics] == ["database-too-many-entries"]


@pytest.mark.parametrize(
    "configured",
    ["../outside/compile_commands.json", r"build\compile_commands.json", "C:commands.json"],
)
def test_invalid_explicit_database_setting_is_bounded_evidence(
    tmp_path: Path,
    configured: str,
) -> None:
    root = _fixture_tree(tmp_path)

    context = load_compilation_context(
        root,
        {"project": {"compile_database": configured}},
    )

    assert context.database_path == "compile_commands.json"
    assert [item.code for item in context.diagnostics] == ["invalid-database-setting"]


@pytest.mark.parametrize(
    ("field", "value", "limit_name"),
    [
        ("arguments", ["g++", "-c", "../src/main.cpp"], "MAX_COMPILE_ARGUMENTS"),
        ("arguments", ["g++", "-c", "../src/main.cpp"], "MAX_COMPILE_ARGUMENT_CHARS"),
        ("command", "g++ -c ../src/main.cpp", "MAX_COMPILE_COMMAND_CHARS"),
    ],
)
def test_per_entry_command_and_argument_limits_are_bounded_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    limit_name: str,
) -> None:
    root = _fixture_tree(tmp_path)
    monkeypatch.setattr(compile_db_module, limit_name, 1)
    _write_database(
        root,
        [{"directory": ".", "file": "../src/main.cpp", field: value}],
    )

    context = load_compilation_context(root, {"project": {}})

    assert context.units == ()
    expected = "invalid-arguments" if field == "arguments" else "invalid-command"
    assert [item.code for item in context.diagnostics] == [expected]


def test_windows_command_line_parser_preserves_quoted_argv_without_execution() -> None:
    command = (
        '"C:\\Program Files\\LLVM\\bin\\clang++.exe" '
        '/DNAME=1 "src\\with space.cpp" /Fo"build\\with space.obj"'
    )

    assert compile_db_module._split_windows_command(command) == (
        "C:\\Program Files\\LLVM\\bin\\clang++.exe",
        "/DNAME=1",
        "src\\with space.cpp",
        "/Fobuild\\with space.obj",
    )


def test_duplicate_json_keys_are_rejected_as_ambiguous_input(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    (root / "compile_commands.json").write_text(
        '[{"directory":"build","file":"../src/main.cpp",'
        '"file":"../src/with space.cpp","arguments":["g++"]}]',
        encoding="utf-8",
    )

    context = load_compilation_context(root, {"project": {}})

    assert context.units == ()
    assert [item.code for item in context.diagnostics] == ["database-malformed"]


@pytest.mark.skipif(compile_db_module.os.name == "nt", reason="POSIX path contract")
@pytest.mark.parametrize(
    ("file_value", "code"),
    [(".", "invalid-source-path"), (r"..\\src\\main.cpp", "foreign-path-syntax")],
)
def test_invalid_row_paths_become_bounded_diagnostics(
    tmp_path: Path,
    file_value: str,
    code: str,
) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [{"directory": ".", "file": file_value, "arguments": ["g++", "-c", file_value]}],
    )

    context = load_compilation_context(root, {"project": {}})

    assert context.units == ()
    assert [item.code for item in context.diagnostics] == [code]


def test_missing_working_directory_is_retained_as_unit_evidence(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": str(root / "missing-build"),
                "file": "../src/main.cpp",
                "arguments": ["g++", "-c", "../src/main.cpp"],
            }
        ],
    )

    context = load_compilation_context(root, {"project": {}})

    assert len(context.units) == 1
    assert [item.code for item in context.units[0].diagnostics] == ["missing-directory"]


def test_flag_parser_stops_at_double_dash_and_does_not_consume_options_as_values(
    tmp_path: Path,
) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": [
                    "g++",
                    "-standard",
                    "gnu++98",
                    "-std",
                    "-c",
                    "-x",
                    "-DSTOP=1",
                    "-I",
                    "--",
                    "-std=c++23",
                    "-DIGNORED=1",
                ],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.standard == ""
    assert unit.language == "c++"
    assert unit.defines == (compile_db_module.CompilationDefine("STOP", "1"),)
    assert unit.include_paths == ()
    assert [item.code for item in unit.diagnostics] == [
        "missing-flag-value",
        "missing-flag-value",
        "missing-flag-value",
    ]


def test_source_mismatch_and_canonical_output_comparison_are_diagnostic(
    tmp_path: Path,
) -> None:
    root = _fixture_tree(tmp_path)
    (root / "src" / "other.cpp").write_text("int other;\n", encoding="utf-8")
    (root / "build" / "obj").mkdir(parents=True)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "-c", "../src/other.cpp", "-o", "main.o"],
                "output": "obj/../main.o",
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.output == "build/main.o"
    assert [item.code for item in unit.diagnostics] == ["source-mismatch"]


def test_explicit_database_setting_uses_same_canonical_path_policy_as_config(
    tmp_path: Path,
) -> None:
    root = _fixture_tree(tmp_path)
    database = _write_database(root, [], relative="compile_commands.json")

    context = load_compilation_context(
        root,
        {"project": {"compile_database": "build/../compile_commands.json"}},
    )

    assert context.database_path == database.relative_to(root).as_posix()
    assert context.diagnostics == ()


def test_bounded_project_response_file_is_expanded_without_execution(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    (root / "build").mkdir(exist_ok=True)
    (root / "build" / "flags.rsp").write_text(
        "-std=c++20 -DRESP=1 -I '../include dir'", encoding="utf-8"
    )
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "@flags.rsp", "-c", "../src/main.cpp"],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.standard == "c++20"
    assert unit.defines == (compile_db_module.CompilationDefine("RESP", "1"),)
    assert [(item.kind, item.path) for item in unit.include_paths] == [("include", "include dir")]
    assert unit.argv[1:4] == ("-std=c++20", "-DRESP=1", "-I")


def test_response_file_escape_is_reported_without_reading_external_input(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    outside = tmp_path / "outside.rsp"
    outside.write_text("-DSECRET=external", encoding="utf-8")
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "@../../outside.rsp", "-c", "../src/main.cpp"],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.defines == ()
    assert [item.code for item in unit.diagnostics] == ["response-file-outside-project"]


def test_database_growth_during_bounded_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(root, [], relative="compile_commands.json")
    monkeypatch.setattr(compile_db_module, "MAX_COMPILE_DATABASE_BYTES", 2)
    real_read = compile_db_module.os.read
    calls = 0

    def growing_read(descriptor: int, size: int) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            return b"x"
        return real_read(descriptor, size)

    monkeypatch.setattr(compile_db_module.os, "read", growing_read)

    context = load_compilation_context(root, {"project": {}})

    assert [item.code for item in context.diagnostics] == ["database-too-large"]


def test_recursive_response_file_cycle_is_bounded_evidence(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    (root / "build").mkdir(exist_ok=True)
    (root / "build" / "one.rsp").write_text("@two.rsp", encoding="utf-8")
    (root / "build" / "two.rsp").write_text("@one.rsp", encoding="utf-8")
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": ["g++", "@one.rsp", "-c", "../src/main.cpp"],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert [item.code for item in unit.diagnostics] == ["response-file-cycle"]


def test_aggregate_response_file_bytes_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fixture_tree(tmp_path)
    (root / "build").mkdir(exist_ok=True)
    (root / "build" / "one.rsp").write_text("-DONE", encoding="utf-8")
    (root / "build" / "two.rsp").write_text("-DTWO", encoding="utf-8")
    monkeypatch.setattr(compile_db_module, "MAX_RESPONSE_FILE_BYTES", 6)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": [
                    "g++",
                    "@one.rsp",
                    "@two.rsp",
                    "-c",
                    "../src/main.cpp",
                ],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.defines == (compile_db_module.CompilationDefine("ONE"),)
    assert [item.code for item in unit.diagnostics] == ["response-file-too-large"]


def test_shared_response_file_is_cached_across_database_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fixture_tree(tmp_path)
    response = root / "build" / "shared.rsp"
    response.parent.mkdir(exist_ok=True)
    response_text = "-DRESP=1"
    response.write_text(response_text, encoding="utf-8")
    rows = []
    for index in range(3):
        source_name = f"unit{index}.cpp"
        (root / "src" / source_name).write_text("int value = 1;\n", encoding="utf-8")
        rows.append(
            {
                "directory": ".",
                "file": f"../src/{source_name}",
                "arguments": [
                    "g++",
                    "@shared.rsp",
                    "-c",
                    f"../src/{source_name}",
                ],
            }
        )
    _write_database(root, rows)
    monkeypatch.setattr(
        compile_db_module,
        "MAX_RESPONSE_FILE_BYTES",
        len(response_text.encode("utf-8")),
    )
    real_read = compile_db_commands_module._read_bounded_regular
    reads: list[Path] = []

    def counting_read(
        path: Path,
        limit: int,
        *,
        containment_root: Path | None = None,
    ) -> bytes:
        reads.append(path)
        return real_read(path, limit, containment_root=containment_root)

    monkeypatch.setattr(compile_db_commands_module, "_read_bounded_regular", counting_read)

    context = load_compilation_context(root, {"project": {}})

    assert reads == [response.resolve()]
    assert context.diagnostics == ()
    assert len(context.units) == 3
    assert all(
        unit.defines == (compile_db_module.CompilationDefine("RESP", "1"),)
        for unit in context.units
    )
    assert all("-DRESP=1" in unit.argv for unit in context.units)


def test_distinct_response_files_share_database_wide_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fixture_tree(tmp_path)
    build = root / "build"
    build.mkdir(exist_ok=True)
    (build / "one.rsp").write_text("-DONE", encoding="utf-8")
    (build / "two.rsp").write_text("-DTWO", encoding="utf-8")
    rows = []
    for source_name, response_name in (("first.cpp", "one.rsp"), ("second.cpp", "two.rsp")):
        (root / "src" / source_name).write_text("int value = 1;\n", encoding="utf-8")
        rows.append(
            {
                "directory": ".",
                "file": f"../src/{source_name}",
                "arguments": [
                    "g++",
                    f"@{response_name}",
                    "-c",
                    f"../src/{source_name}",
                ],
            }
        )
    _write_database(root, rows)
    monkeypatch.setattr(compile_db_module, "MAX_RESPONSE_FILE_BYTES", 6)

    context = load_compilation_context(root, {"project": {}})

    assert context.diagnostics == ()
    units = {unit.source: unit for unit in context.units}
    assert units["src/first.cpp"].defines == (compile_db_module.CompilationDefine("ONE"),)
    assert units["src/first.cpp"].diagnostics == ()
    assert units["src/second.cpp"].defines == ()
    assert [item.code for item in units["src/second.cpp"].diagnostics] == [
        "response-file-too-large"
    ]


def test_repeated_response_expansion_has_database_wide_argument_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fixture_tree(tmp_path)
    response = root / "build" / "shared.rsp"
    response.parent.mkdir(exist_ok=True)
    response.write_text("-DONE=1 -DTWO=2", encoding="utf-8")
    rows = []
    for index in range(3):
        source_name = f"budget{index}.cpp"
        (root / "src" / source_name).write_text("int value = 1;\n", encoding="utf-8")
        rows.append(
            {
                "directory": ".",
                "file": f"../src/{source_name}",
                "arguments": ["g++", "@shared.rsp", "-c", f"../src/{source_name}"],
            }
        )
    _write_database(root, rows)
    monkeypatch.setattr(compile_db_module, "MAX_COMPILE_DATABASE_ARGUMENTS", 10)
    monkeypatch.setattr(compile_db_module, "MAX_COMPILE_DATABASE_ARGUMENT_CHARS", 10_000)

    context = load_compilation_context(root, {"project": {}})

    assert len(context.units) == 2
    assert [item.code for item in context.diagnostics] == ["database-arguments-too-large"]
    assert context.diagnostics[0].entry_index == 2
    assert context.diagnostics[0].source == "src/budget2.cpp"


@pytest.mark.skipif(
    compile_db_paths_module.os.name == "nt"
    or compile_db_paths_module.os.open not in compile_db_paths_module.os.supports_dir_fd
    or not hasattr(compile_db_paths_module.os, "O_DIRECTORY"),
    reason="intermediate no-follow descriptors are POSIX-specific",
)
def test_contained_bounded_open_rejects_intermediate_symlink(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "flags.rsp").write_text("-DOUTSIDE=1", encoding="utf-8")
    (root / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(compile_db_paths_module._ReadError) as error:
        compile_db_paths_module._read_bounded_regular(
            root / "nested" / "flags.rsp",
            1024,
            containment_root=root,
        )

    assert error.value.code == "unreadable"


def test_similar_long_options_are_not_misclassified_as_compilation_metadata(
    tmp_path: Path,
) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": [
                    "g++",
                    "-stdlib=libc++",
                    "-output",
                    "--sysrootx",
                    "-isystematic",
                    "-c",
                    "../src/main.cpp",
                ],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.standard == ""
    assert unit.output == ""
    assert unit.sysroot == ""
    assert unit.include_paths == ()


def test_msvc_style_metadata_is_normalized_without_running_the_compiler(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    _write_database(
        root,
        [
            {
                "directory": ".",
                "file": "../src/main.cpp",
                "arguments": [
                    "cl.exe",
                    "/std:c++20",
                    "/DNAME=1",
                    "/D",
                    "VALUE=two",
                    "/I../include dir",
                    "/external:I../system",
                    "/TP",
                    "/c",
                    "../src/main.cpp",
                    "/Fomain.obj",
                ],
            }
        ],
    )

    unit = load_compilation_context(root, {"project": {}}).units[0]

    assert unit.standard == "c++20"
    assert unit.language == "c++"
    assert [(item.name, item.value) for item in unit.defines] == [
        ("NAME", "1"),
        ("VALUE", "two"),
    ]
    assert [(item.kind, item.path) for item in unit.include_paths] == [
        ("include", "include dir"),
        ("system", "system"),
    ]
    assert unit.output == "build/main.obj"
    assert unit.diagnostics == ()
