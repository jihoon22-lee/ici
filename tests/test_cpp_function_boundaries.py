"""Compiler-backed C++ function-boundary contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ici.core.capabilities import CapabilityInventory
from ici.core.context import (
    AnalysisContext,
    AnalysisIdentity,
    CompilationContext,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import EngineStatus, EvidenceState
from ici.core.runner import ProcessResult
from ici.core.toolchain import ToolCapability
from ici.engines._cpp_function_boundaries import (
    CppFunctionBoundary,
    CppFunctionConfigurationMetric,
    parse_function_boundaries,
    read_cpp_source_text,
    run_cpp_function_boundaries,
)
from ici.engines.complexity import ComplexityEngine
from ici.engines.cpp_text import cpp_definition_name


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path.resolve(strict=True)


def _include_search_output(*paths: Path) -> str:
    body = "\n".join(f" {path}" for path in paths)
    return f"COLLECT_GCC=g++\n#include <...> search starts here:\n{body}\nEnd of search list.\n"


def _context(tmp_path: Path, source_text: str) -> tuple[Path, Path, AnalysisContext]:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    (root / "build").mkdir()
    (root / "include").mkdir()
    (root / "toolchain" / "cxx").mkdir(parents=True)
    (root / "toolchain" / "common").mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")
    compiler = _executable(tmp_path / "tools" / "g++")
    tidy = _executable(tmp_path / "tools" / "clang-tidy")
    capabilities = CapabilityInventory(
        capabilities={
            "g++": ToolCapability(
                name="g++",
                path=str(compiler),
                available=True,
                version="g++ (GCC) 14.2.0",
                version_tuple=(14, 2, 0),
                complete=True,
                returncode=0,
            ),
            "clang-tidy": ToolCapability(
                name="clang-tidy",
                path=str(tidy),
                available=True,
                version="clang-tidy version 18.1.0",
                version_tuple=(18, 1, 0),
                complete=True,
                returncode=0,
            ),
        }
    )
    unit = CompilationUnit(
        source="src/main.cpp",
        directory="build",
        argv=(
            str(compiler),
            "-std=c++20",
            "-I",
            "../include",
            "-MMD",
            "-MF",
            "main.d",
            "-c",
            str(source),
            "-o",
            "main.o",
        ),
        output="build/main.o",
        compiler="g++",
        language="c++",
        standard="c++20",
        configuration=canonical_digest({"configuration": "test"}),
    )
    context = AnalysisContext(
        project=ProjectModel(
            root=root,
            name="function-boundary-project",
            version="1.0.0",
            project_type="cpp",
            source_dirs=("src",),
            cpp_sources=("src/main.cpp",),
            compilable_cpp_sources=("src/main.cpp",),
        ),
        capabilities=capabilities,
        identity=AnalysisIdentity(
            source_commit="unavailable",
            config_digest=canonical_digest({"test": "config"}),
            toolchain_digest=canonical_digest({"test": "toolchain"}),
        ),
        compilation=CompilationContext(
            units=(unit,),
            database_path="build/compile_commands.json",
            database_digest=canonical_digest({"unit": unit.source}),
            origin="cmake",
            generator="Ninja",
            unity_build=False,
        ),
    )
    return root, source, context


def _diagnostic(source: Path, line: int, column: int, name: str, notes: list[str]) -> str:
    rows = [
        f"{source}:{line}:{column}: warning: function '{name}' exceeds recommended "
        "size/complexity thresholds [readability-function-size]"
    ]
    rows.extend(f"{source}:{line}:{column}: note: {note}" for note in notes)
    rows.append("1 warning generated.")
    return "\n".join(rows) + "\n"


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ("int Functor::operator()(int value)", "operator()"),
        ("int Vector::operator[](std::size_t index)", "operator[]"),
        ("explicit operator bool() const", "operator bool"),
        ("bool operator ==(const Value& other) const", "operator=="),
        ("bool operator <(const Value& other) const", "operator<"),
        ("void* operator new [](std::size_t size)", "operator new[]"),
        (
            'std::chrono::milliseconds operator""_ms(const char* value, std::size_t size)',
            'operator""_ms',
        ),
        (
            "auto foo(int value = fallback.operator+(1)) "
            "noexcept(noexcept(value.operator+(1))) "
            "-> decltype(value.operator+(1))",
            "foo",
        ),
        (
            "auto operator +(Value lhs, Value rhs) -> decltype(operator +(lhs, rhs))",
            "operator+",
        ),
    ],
)
def test_cpp_definition_name_preserves_special_operator_spelling(
    signature: str,
    expected: str,
) -> None:
    assert cpp_definition_name(signature) == expected


def test_parser_maps_template_operator_to_the_clang_confirmed_body(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "operator.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "template <typename T>\n"
        "int Box<T>::operator()(int value)\n"
        "{\n"
        "    if (value) {\n"
        "        return value;\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    output = _diagnostic(
        source,
        2,
        13,
        "operator()",
        [
            "5 lines including whitespace and comments (threshold 0)",
            "3 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )

    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )

    assert len(boundaries) == 1
    boundary = boundaries[0]
    assert boundary.file_path == "src/operator.cpp"
    assert boundary.name == "operator()"
    assert (boundary.start_line, boundary.start_column) == (2, 13)
    assert (boundary.body_start_line, boundary.end_line) == (3, 8)
    assert boundary.lines == 5
    assert boundary.statements == 3
    assert boundary.parameters == 1
    assert boundary.function_kind == "operator"
    assert boundary.is_template is True
    assert boundary.origin == "source-spelled"
    assert boundary.configuration_metrics == (
        CppFunctionConfigurationMetric(
            configuration="sha256:one",
            lines=5,
            statements=3,
            parameters=1,
        ),
    )


def test_parser_maps_literal_operator_from_suffix_diagnostic_column(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "literal_operator.cpp"
    source.parent.mkdir(parents=True)
    source_text = (
        'unsigned long long operator""_units(unsigned long long value) {\n'
        "    if (value) { return value; }\n"
        "    return 0;\n"
        "}\n"
    )
    source.write_text(source_text, encoding="utf-8")
    output = _diagnostic(
        source,
        1,
        source_text.index("_units") + 1,
        'operator""_units',
        [
            "3 lines including whitespace and comments (threshold 0)",
            "4 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:literal",
    )[0]

    assert boundary.name == 'operator""_units'
    assert boundary.function_kind == "operator"
    assert (boundary.body_start_line, boundary.end_line) == (1, 4)


def test_parser_excludes_macro_generated_function_with_scope_record(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "macro.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#define DEFINE_FUNCTION(name) int name() { return 1; }\n"
        "DEFINE_FUNCTION(generated)\n"
        "int real_function() { return 2; }\n",
        encoding="utf-8",
    )
    excluded_scopes: list[tuple[str, int, int | None, str]] = []

    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        _diagnostic(
            source,
            2,
            1,
            "generated",
            ["1 statements (threshold 0)"],
        ),
        configuration="sha256:macro",
        excluded_scopes=excluded_scopes,
    )

    assert boundaries == ()
    assert excluded_scopes == [("src/macro.cpp", 2, 1, "generated")]


def test_parser_excludes_multiline_local_macro_invocation_without_next_brace_mapping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "macro.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "#define DECLARE_FUNCTION(name) int name() { return 1; }\n"
        "DECLARE_FUNCTION(\n"
        "    generated\n"
        ")\n"
        "int real_function() { return 2; }\n",
        encoding="utf-8",
    )
    excluded_scopes: list[tuple[str, int, int | None, str]] = []
    generated = _diagnostic(
        source,
        2,
        5,
        "generated",
        ["1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    real = _diagnostic(
        source,
        5,
        5,
        "real_function",
        ["1 statements (threshold 0)"],
    )

    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        generated + real,
        configuration="sha256:multiline-macro",
        excluded_scopes=excluded_scopes,
    )

    assert [boundary.name for boundary in boundaries] == ["real_function"]
    assert excluded_scopes == [("src/macro.cpp", 2, 5, "generated")]


def test_parser_keeps_uppercase_source_functions_unless_a_macro_call_proves_expansion(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "uppercase.cpp"
    source.parent.mkdir(parents=True)
    source_text = (
        "#define DECLARE_FUNCTION(name) int name() { return 1; }\n"
        "DECLARE_FUNCTION(generated)\n"
        "int FOO() { return 2; }\n"
        "int DEFINE_FUNCTION() { return 3; }\n"
    )
    source.write_text(source_text, encoding="utf-8")
    excluded_scopes: list[tuple[str, int, int | None, str]] = []
    generated = _diagnostic(
        source,
        2,
        1,
        "generated",
        ["1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    foo = _diagnostic(
        source,
        3,
        source_text.splitlines()[2].index("FOO") + 1,
        "FOO",
        ["1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    define_function = _diagnostic(
        source,
        4,
        source_text.splitlines()[3].index("DEFINE_FUNCTION") + 1,
        "DEFINE_FUNCTION",
        ["1 statements (threshold 0)"],
    )

    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        generated + foo + define_function,
        configuration="sha256:uppercase",
        excluded_scopes=excluded_scopes,
    )

    assert [boundary.name for boundary in boundaries] == ["FOO", "DEFINE_FUNCTION"]
    assert excluded_scopes == [("src/uppercase.cpp", 2, 1, "generated")]


def test_parser_maps_same_line_class_and_namespace_members_independently(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "inline.cpp"
    source.parent.mkdir(parents=True)
    source_text = (
        "struct Box { int first() { return 1; } "
        "int second(int value) { if (value) { return 2; } return 0; } }; "
        "namespace ns { int third() { return 3; } "
        "int fourth(int value) { if (value) { return 4; } return 0; } }\n"
    )
    source.write_text(source_text, encoding="utf-8")
    diagnostics: list[str] = []
    for name, statements in (
        ("first", 1),
        ("second", 3),
        ("third", 1),
        ("fourth", 3),
    ):
        diagnostics.append(
            _diagnostic(
                source,
                1,
                source_text.index(name) + 1,
                name,
                [f"{statements} statements (threshold 0)"],
            ).replace("1 warning generated.\n", "")
        )

    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        "".join(diagnostics) + "4 warnings generated.\n",
        configuration="sha256:inline-members",
    )

    assert [boundary.name for boundary in boundaries] == ["first", "second", "third", "fourth"]
    for boundary in boundaries:
        start = source_text.index(boundary.name)
        opened = source_text.index("{", start)
        assert boundary.start_line == boundary.end_line == 1
        assert boundary.start_column == start + 1
        assert boundary.body_start_column == opened + 1
    first = boundaries[0]
    second = boundaries[1]
    third = boundaries[2]
    fourth = boundaries[3]
    assert (
        first.end_column
        == source_text.index("}", source_text.index("{", source_text.index("first"))) + 1
    )
    class_close = source_text.index("};", source_text.index("second"))
    assert second.end_column == class_close - 1
    assert (
        third.end_column
        == source_text.index("}", source_text.index("{", source_text.index("third"))) + 1
    )
    namespace_close = source_text.rfind("}")
    assert fourth.end_column == namespace_close - 1


def test_parser_keeps_operator_prefixed_identifiers_as_ordinary_functions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "ordinary.cpp"
    source.parent.mkdir(parents=True)
    source_text = "int operatorHelper() { return 1; } int operator_helper() { return 2; }\n"
    source.write_text(source_text, encoding="utf-8")
    diagnostics = []
    for name in ("operatorHelper", "operator_helper"):
        diagnostics.append(
            _diagnostic(
                source,
                1,
                source_text.index(name) + 1,
                name,
                ["1 statements (threshold 0)"],
            ).replace("1 warning generated.\n", "")
        )

    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        "".join(diagnostics) + "2 warnings generated.\n",
        configuration="sha256:ordinary",
    )

    assert [boundary.name for boundary in boundaries] == ["operatorHelper", "operator_helper"]
    assert [boundary.function_kind for boundary in boundaries] == ["function", "function"]


def test_parser_chooses_constructor_body_after_braced_initializer(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "constructor.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "struct Widget { int value_; Widget(); };\nWidget::Widget() : value_{1} {}\n",
        encoding="utf-8",
    )
    output = _diagnostic(
        source,
        2,
        9,
        "Widget",
        ["1 statements (threshold 0)"],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )[0]

    assert boundary.body_start_column == 30
    assert boundary.end_column == 31


def test_parser_keeps_same_line_function_boundaries_separate(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "same_line.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int first() { return 1; } int second() { return 2; }\n",
        encoding="utf-8",
    )
    output = _diagnostic(
        source,
        1,
        5,
        "first",
        ["1 statements (threshold 0)"],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )[0]

    assert boundary.body_start_column == 13
    assert boundary.end_column == 25


@pytest.mark.parametrize(
    ("source_text", "body_marker"),
    [
        ("int measured(int value = {1}) { return value; }\n", ") {"),
        ("int measured(int value = [] { return 1; }()) { return value; }\n", ") {"),
        ("int measured() noexcept(noexcept(int{1})) { return 1; }\n", ") {"),
    ],
)
def test_parser_skips_braced_expressions_before_a_same_line_body(
    tmp_path: Path,
    source_text: str,
    body_marker: str,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(source_text, encoding="utf-8")
    output = _diagnostic(
        source,
        1,
        5,
        "measured",
        ["1 statements (threshold 0)"],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )[0]

    expected_open = source_text.rindex(body_marker) + len(body_marker)
    expected_close = source_text.rindex("}") + 1
    assert boundary.body_start_column == expected_open
    assert boundary.end_column == expected_close


def test_parser_maps_function_try_block_through_its_catch_handlers(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "struct Widget {\n"
        "    int value;\n"
        "    Widget(int input) try : value{input} {\n"
        "        if (input < 0) { throw input; }\n"
        "    } catch (...) {\n"
        "        value = 0;\n"
        "    }\n"
        "};\n",
        encoding="utf-8",
    )
    output = _diagnostic(
        source,
        3,
        5,
        "Widget",
        [
            "4 lines including whitespace and comments (threshold 0)",
            "5 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )[0]

    assert (boundary.body_start_line, boundary.end_line) == (3, 7)
    assert boundary.body_start_column == 42
    assert boundary.end_column == 5
    assert ComplexityEngine(root)._cpp_boundary_metrics(
        source.read_text(encoding="utf-8"), boundary
    ) == (3, 1)


def test_parser_supports_cpp_brace_digraphs(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int measured() <% if (true) <% return 1; %> return 0; %>\n", encoding="utf-8"
    )
    output = _diagnostic(
        source,
        1,
        5,
        "measured",
        ["3 statements (threshold 0)"],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )[0]

    text = source.read_text(encoding="utf-8")
    assert boundary.body_start_column == text.index("<%") + 1
    assert boundary.end_column == text.rindex("%>") + 1
    assert ComplexityEngine(root)._cpp_boundary_metrics(text, boundary) == (2, 1)


@pytest.mark.parametrize(
    "requires_expression",
    ["requires { value + 1; }", "requires(T candidate) { candidate + 1; }"],
)
def test_parser_skips_trailing_requires_expression_before_function_body(
    tmp_path: Path,
    requires_expression: str,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source_text = (
        f"template <class T> int constrained(T value) requires {requires_expression} "
        "{ if (value) { return 1; } return 0; }\n"
    )
    source.write_text(source_text, encoding="utf-8")
    output = _diagnostic(
        source,
        1,
        source_text.index("constrained") + 1,
        "constrained",
        ["3 statements (threshold 0)", "1 parameters (threshold 0)"],
    )

    boundary = parse_function_boundaries(
        root,
        root,
        "",
        output,
        configuration="sha256:one",
    )[0]

    expected_body = source_text.index(" { if") + 2
    assert boundary.body_start_column == expected_body
    assert boundary.end_column == source_text.rindex("}") + 1
    assert ComplexityEngine(root)._cpp_boundary_metrics(source_text, boundary) == (2, 1)


def test_parser_requires_a_lines_note_for_a_multi_line_body(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int measured() {\n    return 1;\n}\n", encoding="utf-8")
    output = _diagnostic(
        source,
        1,
        5,
        "measured",
        ["1 statements (threshold 0)"],
    )

    with pytest.raises(ValueError, match="lines note"):
        parse_function_boundaries(
            root,
            root,
            "",
            output,
            configuration="sha256:one",
        )


def test_parser_rejects_a_metric_note_at_another_location(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int measured() { return 1; }\n", encoding="utf-8")
    output = (
        f"{source}:1:5: warning: function 'measured' exceeds recommended "
        "size/complexity thresholds [readability-function-size]\n"
        f"{source}:1:6: note: 1 statements (threshold 0)\n"
        "1 warning generated.\n"
    )

    with pytest.raises(ValueError, match="parent location"):
        parse_function_boundaries(
            root,
            root,
            "",
            output,
            configuration="sha256:one",
        )


def test_parser_rejects_suppressed_or_unexpected_diagnostics(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")

    with pytest.raises(ValueError, match="suppressed"):
        parse_function_boundaries(
            root,
            root,
            "",
            "Suppressed 1 warning (1 NOLINT).\n",
            configuration="sha256:one",
        )
    with pytest.raises(ValueError, match="suppressed"):
        parse_function_boundaries(
            root,
            root,
            "",
            "Suppressed 2 warnings (1 in non-user code).\n",
            configuration="sha256:one",
        )
    with pytest.raises(ValueError, match="unexpected check"):
        parse_function_boundaries(
            root,
            root,
            "",
            f"{source}:1:5: warning: finding [modernize-use-nullptr]\n",
            configuration="sha256:one",
        )


def test_parser_allows_only_external_suppression_summary(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int main() { return 0; }\n", encoding="utf-8")
    output = _diagnostic(
        source,
        1,
        5,
        "main",
        ["1 statements (threshold 0)"],
    )

    boundaries = parse_function_boundaries(
        root,
        root,
        output,
        "Suppressed 13603 warnings (13603 in non-user code).\n",
        configuration="sha256:one",
    )

    assert [boundary.name for boundary in boundaries] == ["main"]


def test_parser_rejects_suppression_only_output_even_for_external_only_summary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(ValueError):
        parse_function_boundaries(
            root,
            root,
            "",
            "Suppressed 1 warning (1 in non-user code).\n",
            configuration="sha256:suppression-only",
        )


def test_adapter_uses_exact_sanitized_context_and_records_evidence(tmp_path: Path) -> None:
    root, source, context = _context(
        tmp_path,
        "int measured(int value) {\n    if (value) { return value; }\n    return 0;\n}\n",
    )
    tidy_output = _diagnostic(
        source,
        1,
        5,
        "measured",
        [
            "3 lines including whitespace and comments (threshold 0)",
            "3 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        calls.append((command, kwargs))
        return ProcessResult(0, "", tidy_output, 0.01)

    outcome = run_cpp_function_boundaries(
        root,
        [source],
        context,
        runner=runner,
    )

    assert outcome.mode == "exact"
    assert outcome.errors == []
    assert outcome.sources_checked == 1
    assert outcome.configurations_checked == 1
    assert [item.name for item in outcome.boundaries] == ["measured"]
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[1:3] == ["--use-color=false", "--checks=-*,readability-function-size"]
    assert command[3].startswith('--config={"CheckOptions":')
    assert command[4].startswith("--header-filter=^")
    assert command[5:7] == [str(source.resolve(strict=True)), "--"]
    assert "-std=c++20" in command
    assert "-nostdinc++" in command
    assert command[-1] == "-w"
    assert all(value not in command for value in ("-p", "--fix", "-MMD", "-MF", "-c", "-o"))
    assert kwargs["replace_env"] is True
    assert kwargs["input_text"] == ""
    tool = [item for item in outcome.evidence if item.name == "clang-tidy function boundaries"]
    assert len(tool) == 1
    assert tool[0].returncode == 0
    assert tool[0].error == ""


def test_adapter_rejects_a_source_changed_during_the_tool_run(tmp_path: Path) -> None:
    root, source, context = _context(
        tmp_path,
        "int measured() {\n    return 1;\n}\n",
    )
    tidy_output = _diagnostic(
        source,
        1,
        5,
        "measured",
        [
            "2 lines including whitespace and comments (threshold 0)",
            "1 statements (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        source.write_text("int changed() { return 2; }\n", encoding="utf-8")
        return ProcessResult(0, "", tidy_output, 0.01)

    outcome = run_cpp_function_boundaries(root, [source], context, runner=runner)

    assert outcome.mode == "error"
    assert outcome.boundaries == []
    assert outcome.errors == ["function boundary source changed during analysis: src/main.cpp"]
    tidy_evidence = [
        item for item in outcome.evidence if item.name == "clang-tidy function boundaries"
    ]
    assert len(tidy_evidence) == 1
    assert tidy_evidence[0].error == outcome.errors[0]


def test_adapter_rejects_a_tool_replaced_after_capability_validation(tmp_path: Path) -> None:
    root, source, context = _context(tmp_path, "int measured() { return 1; }\n")
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)
    tidy = Path(context.capabilities.capabilities["clang-tidy"].path).resolve(strict=True)
    replaced = False

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        nonlocal replaced
        if Path(command[0]).resolve(strict=True) == compiler:
            if not replaced:
                tidy.unlink()
                _executable(tidy)
                replaced = True
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        pytest.fail("replaced clang-tidy must not execute")

    outcome = run_cpp_function_boundaries(root, [source], context, runner=runner)

    assert outcome.mode == "error"
    assert outcome.boundaries == []
    assert outcome.errors == [
        "function boundary tool identity changed before execution: src/main.cpp"
    ]
    tidy_evidence = [
        item for item in outcome.evidence if item.name == "clang-tidy function boundaries"
    ]
    assert len(tidy_evidence) == 1
    assert tidy_evidence[0].error == outcome.errors[0]


def test_adapter_rejects_a_stale_caller_source_snapshot(tmp_path: Path) -> None:
    root, source, context = _context(tmp_path, "int measured() { return 1; }\n")

    outcome = run_cpp_function_boundaries(
        root,
        [source],
        context,
        runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
        source_texts={"src/main.cpp": "int stale() { return 0; }\n"},
    )

    assert outcome.mode == "error"
    assert outcome.boundaries == []
    assert outcome.errors == ["function boundary source snapshot changed: src/main.cpp"]


def test_parser_enforces_its_deadline(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text("int measured() { return 1; }\n", encoding="utf-8")
    output = _diagnostic(
        source,
        1,
        5,
        "measured",
        ["1 statements (threshold 0)"],
    )

    with pytest.raises(ValueError, match="parser budget"):
        parse_function_boundaries(
            root,
            root,
            "",
            output,
            configuration="sha256:one",
            deadline=0.0,
        )


def test_parser_reads_and_masks_each_source_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    source = root / "src" / "main.cpp"
    source.parent.mkdir(parents=True)
    source.write_text(
        "int first() { return 1; }\nint second() { return 2; }\n",
        encoding="utf-8",
    )
    first = _diagnostic(
        source,
        1,
        5,
        "first",
        ["1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    second = _diagnostic(
        source,
        2,
        5,
        "second",
        ["1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    calls = 0

    def counted_reader(project_root: Path, file_path: str) -> str:
        nonlocal calls
        calls += 1
        return read_cpp_source_text(project_root, file_path)

    monkeypatch.setattr(
        "ici.engines._cpp_function_boundaries.read_cpp_source_text",
        counted_reader,
    )
    boundaries = parse_function_boundaries(
        root,
        root,
        "",
        first + second + "2 warnings generated.\n",
        configuration="sha256:one",
    )

    assert [item.name for item in boundaries] == ["first", "second"]
    assert calls == 1


def test_configuration_dependent_geometry_stays_partial_and_estimated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "int configured() {\n    if (true) {\n    }\n}\n",
    )
    first = context.compilation.units[0]
    second_argv = list(first.argv)
    second_argv.insert(second_argv.index("-c"), "-DSECOND_CONFIGURATION")
    second = replace(
        first,
        argv=tuple(second_argv),
        configuration=canonical_digest({"configuration": "second"}),
    )
    context = replace(
        context,
        compilation=replace(context.compilation, units=(first, second)),
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", "", 0.01)

    def fake_parser(
        _root: Path,
        _cwd: Path,
        _stdout: str,
        _stderr: str,
        *,
        configuration: str,
        **_kwargs: object,
    ) -> tuple[CppFunctionBoundary, ...]:
        end_line = 3 if configuration == second.configuration else 4
        return (
            CppFunctionBoundary(
                file_path="src/main.cpp",
                start_line=1,
                end_line=end_line,
                start_column=5,
                end_column=1,
                body_start_line=1,
                body_start_column=18,
                name="configured",
                lines=end_line - 1,
                configurations=(configuration,),
            ),
        )

    monkeypatch.setattr(
        "ici.engines._cpp_function_boundaries.parse_function_boundaries",
        fake_parser,
    )

    outcome = run_cpp_function_boundaries(root, [source], context, runner=runner)

    assert outcome.mode == "partial"
    assert outcome.boundaries == []
    assert outcome.configurations_checked == 2
    assert len(outcome.warnings) == 1
    assert "configuration-dependent" in outcome.warnings[0]

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    automatic = ComplexityEngine(root, analysis_context=context).run()
    required = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert automatic.status == EngineStatus.PASS
    assert automatic.evidence == EvidenceState.ESTIMATED
    assert automatic.extra["cpp_boundary_mode"] == "partial"
    assert automatic.extra["cpp_exact_boundaries"] == 0
    assert automatic.extra["cpp_estimated_boundaries"] == 1
    assert required.status == EngineStatus.ERROR
    assert required.evidence == EvidenceState.NOT_RUN
    assert required.extra["cpp_boundary_mode"] == "error"


def test_same_geometry_with_metric_variants_stays_partial_and_exposes_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "int configured(int value) {\n"
        "    if (value) {\n"
        "        return value;\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
    )
    first = context.compilation.units[0]
    second_argv = list(first.argv)
    second_argv.insert(second_argv.index("-c"), "-DSECOND_CONFIGURATION")
    second = replace(
        first,
        argv=tuple(second_argv),
        configuration=canonical_digest({"configuration": "second"}),
    )
    context = replace(
        context,
        compilation=replace(context.compilation, units=(first, second)),
    )
    first_output = _diagnostic(
        source,
        1,
        5,
        "configured",
        [
            "5 lines including whitespace and comments (threshold 0)",
            "1 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )
    second_output = _diagnostic(
        source,
        1,
        5,
        "configured",
        [
            "5 lines including whitespace and comments (threshold 0)",
            "3 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        output = second_output if "-DSECOND_CONFIGURATION" in command else first_output
        return ProcessResult(0, "", output, 0.01)

    outcome = run_cpp_function_boundaries(root, [source], context, runner=runner)

    assert outcome.mode == "partial"
    assert outcome.errors == []
    assert len(outcome.boundaries) == 1
    boundary = outcome.boundaries[0]
    assert boundary.metric_variant is True
    assert boundary.configurations == tuple(sorted({first.configuration, second.configuration}))
    assert {
        item.configuration: (item.lines, item.statements, item.parameters)
        for item in boundary.configuration_metrics
    } == {
        first.configuration: (5, 1, 1),
        second.configuration: (5, 3, 1),
    }
    assert any("metrics remain estimated" in warning for warning in outcome.warnings)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    automatic = ComplexityEngine(root, analysis_context=context).run()
    required = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert automatic.status == EngineStatus.PASS
    assert automatic.evidence == EvidenceState.ESTIMATED
    assert automatic.extra["cpp_boundary_mode"] == "partial"
    assert automatic.extra["cpp_exact_boundaries"] == 1
    assert automatic.extra["cpp_estimated_boundaries"] == 0
    assert automatic.targets[0].metrics["metric_variant"] is True
    assert automatic.targets[0].metrics["metric_confidence"] == "low"
    assert required.status == EngineStatus.ERROR
    assert required.evidence == EvidenceState.NOT_RUN
    assert required.extra["cpp_boundary_mode"] == "error"
    assert "partial/low-confidence" in required.extra["cpp_boundary_errors"][-1]


def test_preprocessor_conditional_boundary_is_partial_and_low_confidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "int conditional(int value) {\n"
        "#if defined(VARIANT)\n"
        "    if (value) {\n"
        "        return 1;\n"
        "    }\n"
        "#endif\n"
        "    return 0;\n"
        "}\n",
    )
    output = _diagnostic(
        source,
        1,
        5,
        "conditional",
        [
            "7 lines including whitespace and comments (threshold 0)",
            "1 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", output, 0.01)

    outcome = run_cpp_function_boundaries(root, [source], context, runner=runner)

    assert outcome.mode == "partial"
    assert outcome.errors == []
    assert len(outcome.boundaries) == 1
    assert outcome.boundaries[0].preprocessor_conditional is True
    assert any("preprocessor-dependent" in warning for warning in outcome.warnings)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    automatic = ComplexityEngine(root, analysis_context=context).run()
    required = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert automatic.status == EngineStatus.PASS
    assert automatic.evidence == EvidenceState.ESTIMATED
    assert automatic.extra["cpp_boundary_mode"] == "partial"
    assert automatic.targets[0].metrics["metric_confidence"] == "low"
    assert automatic.targets[0].metrics["preprocessor_conditional"] is True
    assert required.status == EngineStatus.ERROR
    assert required.evidence == EvidenceState.NOT_RUN
    assert required.extra["cpp_boundary_mode"] == "error"
    assert required.extra["cpp_boundary_errors"]


def test_boundary_missing_from_one_successful_configuration_stays_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "int configured() {\n    return 1;\n}\n",
    )
    first = context.compilation.units[0]
    second_argv = list(first.argv)
    second_argv.insert(second_argv.index("-c"), "-DSECOND_CONFIGURATION")
    second = replace(
        first,
        argv=tuple(second_argv),
        configuration=canonical_digest({"configuration": "second"}),
    )
    context = replace(
        context,
        compilation=replace(context.compilation, units=(first, second)),
    )
    diagnostic = _diagnostic(
        source,
        1,
        5,
        "configured",
        [
            "2 lines including whitespace and comments (threshold 0)",
            "1 statements (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        output = "" if "-DSECOND_CONFIGURATION" in command else diagnostic
        return ProcessResult(0, "", output, 0.01)

    outcome = run_cpp_function_boundaries(root, [source], context, runner=runner)

    assert outcome.mode == "partial"
    assert outcome.errors == []
    assert outcome.boundaries == []
    assert outcome.configurations_checked == 2
    assert outcome.sources_checked == 1
    assert len(outcome.warnings) == 1
    assert "configuration-dependent" in outcome.warnings[0]

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    automatic = ComplexityEngine(root, analysis_context=context).run()
    required = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert automatic.status == EngineStatus.PASS
    assert automatic.evidence == EvidenceState.ESTIMATED
    assert automatic.extra["cpp_boundary_mode"] == "partial"
    assert automatic.extra["cpp_exact_boundaries"] == 0
    assert automatic.extra["cpp_estimated_boundaries"] == 1
    assert required.status == EngineStatus.ERROR
    assert required.evidence == EvidenceState.NOT_RUN


def test_complexity_prefers_exact_operator_boundary_and_discloses_confidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "struct Functor {\n"
        "    int operator()(int value) {\n"
        "        if (value) { return value; }\n"
        "        return 0;\n"
        "    }\n"
        "};\n",
    )
    tidy_output = _diagnostic(
        source,
        2,
        9,
        "operator()",
        [
            "3 lines including whitespace and comments (threshold 0)",
            "3 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", tidy_output, 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    result = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_boundary_mode"] == "exact"
    assert result.extra["cpp_exact_boundaries"] == 1
    assert result.extra["cpp_estimated_boundaries"] == 0
    assert len(result.targets) == 1
    target = result.targets[0]
    assert target.target_name == "operator()"
    assert (target.start_line, target.end_line) == (2, 5)
    assert target.metrics["complexity"] == 2
    assert target.metrics["boundary_source"] == "clang-tidy-ast"
    assert target.metrics["boundary_confidence"] == "exact"


def test_exact_same_line_functions_keep_their_own_complexity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "int first() { return 1; } int second(int value) { if (value) { return 2; } return 0; }\n",
    )
    first = _diagnostic(
        source,
        1,
        5,
        "first",
        ["1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    second = _diagnostic(
        source,
        1,
        31,
        "second",
        [
            "1 parameters (threshold 0)",
            "3 statements (threshold 0)",
        ],
    ).replace("1 warning generated.\n", "")
    tidy_output = first + second + "2 warnings generated.\n"
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", tidy_output, 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    result = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert result.status == EngineStatus.PASS
    assert result.extra["cpp_exact_boundaries"] == 2
    assert result.extra["cpp_estimated_boundaries"] == 0
    metrics = {target.target_name: target.metrics["complexity"] for target in result.targets}
    assert metrics == {"first()": 1, "second()": 2}


def test_exact_same_line_overloads_do_not_leave_an_estimated_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_text = (
        "int same(int value) { return value; } "
        "int same(double value) { if (value > 0) { return 1; } return 0; }\n"
    )
    root, source, context = _context(tmp_path, source_text)
    first = _diagnostic(
        source,
        1,
        source_text.index("same(int") + 1,
        "same",
        ["1 parameters (threshold 0)", "1 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    second = _diagnostic(
        source,
        1,
        source_text.index("same(double") + 1,
        "same",
        ["1 parameters (threshold 0)", "3 statements (threshold 0)"],
    ).replace("1 warning generated.\n", "")
    tidy_output = first + second + "2 warnings generated.\n"
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", tidy_output, 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    result = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_boundary_mode"] == "exact"
    assert result.extra["cpp_exact_boundaries"] == 2
    assert result.extra["cpp_estimated_boundaries"] == 0
    assert [target.target_name for target in result.targets] == ["same()", "same()"]
    assert sorted(target.metrics["complexity"] for target in result.targets) == [1, 2]


@pytest.mark.parametrize("initializer", ["[]", "+[]"])
def test_lambda_initializer_does_not_leave_a_phantom_estimated_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    initializer: str,
) -> None:
    root, source, context = _context(
        tmp_path,
        f"void (*callback)() = {initializer} {{ return; }};\n"
        "int measured(int value) { if (value) { return 1; } return 0; }\n",
    )
    tidy_output = _diagnostic(
        source,
        2,
        5,
        "measured",
        ["1 parameters (threshold 0)", "3 statements (threshold 0)"],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", tidy_output, 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    result = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_boundary_mode"] == "exact"
    assert result.extra["cpp_exact_boundaries"] == 1
    assert result.extra["cpp_estimated_boundaries"] == 0
    assert [target.target_name for target in result.targets] == ["measured()"]


def test_required_boundary_tool_fails_closed_but_auto_falls_back(tmp_path: Path) -> None:
    root, _source, context = _context(tmp_path, "int fallback() { return 0; }\n")
    context = AnalysisContext(
        project=context.project,
        capabilities=CapabilityInventory(
            capabilities={"g++": context.capabilities.capabilities["g++"]}
        ),
        identity=context.identity,
        compilation=context.compilation,
    )

    required = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()
    automatic = ComplexityEngine(root, analysis_context=context).run()

    assert required.status == EngineStatus.ERROR
    assert required.evidence == EvidenceState.NOT_RUN
    assert required.extra["cpp_boundary_mode"] == "error"
    assert required.extra["cpp_boundary_errors"]
    assert required.extra["total_functions"] == 1
    assert required.extra["issues_count"] == 1
    assert "across 1 functions (1 issues)" in required.summary
    assert automatic.status == EngineStatus.PASS
    assert automatic.evidence == EvidenceState.ESTIMATED
    assert automatic.extra["cpp_boundary_mode"] == "heuristic"
    assert automatic.targets[0].metrics["boundary_source"] == "heuristic"


def test_successful_empty_cpp_analysis_remains_exact_and_measured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _source, context = _context(tmp_path, "// no function definitions\n")
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    result = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert result.status == EngineStatus.PASS
    assert result.evidence == EvidenceState.MEASURED
    assert result.extra["cpp_boundary_mode"] == "exact"
    assert result.extra["cpp_exact_boundaries"] == 0
    assert result.extra["cpp_estimated_boundaries"] == 0
    assert result.extra["total_functions"] == 0


def test_successful_empty_cpp_output_with_fallback_targets_is_not_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _source, context = _context(
        tmp_path,
        "int fallback(int value) { if (value) { return 1; } return 0; }\n",
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", "", 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    automatic = ComplexityEngine(root, analysis_context=context).run()
    required = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert automatic.status == EngineStatus.PASS
    assert automatic.evidence == EvidenceState.ESTIMATED
    assert automatic.extra["cpp_boundary_mode"] == "partial"
    assert automatic.extra["cpp_boundary_warnings"] == [
        "compiler-backed function output omitted source-scanned definitions"
    ]
    assert automatic.extra["cpp_exact_boundaries"] == 0
    assert automatic.extra["cpp_estimated_boundaries"] == 1
    assert automatic.targets[0].metrics["boundary_source"] == "heuristic"
    assert required.status == EngineStatus.ERROR
    assert required.evidence == EvidenceState.NOT_RUN
    assert required.extra["cpp_boundary_mode"] == "error"
    assert required.extra["cpp_boundary_errors"]


def test_complexity_stops_before_tooling_when_source_inventory_exceeds_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _source, context = _context(tmp_path, "int measured() { return 1; }\n")
    monkeypatch.setattr("ici.engines.complexity._MAX_CPP_COMPLEXITY_SOURCE_BYTES", 1)
    monkeypatch.setattr(
        "ici.engines.complexity.run_cpp_function_boundaries",
        lambda *_args, **_kwargs: pytest.fail("tool adapter must not be called"),
    )

    result = ComplexityEngine(root, analysis_context=context).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["cpp_boundary_mode"] == "error"
    assert result.extra["total_functions"] == 0
    assert result.extra["cpp_boundary_errors"] == [
        "C++ complexity source inventory exceeds the bounded limit"
    ]


def test_adapter_stops_before_snapshotting_units_over_the_run_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(tmp_path, "int measured() { return 1; }\n")
    unit = context.compilation.units[0]
    monkeypatch.setattr("ici.engines._cpp_function_boundaries._MAX_SELECTED_UNITS", 1)
    context = replace(
        context,
        compilation=replace(
            context.compilation,
            units=(unit, replace(unit, output="build/second.o")),
        ),
    )

    outcome = run_cpp_function_boundaries(
        root,
        [source],
        context,
        runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
    )

    assert outcome.mode == "error"
    assert outcome.boundaries == []
    assert outcome.errors == ["function boundary translation-unit count exceeds the bounded limit"]
    assert outcome.sources_checked == 0
    assert outcome.configurations_checked == 0


def test_adapter_fails_closed_when_prepared_mapped_source_cache_exceeds_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(tmp_path, "int first() { return 1; }\n")
    second = root / "src" / "second.cpp"
    second.write_text("int second() { return 2; }\n", encoding="utf-8")
    first_unit = context.compilation.units[0]
    second_argv = tuple(
        str(second) if argument == str(source) else argument for argument in first_unit.argv
    )
    second_unit = replace(
        first_unit,
        source="src/second.cpp",
        argv=second_argv,
        output="build/second.o",
        configuration=canonical_digest({"configuration": "second"}),
    )
    context = replace(
        context,
        compilation=replace(context.compilation, units=(first_unit, second_unit)),
    )
    monkeypatch.setattr(
        "ici.engines._cpp_function_boundaries._MAX_SOURCE_CACHE_BYTES",
        len(source.read_bytes()),
    )

    outcome = run_cpp_function_boundaries(
        root,
        [source, second],
        context,
        runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
    )

    assert outcome.mode == "error"
    assert outcome.boundaries == []
    assert outcome.errors == ["function boundary source cache exceeds the bounded limit"]
    assert outcome.sources_checked == 0
    assert outcome.configurations_checked == 0


def test_adapter_reports_an_outside_source_as_a_structured_error(tmp_path: Path) -> None:
    root, _source, context = _context(tmp_path, "int inside() { return 0; }\n")
    outside = tmp_path / "outside.cpp"
    outside.write_text("int outside() { return 0; }\n", encoding="utf-8")

    outcome = run_cpp_function_boundaries(
        root,
        [outside],
        context,
        runner=lambda *_args, **_kwargs: pytest.fail("runner must not be called"),
    )

    assert outcome.mode == "error"
    assert outcome.boundaries == []
    assert outcome.errors == ["function boundary source selection is outside the project"]


def test_required_mode_rejects_functions_left_to_the_source_scanner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, source, context = _context(
        tmp_path,
        "int measured(int value) {\n"
        "    if (value) { return value; }\n"
        "    return 0;\n"
        "}\n"
        "void empty() {}\n",
    )
    tidy_output = _diagnostic(
        source,
        1,
        5,
        "measured",
        [
            "3 lines including whitespace and comments (threshold 0)",
            "3 statements (threshold 0)",
            "1 parameters (threshold 0)",
        ],
    )
    compiler = Path(context.capabilities.capabilities["g++"].path).resolve(strict=True)

    def runner(command: list[str], **_kwargs: object) -> ProcessResult:
        if Path(command[0]).resolve(strict=True) == compiler:
            language = command[command.index("-x") + 1]
            common = root / "toolchain" / "common"
            paths = (root / "toolchain" / "cxx", common) if language == "c++" else (common,)
            return ProcessResult(0, "", _include_search_output(*paths), 0.01)
        return ProcessResult(0, "", tidy_output, 0.01)

    monkeypatch.setattr("ici.engines.complexity.run_process", runner)
    result = ComplexityEngine(
        root,
        {"engines": {"complexity": {"cpp_boundaries": "required"}}},
        analysis_context=context,
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.extra["total_functions"] == 2
    assert result.extra["cpp_exact_boundaries"] == 1
    assert result.extra["cpp_estimated_boundaries"] == 1
    assert "1 function(s) still needed source scanning" in result.extra["cpp_boundary_errors"][-1]
