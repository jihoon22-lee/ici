"""Contract tests for normalized GCC/Clang compiler diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines._cpp_diagnostics import (
    parse_clang_tidy_diagnostics,
    parse_compiler_diagnostics,
)


def test_gcc_json_diagnostics_include_children_and_fixits(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0 }\n", encoding="utf-8")
    payload = [
        {
            "kind": "error",
            "message": "expected ';' after return statement",
            "option": "-Werror",
            "locations": [
                {
                    "caret": {"file": str(source), "line": 1, "column": 24},
                    "start": {"file": str(source), "line": 1, "column": 22},
                    "finish": {"file": str(source), "line": 1, "column": 24},
                }
            ],
            "fixits": [
                {
                    "start": {"file": str(source), "line": 1, "column": 24},
                    "next": {"file": str(source), "line": 1, "column": 24},
                    "string": ";",
                },
                {
                    "start": {"file": str(source), "line": 1, "column": 10},
                    "next": {"file": str(source), "line": 1, "column": 11},
                    "string": "",
                },
            ],
            "children": [
                {
                    "kind": "note",
                    "message": "the return statement is here",
                    "locations": [{"caret": {"file": str(source), "line": 1, "column": 14}}],
                }
            ],
        }
    ]

    result = parse_compiler_diagnostics(root, root, json.dumps(payload), "")

    assert result.format_name == "json"
    assert result.error == ""
    assert len(result.diagnostics) == 2

    diagnostic, child = result.diagnostics
    assert diagnostic.tool_rule_id == "-Werror"
    assert diagnostic.target.file_path == "src/main.cpp"
    assert diagnostic.target.start_line == 1
    assert diagnostic.target.end_line == 1
    assert diagnostic.target.start_column == 24
    assert diagnostic.target.end_column == 24
    assert diagnostic.target.status is EngineStatus.FAIL
    assert diagnostic.target.target_name == "Compiler:-Werror"
    assert [fixit.replacement for fixit in diagnostic.fixits] == [";", ""]
    assert diagnostic.fixits[1].replacement == ""
    assert child.target.file_path == "src/main.cpp"
    assert child.target.status is EngineStatus.WARN
    assert child.target.message == "note: the return statement is here"


def test_clang_diagnostics_object_uses_level_and_location_fields(tmp_path: Path) -> None:
    root = tmp_path / "project"
    payload = {
        "level": "warning",
        "message": "unused variable 'answer'",
        "location": {"file": "src/main.cpp", "line": 8, "column": 7},
        "check_name": "clang-diagnostic-unused-variable",
    }

    result = parse_compiler_diagnostics(root, root, json.dumps(payload), "")

    assert result.format_name == "json"
    assert result.error == ""
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.tool_rule_id == "clang-diagnostic-unused-variable"
    assert diagnostic.target.file_path == "src/main.cpp"
    assert diagnostic.target.start_line == 8
    assert diagnostic.target.start_column == 7
    assert diagnostic.target.status is EngineStatus.WARN
    assert diagnostic.target.message == "warning: unused variable 'answer'"


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("internal compiler error", EngineStatus.FAIL),
        ("sorry, unimplemented", EngineStatus.FAIL),
        ("anachronism", EngineStatus.WARN),
    ],
)
def test_gcc_json_unlocated_diagnostics_keep_bounded_external_target(
    tmp_path: Path,
    kind: str,
    status: EngineStatus,
) -> None:
    root = tmp_path / "project"
    payload = [{"kind": kind, "message": "command-line diagnostic", "locations": []}]

    result = parse_compiler_diagnostics(root, root, json.dumps(payload), "")

    assert result.error == ""
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.target.file_path == "[external]"
    assert diagnostic.target.start_line == 1
    assert diagnostic.target.status is status
    assert diagnostic.target.message == f"{kind}: command-line diagnostic"


def test_text_diagnostics_preserve_rule_and_fixit(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = "\n".join(
        (
            "src/main.cpp:9:3: warning: use NULL instead of 0 [modernize-use-nullptr]",
            'fix-it:"src/main.cpp":{9:3-9:4}:"nullptr"',
        )
    )

    result = parse_compiler_diagnostics(root, root, "", output)

    assert result.format_name == "text"
    assert result.error == ""
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.tool_rule_id == "modernize-use-nullptr"
    assert diagnostic.target.file_path == "src/main.cpp"
    assert diagnostic.target.start_line == 9
    assert diagnostic.target.start_column == 3
    assert diagnostic.target.status is EngineStatus.WARN
    assert diagnostic.target.target_name == "Compiler:modernize-use-nullptr"
    assert diagnostic.fixits[0].file_path == "src/main.cpp"
    assert diagnostic.fixits[0].start_line == 9
    assert diagnostic.fixits[0].start_column == 3
    assert diagnostic.fixits[0].end_line == 9
    assert diagnostic.fixits[0].end_column == 4
    assert diagnostic.fixits[0].replacement == "nullptr"


def test_diagnostic_paths_are_project_relative_or_external(tmp_path: Path) -> None:
    root = tmp_path / "project"
    cwd = root / "build"
    cwd.mkdir(parents=True)
    payload = [
        {
            "kind": "warning",
            "message": "project source",
            "location": {"file": "../src/main.cpp", "line": 3},
        },
        {
            "kind": "warning",
            "message": "system header",
            "location": {"file": "/opt/vendor/include/vendor.hpp", "line": 4},
        },
    ]

    result = parse_compiler_diagnostics(root, cwd, json.dumps(payload), "")

    assert result.error == ""
    assert [item.target.file_path for item in result.diagnostics] == [
        "src/main.cpp",
        "[external]",
    ]


def test_mixed_valid_and_malformed_json_is_rejected_atomically(tmp_path: Path) -> None:
    root = tmp_path / "project"
    payload = [
        {
            "kind": "warning",
            "message": "valid diagnostic",
            "location": {"file": "src/main.cpp", "line": 2},
        },
        {
            "kind": "error",
            "message": "malformed diagnostic",
            "location": {"file": "src/main.cpp", "line": 0},
        },
    ]

    result = parse_compiler_diagnostics(root, root, json.dumps(payload), "")

    assert result.format_name == "json"
    assert result.error
    assert result.diagnostics == ()


def test_clang_tidy_warning_summary_rejects_unaccounted_generated_warnings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    output = (
        "src/main.cpp:3:5: warning: valid [modernize-use-nullptr]\n"
        "Suppressed 1 warning (filtered by configuration).\n"
        "3 warnings generated.\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert result.error
    assert result.diagnostics == ()


def test_clang_tidy_warning_summary_accepts_parsed_and_suppressed_warnings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    output = (
        "src/main.cpp:3:5: warning: valid [modernize-use-nullptr]\n"
        "Suppressed 1 warning (filtered by configuration).\n"
        "2 warnings generated.\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert result.error == ""
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].tool_rule_id == "modernize-use-nullptr"


def test_clang_tidy_clean_system_warnings_are_fully_accounted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "15780 warnings generated.\n"
        "Suppressed 15780 warnings (15780 in non-user code).\n"
        "Use -header-filter=.* to display errors from all non-system headers. "
        "Use -system-headers to display errors from system headers as well.\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert result.error == ""
    assert result.diagnostics == ()


def test_clang_tidy_quiet_summary_without_accounting_is_not_clean(tmp_path: Path) -> None:
    root = tmp_path / "project"

    result = parse_clang_tidy_diagnostics(root, root, "", "15780 warnings generated.\n")

    assert result.format_name == "clang-tidy-text"
    assert result.error
    assert result.diagnostics == ()


def test_clang_tidy_note_inherits_parent_rule_and_family(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "src/main.cpp:3:5: warning: prefer nullptr [modernize-use-nullptr]\n"
        "src/main.cpp:4:7: note: expanded from macro 'NULL'\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert result.error == ""
    assert len(result.diagnostics) == 2
    primary, note = result.diagnostics
    assert primary.tool_rule_id == "modernize-use-nullptr"
    assert primary.family == "clang-tidy"
    assert note.tool_rule_id == primary.tool_rule_id
    assert note.family == primary.family
    assert note.target.file_path == "src/main.cpp"
    assert note.target.start_line == 4
    assert note.target.start_column == 7


@pytest.mark.parametrize(
    ("parser", "output"),
    [
        (parse_compiler_diagnostics, "src/main.cpp:2:3: warning: valid [compiler-warning]"),
        (
            parse_clang_tidy_diagnostics,
            "src/main.cpp:2:3: warning: valid [modernize-use-nullptr]",
        ),
    ],
    ids=["compiler", "clang-tidy"],
)
@pytest.mark.parametrize("suffix", ["\x00trailing", "x" * 1_000_001], ids=["nul", "oversized"])
def test_diagnostic_parsers_reject_nul_and_oversized_output_atomically(
    tmp_path: Path,
    parser,
    output: str,
    suffix: str,
) -> None:
    root = tmp_path / "project"

    result = parser(root, root, output + suffix, "")

    assert result.error
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "output",
    [
        "    1 | int main() {}\n      | ^\n",
        "src/main.cpp: In function 'main':\n",
        "1 warning generated.\n",
    ],
    ids=["context", "header", "trailer"],
)
def test_compiler_context_header_or_trailer_only_output_is_not_clean(
    tmp_path: Path, output: str
) -> None:
    root = tmp_path / "project"

    result = parse_compiler_diagnostics(root, root, "", output)

    assert result.error
    assert result.diagnostics == ()


def test_clang_tidy_header_filter_hint_alone_is_not_clean(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = "Use -header-filter=.* to display errors from all non-system headers.\n"

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.error
    assert result.diagnostics == ()
