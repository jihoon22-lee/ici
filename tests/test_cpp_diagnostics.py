"""Contract tests for normalized GCC/Clang compiler diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.core.models import EngineStatus
from ici.engines._cpp_diagnostics import (
    MAX_DIAGNOSTIC_OUTPUT_CHARS,
    MAX_DIAGNOSTICS,
    MAX_MESSAGE_CHARS,
    parse_clang_tidy_diagnostics,
    parse_clazy_diagnostics,
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


def test_clang_tidy_warning_summary_accepts_coalesced_rendered_diagnostics(
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
    assert result.error == ""
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].tool_rule_id == "modernize-use-nullptr"


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


@pytest.mark.parametrize(
    "header_hint",
    [
        "Use -header-filter=.* to display errors from all non-system headers. "
        "Use -system-headers to display errors from system headers as well.",
        "Use -header-filter=.* or leave it as default to display errors from all "
        "non-system headers. Use -system-headers to display errors from system headers as well.",
    ],
    ids=["llvm-18", "llvm-current"],
)
def test_clang_tidy_clean_system_warnings_are_fully_accounted(
    tmp_path: Path, header_hint: str
) -> None:
    root = tmp_path / "project"
    output = (
        "15780 warnings generated.\n"
        "Suppressed 15780 warnings (15780 in non-user code).\n"
        f"{header_hint}\n"
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


@pytest.mark.parametrize(
    "output",
    [
        "1 warning generated.\n1 warning generated.\n",
        "Suppressed 1 warning (non-user code).\nSuppressed 1 warning (NOLINT).\n",
    ],
    ids=["generated", "suppressed"],
)
def test_clang_tidy_duplicate_summaries_are_rejected_atomically(
    tmp_path: Path, output: str
) -> None:
    root = tmp_path / "project"

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert result.error
    assert result.diagnostics == ()


def test_clang_tidy_note_inherits_parent_rule_and_family(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "src/main.cpp:3:5: warning: prefer nullptr [modernize-use-nullptr]\n"
        "src/main.cpp:4:7: note: expanded from macro 'NULL'\n"
        'fix-it:"src/main.cpp":{4:7-4:11}:"nullptr"\n'
    )

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert result.error == ""
    assert len(result.diagnostics) == 1
    primary = result.diagnostics[0]
    assert len(primary.related_diagnostics) == 1
    note = primary.related_diagnostics[0]
    assert primary.tool_rule_id == "modernize-use-nullptr"
    assert primary.family == "clang-tidy"
    assert note.tool_rule_id == primary.tool_rule_id
    assert note.family == primary.family
    assert note.target.file_path == "src/main.cpp"
    assert note.target.start_line == 4
    assert note.target.start_column == 7
    assert len(note.fixits) == 1
    assert note.fixits[0].replacement == "nullptr"


def test_clang_tidy_llvm18_empty_structural_note_keeps_concrete_note(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    output = (
        "src/model.cpp:431:50: warning: 3 adjacent parameters are easily swapped "
        "[bugprone-easily-swappable-parameters]\n"
        "  431 | QVariant configurationData(qsizetype entryIndex, int column, int role);\n"
        "      |                              ^~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
        "src/model.cpp:431:60: note: the first parameter in the range is 'entryIndex'\n"
        "  431 | QVariant configurationData(qsizetype entryIndex, int column, int role);\n"
        "      |                                        ^~~~~~~~~~\n"
        "src/model.cpp:432:54: note: the last parameter in the range is 'role'\n"
        "  432 |                                                  int role) const;\n"
        "      |                                                      ^~~~\n"
        "src/model.cpp:431:50: note: \n"
        "  431 | QVariant configurationData(qsizetype entryIndex, int column, int role);\n"
        "      |                              ^\n"
        "src/model.cpp:431:80: note: 'qsizetype' and 'int' may be implicitly converted: "
        "'qsizetype' (as 'long long') -> 'int', 'int' -> 'qsizetype' (as 'long long')\n"
        "40713 warnings generated.\n"
        "Suppressed 40712 warnings (40712 in non-user code).\n"
        "Use -header-filter=.* to display errors from all non-system headers. "
        "Use -system-headers to display errors from system headers as well.\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, output, "")

    assert result.format_name == "clang-tidy-text"
    assert result.error == ""
    assert len(result.diagnostics) == 1
    primary = result.diagnostics[0]
    assert len(primary.related_diagnostics) == 3
    first_note, last_note, note = primary.related_diagnostics
    assert primary.tool_rule_id == "bugprone-easily-swappable-parameters"
    assert first_note.tool_rule_id == primary.tool_rule_id
    assert last_note.tool_rule_id == primary.tool_rule_id
    assert note.tool_rule_id == primary.tool_rule_id
    assert note.target.message == (
        "note: 'qsizetype' and 'int' may be implicitly converted: "
        "'qsizetype' (as 'long long') -> 'int', 'int' -> 'qsizetype' (as 'long long')"
    )


def test_clang_tidy_note_with_conflicting_rule_is_rejected_atomically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    output = (
        "src/main.cpp:3:5: warning: prefer nullptr [modernize-use-nullptr]\n"
        "src/main.cpp:4:7: note: unrelated check [bugprone-use-after-move]\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, "", output)

    assert result.format_name == "clang-tidy-text"
    assert "does not match" in result.error
    assert result.diagnostics == ()


def test_clang_tidy_empty_note_without_diagnostic_context_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"

    result = parse_clang_tidy_diagnostics(
        root,
        root,
        "src/model.cpp:431:50: note: \n",
        "",
    )

    assert result.format_name == "clang-tidy-text"
    assert "empty note" in result.error
    assert result.diagnostics == ()


def test_clang_tidy_out_of_range_empty_note_is_not_treated_as_structure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    output = (
        "src/model.cpp:3:5: warning: valid [bugprone-easily-swappable-parameters]\n"
        "src/model.cpp:2147483648:5: note: \n"
    )

    result = parse_clang_tidy_diagnostics(root, root, output, "")

    assert result.format_name == "clang-tidy-text"
    assert result.error
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "empty_note",
    [
        "src/model.cpp:3:2147483648: note: ",
        "src/model.cpp:0:5: note: ",
        "src/model.cpp:03:5: note: ",
        f"{'x' * 4097}:3:5: note: ",
    ],
    ids=["column-overflow", "zero-line", "leading-zero-line", "oversized-path"],
)
def test_clang_tidy_malformed_empty_note_cannot_bypass_parser(
    tmp_path: Path, empty_note: str
) -> None:
    root = tmp_path / "project"
    output = (
        f"src/model.cpp:3:5: warning: valid [bugprone-easily-swappable-parameters]\n{empty_note}\n"
    )

    result = parse_clang_tidy_diagnostics(root, root, output, "")

    assert result.format_name == "clang-tidy-text"
    assert result.error
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "output",
    [
        ("src/a.cpp:1:1: warning: valid [modernize-use-nullptr]\nsrc/a.cpp:1:1: note: \n"),
        (
            "src/a.cpp:1:1: note: \n"
            "src/a.cpp:1:2: note: 'int' and 'long' may be implicitly converted: "
            "'int' -> 'long'\n"
        ),
        (
            "src/a.cpp:1:1: warning: valid [bugprone-easily-swappable-parameters]\n"
            "src/a.cpp:1:1: note: \n"
        ),
        (
            "src/a.cpp:1:1: warning: valid [bugprone-easily-swappable-parameters]\n"
            "src/b.cpp:1:1: note: \n"
            "src/b.cpp:1:2: note: 'int' and 'long' may be implicitly converted: "
            "'int' -> 'long'\n"
        ),
        (
            "src/a.cpp:1:1: warning: valid [bugprone-easily-swappable-parameters]\n"
            "src/a.cpp:1:1: note: \n"
            "src/a.cpp:1:1: note: \n"
        ),
        (
            "src/a.cpp:1:1: warning: valid [bugprone-easily-swappable-parameters]\n"
            "src/a.cpp:1:1: note: \n"
            "src/a.cpp:1:2: note: unrelated explanation\n"
        ),
        (
            "src/a.cpp:1:1: warning: valid [bugprone-easily-swappable-parameters]\n"
            "src/a.cpp:1:2: note: unrelated explanation\n"
            "src/a.cpp:1:1: note: \n"
            "src/a.cpp:1:2: note: 'int' and 'long' may be implicitly converted: "
            "'int' -> 'long'\n"
        ),
    ],
    ids=[
        "unrelated-rule",
        "before-parent",
        "missing-child",
        "mismatched-path",
        "multiple-empty",
        "wrong-child",
        "wrong-intermediate-child",
    ],
)
def test_clang_tidy_empty_note_requires_exact_llvm_context(tmp_path: Path, output: str) -> None:
    root = tmp_path / "project"

    result = parse_clang_tidy_diagnostics(root, root, output, "")

    assert result.format_name == "clang-tidy-text"
    assert "empty note" in result.error
    assert result.diagnostics == ()


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


def test_clazy_diagnostics_normalize_rule_and_inherit_located_notes(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = "src/widget.cpp:7:11: warning: use QStringLiteral [ -Wclazy-qstring-arg ]\n"
    # The real spelling has no spaces inside the option brackets.  Keep the
    # malformed sample above separate so a parser regression cannot silently
    # accept a lookalike annotation.
    malformed = parse_clazy_diagnostics(root, root, output, "")
    assert malformed.error
    assert malformed.diagnostics == ()

    stdout = "src/widget.cpp:7:11: warning: use QStringLiteral [-Wclazy-qstring-arg]\n"
    stderr = "src/widget.cpp:8:5: note: instantiated from this call\n"
    result = parse_clazy_diagnostics(root, root, stdout, stderr)

    assert result.format_name == "clazy-text"
    assert result.error == ""
    assert len(result.diagnostics) == 2
    primary, note = result.diagnostics
    assert primary.tool_rule_id == "clazy-qstring-arg"
    assert primary.family == "clazy"
    assert primary.target.target_name == "Clazy:clazy-qstring-arg"
    assert primary.target.status is EngineStatus.WARN
    assert note.tool_rule_id == primary.tool_rule_id
    assert note.family == primary.family
    assert note.target.target_name == "ClazyNote:clazy-qstring-arg"
    assert note.target.file_path == "src/widget.cpp"
    assert note.target.start_line == 8
    assert note.target.start_column == 5


def test_clazy_diagnostics_accept_errors_and_owned_note_rules(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "src/widget.cpp:3:2: error: QObject macro is missing [-Wclazy-missing-qobject-macro]\n"
        "src/widget.cpp:4:2: note: another check [-Wclazy-qstring-ref]\n"
        "src/widget.cpp:5:2: note: follows the preceding check\n"
    )

    result = parse_clazy_diagnostics(root, root, output, "")

    assert result.error == ""
    assert [item.tool_rule_id for item in result.diagnostics] == [
        "clazy-missing-qobject-macro",
        "clazy-qstring-ref",
        "clazy-qstring-ref",
    ]
    assert result.diagnostics[0].target.status is EngineStatus.FAIL
    assert result.diagnostics[1].target.target_name == "ClazyNote:clazy-qstring-ref"


def test_clazy_diagnostics_validate_and_exclude_mixed_compiler_warning(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "src/widget.cpp:3:2: warning: use QStringLiteral [-Wclazy-qstring-arg]\n"
        "src/widget.cpp:4:2: warning: unused parameter [-Wunused-parameter]\n"
    )

    result = parse_clazy_diagnostics(root, root, output, "")

    assert result.error == ""
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].tool_rule_id == "clazy-qstring-arg"


def test_clazy_diagnostics_exclude_compiler_warning_with_note_and_context(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "src/widget.cpp:4:2: warning: unused parameter [-Wunused-parameter]\n"
        "    4 | void f(int unused) {}\n"
        "      |        ^\n"
        "src/widget.cpp:4:2: note: parameter declared here\n"
        "1 warning generated.\n"
    )

    result = parse_clazy_diagnostics(root, root, output, "")

    assert result.error == ""
    assert result.diagnostics == ()


def test_clazy_diagnostics_reject_non_clazy_error_even_with_warning_rule(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = "src/widget.cpp:4:2: error: promoted warning [-Wunused-parameter]\n"

    result = parse_clazy_diagnostics(root, root, output, "")

    assert result.error
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "output",
    [
        "src/widget.cpp:3:2: warning: no option\n",
        "src/widget.cpp:3:2: error: foreign option [-Wsign-compare]\n",
        "src/widget.cpp:3:2: warning: unsafe option [-Wclazy-qstring/ref]\n",
        "src/widget.cpp:3:2: warning: conflicting options [-Wclazy-a,-Wclazy-b]\n",
        "src/widget.cpp:3:2: note: note before a parent\n",
    ],
    ids=["unruled-primary", "foreign-rule", "unsafe-rule", "conflicting-rules", "orphan-note"],
)
def test_clazy_diagnostics_reject_unknown_or_unsafe_output_atomically(
    tmp_path: Path, output: str
) -> None:
    root = tmp_path / "project"
    prefix = (
        ""
        if output.startswith("src/widget.cpp:3:2: note:")
        else ("src/widget.cpp:1:1: warning: valid [-Wclazy-qstring-arg]\n")
    )

    result = parse_clazy_diagnostics(
        root,
        root,
        prefix + output,
        "",
    )

    assert result.format_name == "clazy-text"
    assert result.error
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "output",
    [
        "  3 | QObject object;\n      | ^\n",
        "In file included from src/widget.cpp:1:\n",
        "src/widget.cpp: In function 'paint':\n",
        "1 warning generated.\n",
    ],
    ids=["source-context", "include-context", "diagnostic-header", "summary-trailer"],
)
def test_clazy_diagnostics_allow_only_structural_context_and_require_a_finding(
    tmp_path: Path, output: str
) -> None:
    root = tmp_path / "project"
    valid = "src/widget.cpp:3:2: warning: valid [-Wclazy-qstring-arg]\n"

    with_context = parse_clazy_diagnostics(root, root, valid + output, "")
    assert with_context.error == ""
    assert len(with_context.diagnostics) == 1

    context_only = parse_clazy_diagnostics(root, root, output, "")
    assert context_only.error
    assert context_only.diagnostics == ()


def test_clazy_diagnostics_accept_validated_legacy_clang_context(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "datetime.cpp"
    source.parent.mkdir(parents=True)
    source_line = "QDateTime inefficientUtc() { return QDateTime::currentDateTime().toUTC(); }"
    source.write_text(f"#include <QDateTime>\n{source_line}\n", encoding="utf-8")
    output = (
        f"{source}:2:37: warning: Use QDateTime::currentDateTimeUtc() instead "
        "[-Wclazy-qdatetime-utc]\n"
        f"{source_line}\n"
        "                                    ^        ~~~~~\n"
        "                                             ::currentDateTimeUtc()\n"
        "1 warning generated.\n"
    )

    result = parse_clazy_diagnostics(root, root, "", output)

    assert result.error == ""
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.tool_rule_id == "clazy-qdatetime-utc"
    assert diagnostic.target.file_path == "src/datetime.cpp"
    assert diagnostic.target.start_line == 2


@pytest.mark.parametrize(
    "context",
    [
        "QDateTime forged() {}\n                                    ^\n",
        (
            "QDateTime inefficientUtc() { return QDateTime::currentDateTime().toUTC(); }\n"
            "                                    ^\n"
            "                                             ::currentDateTimeUtc()\n"
            "                                             secondPreview()\n"
        ),
        (
            "QDateTime inefficientUtc() { return QDateTime::currentDateTime().toUTC(); }\n"
            "                                    ^\n"
            f"{' ' * MAX_MESSAGE_CHARS}replacement()\n"
        ),
    ],
    ids=["source-mismatch", "duplicate-preview", "oversized-preview"],
)
def test_clazy_diagnostics_reject_invalid_legacy_clang_context_atomically(
    tmp_path: Path, context: str
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "datetime.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#include <QDateTime>\n"
        "QDateTime inefficientUtc() { return QDateTime::currentDateTime().toUTC(); }\n",
        encoding="utf-8",
    )
    output = (
        f"{source}:2:37: warning: Use QDateTime::currentDateTimeUtc() instead "
        "[-Wclazy-qdatetime-utc]\n"
        f"{context}"
        "1 warning generated.\n"
    )

    result = parse_clazy_diagnostics(root, root, "", output)

    assert result.error
    assert result.diagnostics == ()


@pytest.mark.parametrize(
    "suffix",
    [
        "\x00trailing",
        "x" * (MAX_DIAGNOSTIC_OUTPUT_CHARS + 1),
    ],
    ids=["nul", "oversized-output"],
)
def test_clazy_diagnostics_reject_nul_and_output_overflow_atomically(
    tmp_path: Path, suffix: str
) -> None:
    root = tmp_path / "project"
    output = "src/widget.cpp:3:2: warning: valid [-Wclazy-qstring-arg]\n"

    result = parse_clazy_diagnostics(root, root, output + suffix, "")

    assert result.error
    assert result.diagnostics == ()


def test_clazy_diagnostics_reject_count_message_and_line_overflow_atomically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    many = "".join(
        f"src/widget.cpp:{index}:2: warning: valid [-Wclazy-qstring-arg]\n"
        for index in range(1, MAX_DIAGNOSTICS + 2)
    )
    count_result = parse_clazy_diagnostics(root, root, many, "")
    assert count_result.error
    assert count_result.diagnostics == ()

    long_message = (
        "src/widget.cpp:3:2: warning: "
        + ("x" * (MAX_MESSAGE_CHARS + 1))
        + " [-Wclazy-qstring-arg]\n"
    )
    message_result = parse_clazy_diagnostics(root, root, long_message, "")
    assert message_result.error
    assert message_result.diagnostics == ()

    line_result = parse_clazy_diagnostics(
        root,
        root,
        "src/widget.cpp:2147483648:2: warning: valid [-Wclazy-qstring-arg]\n",
        "",
    )
    assert line_result.error
    assert line_result.diagnostics == ()


def test_clazy_diagnostics_do_not_export_fixits(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = (
        "src/widget.cpp:3:2: warning: use QStringLiteral [-Wclazy-qstring-arg]\n"
        'fix-it:"src/widget.cpp":{3:2-3:3}:"QStringLiteral"\n'
    )

    result = parse_clazy_diagnostics(root, root, output, "")

    assert result.error
    assert result.diagnostics == ()


def test_clazy_diagnostics_accept_exact_external_qt_macro_context_from_approved_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    external_include_root = tmp_path / "qt" / "include"
    header = external_include_root / "QtCore" / "qstring.h"
    header.parent.mkdir(parents=True)
    macro_line = "#define QStringLiteral(str) QString::fromUtf8(str)"
    header.write_text("\n" * 41 + macro_line + "\n", encoding="utf-8")

    output = (
        "src/widget.cpp:3:2: warning: use QStringLiteral [-Wclazy-qstring-arg]\n"
        f"{header}:42:5: note: expanded from macro 'QStringLiteral'\n"
        f"{macro_line}\n"
        "    ^~~~~~~~~~~~~\n"
        "    QString::fromUtf8(str)\n"
    )

    result = parse_clazy_diagnostics(
        root,
        root,
        output,
        "",
        source_roots=(external_include_root,),
    )

    assert result.format_name == "clazy-text"
    assert result.error == ""
    assert len(result.diagnostics) == 2
    primary, note = result.diagnostics
    assert primary.tool_rule_id == "clazy-qstring-arg"
    assert primary.target.file_path == "src/widget.cpp"
    assert note.tool_rule_id == primary.tool_rule_id
    assert note.target.target_name == "ClazyNote:clazy-qstring-arg"
    assert note.target.file_path == "[external]"
    assert note.target.start_line == 42
    assert note.target.start_column == 5


@pytest.mark.parametrize(
    "case",
    [
        "forged-preview",
        "outside-approved-root",
    ],
    ids=["forged-preview", "outside-approved-root"],
)
def test_clazy_diagnostics_reject_external_macro_context_atomically(
    tmp_path: Path,
    case: str,
) -> None:
    root = tmp_path / "project"
    external_include_root = tmp_path / "qt" / "include"
    approved_header = external_include_root / "QtCore" / "qstring.h"
    approved_header.parent.mkdir(parents=True)
    macro_line = "#define QStringLiteral(str) QString::fromUtf8(str)"
    approved_header.write_text("\n" * 41 + macro_line + "\n", encoding="utf-8")

    if case == "forged-preview":
        header = approved_header
        preview_line = "#define QStringLiteral(str) forged-preview"
    else:
        header = tmp_path / "unapproved-qt" / "include" / "QtCore" / "qstring.h"
        header.parent.mkdir(parents=True)
        header.write_text("\n" * 41 + macro_line + "\n", encoding="utf-8")
        preview_line = macro_line

    output = (
        "src/widget.cpp:3:2: warning: use QStringLiteral [-Wclazy-qstring-arg]\n"
        f"{header}:42:5: note: expanded from macro 'QStringLiteral'\n"
        f"{preview_line}\n"
        "    ^~~~~~~~~~~~~\n"
        "    QString::fromUtf8(str)\n"
    )

    result = parse_clazy_diagnostics(
        root,
        root,
        output,
        "",
        source_roots=(external_include_root,),
    )

    assert result.format_name == "clazy-text"
    assert result.error
    assert result.diagnostics == ()


def test_clazy_external_source_validation_enforces_a_byte_budget(tmp_path: Path) -> None:
    root = tmp_path / "project"
    external_include_root = tmp_path / "qt" / "include"
    header = external_include_root / "QtCore" / "qstring.h"
    header.parent.mkdir(parents=True)
    prefix = ("U0001f40d" * 1_000 + "\n") * 250
    macro_line = "#define QStringLiteral(str) QString::fromUtf8(str)"
    header.write_text(prefix + macro_line + "\n", encoding="utf-8")
    output = (
        "src/widget.cpp:3:2: warning: use QStringLiteral [-Wclazy-qstring-arg]\n"
        f"{header}:251:5: note: expanded from macro 'QStringLiteral'\n"
        f"{macro_line}\n"
        "    ^~~~~~~~~~~~~\n"
        "    QString::fromUtf8(str)\n"
    )

    result = parse_clazy_diagnostics(
        root,
        root,
        output,
        "",
        source_roots=(external_include_root,),
    )

    assert result.error
    assert result.diagnostics == ()
