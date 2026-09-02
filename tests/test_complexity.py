"""Tests for Complexity and Exception Safety Engines."""

import ast
from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines.complexity import ComplexityEngine
from ici.engines.exception import ExceptionSafetyEngine


def test_complexity_engine(tmp_python_project: Path):
    engine = ComplexityEngine(tmp_python_project)
    res = engine.run()
    assert res.status == EngineStatus.PASS
    assert res.score is not None
    assert len(res.targets) > 0


def test_python_complexity_excludes_nested_scope_bodies(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "scopes.py").write_text(
        """def outer(x, xs):
    if x:
        def inner(y):
            if y:
                return 1
            return 0
        @class_deco(x if x else 0)
        class Holder(Base(x) if x else Base):
            if x:
                marker = 1
            def method(self, y):
                if y:
                    return 1
                return 0
        chooser = lambda value: value if value and x else 0
        return inner(x)
    return 0
""",
        encoding="utf-8",
    )

    result = ComplexityEngine(tmp_path).run()

    assert result.status == EngineStatus.PASS
    assert result.score == 4.0
    assert result.extra["max_complexity"] == 4
    assert result.extra["total_functions"] == 3
    rows = {
        (target.target_name, target.start_line): (
            target.metrics["complexity"],
            target.metrics["nesting"],
        )
        for target in result.targets
    }
    assert rows == {
        ("outer()", 1): (4, 1),
        ("inner()", 3): (2, 1),
        ("method()", 11): (2, 1),
    }


def test_python_complexity_bounds_async_nested_function_body(tmp_path: Path):
    tree = ast.parse(
        """async def async_outer(xs):
    async for x in xs:
        async def async_inner(ys):
            async for y in ys:
                if y and y > 0:
                    break
            return 0
        return await async_inner(xs)
    return 0
"""
    )
    nodes = {
        (node.name, node.lineno): node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    engine = ComplexityEngine(tmp_path)

    outer = nodes[("async_outer", 1)]
    inner = nodes[("async_inner", 3)]
    assert (engine._calc_ast_cc(outer), engine._calc_ast_nesting(outer)) == (2, 1)
    assert (engine._calc_ast_cc(inner), engine._calc_ast_nesting(inner)) == (4, 2)


def test_python_complexity_keeps_definition_expressions_and_comprehensions(
    tmp_path: Path,
):
    tree = ast.parse(
        """@deco(flag if ok else fallback)
def decorated(value=default(value) if ok else fallback):
    if value:
        return value
    return 0

def outer(x):
    if x:
        def inner(y=default(y) if y else 0):
            if y:
                return y
            return 0
        return inner()
    return 0

def comp(xs, ys):
    return [x for x in xs if x and x > 0 for y in ys if y]
"""
    )
    nodes = {
        (node.name, node.lineno): node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    engine = ComplexityEngine(tmp_path)

    assert engine._calc_ast_cc(nodes[("decorated", 2)]) == 4
    assert engine._calc_ast_cc(nodes[("outer", 7)]) == 3
    assert engine._calc_ast_cc(nodes[("inner", 9)]) == 3
    assert engine._calc_ast_cc(nodes[("comp", 16)]) == 4
    assert engine._calc_ast_nesting(nodes[("comp", 16)]) == 0


def test_process_validation_helpers_stay_below_complexity_limit():
    project_root = Path(__file__).resolve().parents[1]
    _, targets = ComplexityEngine(project_root)._analyze_python_complexity(15, 25, 4)

    task4_paths = {
        "src/ici/core/runner.py",
        "src/ici/engines/lint.py",
        "src/ici/engines/test.py",
        "src/ici/engines/type_check.py",
    }
    offenders = [
        (target.file_path, target.target_name, target.metrics["complexity"])
        for target in targets
        if target.file_path in task4_paths and target.metrics.get("complexity", 0) > 25
    ]

    assert offenders == []


def test_coverage_validation_helpers_stay_below_complexity_limit():
    project_root = Path(__file__).resolve().parents[1]
    _, targets = ComplexityEngine(project_root)._analyze_python_complexity(15, 25, 4)

    task5_paths = {
        "src/ici/engines/coverage_support.py",
        "src/ici/engines/test.py",
    }
    offenders = [
        (target.file_path, target.target_name, target.metrics["complexity"])
        for target in targets
        if target.file_path in task5_paths and target.metrics.get("complexity", 0) > 25
    ]

    assert offenders == []


def test_i4_qt_analysis_helpers_stay_below_critical_complexity_limit():
    project_root = Path(__file__).resolve().parents[1]
    _, targets = ComplexityEngine(project_root)._analyze_python_complexity(15, 25, 4)

    i4_paths = {
        "src/ici/core/toolchain.py",
        "src/ici/engines/_clazy.py",
        "src/ici/engines/_cpp_diagnostics.py",
        "src/ici/engines/_cpp_tooling.py",
        "src/ici/engines/_qt_codegen.py",
    }
    offenders = [
        (target.file_path, target.target_name, target.metrics["complexity"])
        for target in targets
        if target.file_path in i4_paths and target.metrics.get("complexity", 0) > 25
    ]

    assert offenders == []


def test_process_runner_has_no_silent_cleanup_exceptions():
    project_root = Path(__file__).resolve().parents[1]
    targets = []
    ExceptionSafetyEngine(project_root)._check_python_exceptions(targets)

    silent_runner_errors = [
        target
        for target in targets
        if target.file_path == "src/ici/core/runner.py" and target.target_name == "ErrorSwallowing"
    ]

    assert silent_runner_errors == []


def test_exception_safety_detects_swallowed_error(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    bad_py = src / "bad_error.py"
    bad_py.write_text(
        """def dangerous():
    try:
        x = 1 / 0
    except Exception:
        pass
""",
        encoding="utf-8",
    )

    engine = ExceptionSafetyEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert any("ErrorSwallowing" in t.target_name for t in res.targets)


def test_exception_safety_detects_bare_except(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir(parents=True)
    bad_py = src / "bare.py"
    bad_py.write_text(
        """def bad_bare():
    try:
        x = 1
    except:
        print("error")
""",
        encoding="utf-8",
    )

    engine = ExceptionSafetyEngine(tmp_path)
    res = engine.run()
    assert res.status == EngineStatus.FAIL
    assert any("BareExcept" in t.target_name for t in res.targets)


def _cpp_targets(tmp_path: Path, source: str) -> dict:
    """Run the C++ path of the complexity engine over one file."""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "sample.cpp").write_text(source, encoding="utf-8")
    result = ComplexityEngine(tmp_path).run()
    return {t.target_name: t for t in result.targets}


def test_cpp_single_line_definition_closes_itself(tmp_path: Path):
    """A body on the signature line must not absorb the functions after it."""
    targets = _cpp_targets(
        tmp_path,
        "void trivial() { return; }\n"
        "\n"
        "bool busy(int a, int b) {\n"
        "    if (a > 0) {\n"
        "        while (b > 0) {\n"
        "            --b;\n"
        "        }\n"
        "    }\n"
        "    return a > b;\n"
        "}\n",
    )
    assert set(targets) == {"trivial()", "busy()"}
    assert targets["trivial()"].start_line == 1
    assert targets["trivial()"].end_line == 1
    assert targets["trivial()"].metrics["complexity"] == 1
    # The loop and branch belong to busy(), not to the one-liner above it.
    assert targets["busy()"].start_line == 3
    assert targets["busy()"].metrics["complexity"] == 3


def test_cpp_same_line_definitions_are_scanned_independently(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "int first() { return 1; } int second(int value) { if (value) { return 2; } return 0; }\n",
    )

    assert set(targets) == {"first()", "second()"}
    assert targets["first()"].metrics["complexity"] == 1
    assert targets["second()"].metrics["complexity"] == 2


def test_cpp_scanner_keeps_remainder_after_namespace_and_enum_braces(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.cpp").write_text(
        "namespace nested { int first() { return 1; } int second() { return 2; } }\n"
        "enum class Flag { One }; int third() { return 3; }\n",
        encoding="utf-8",
    )

    result = ComplexityEngine(tmp_path).run()
    names = [target.target_name for target in result.targets]

    assert names.count("first()") == 1
    assert names.count("second()") == 1
    assert names.count("third()") == 1
    assert len(names) == 3


def test_cpp_scanner_masks_multiline_macro_definition_before_real_function(
    tmp_path: Path,
):
    targets = _cpp_targets(
        tmp_path,
        "#define DECLARE_FUNCTION(name) \\\n"
        "    int name() { \\\n"
        "        if (true) { \\\n"
        "            return 1; \\\n"
        "        } \\\n"
        "    }\n"
        "int real_function() { return 0; }\n",
    )

    assert set(targets) == {"real_function()"}
    assert targets["real_function()"].start_line == 7


def test_cpp_scanner_skips_local_and_uppercase_macro_calls_before_next_function(
    tmp_path: Path,
):
    targets = _cpp_targets(
        tmp_path,
        "#define DECLARE_LOCAL(name) int name() { return 0; }\n"
        "DECLARE_LOCAL(generated)\n"
        "UPPER_GENERATE(generated_again)\n"
        "int real_function(int value) {\n"
        "    if (value) {\n"
        "        return 1;\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
    )

    assert set(targets) == {"real_function()"}
    assert targets["real_function()"].metrics["complexity"] == 2
    assert targets["real_function()"].end_line == 9


def test_cpp_scanner_excludes_nested_lambdas_from_enclosing_metrics(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "int outer(int value) {\n"
        "    auto callback = [value]() {\n"
        "        if (value) {\n"
        "            return value;\n"
        "        }\n"
        "        return 0;\n"
        "    };\n"
        "    if (value > 1) {\n"
        "        return callback();\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
    )

    assert set(targets) == {"outer()"}
    target = targets["outer()"]
    assert target.metrics["complexity"] == 2
    assert target.metrics["nesting"] == 1
    assert target.metrics["excluded_nested_lambdas"] == 1
    assert target.end_line == 12

    result = ComplexityEngine(tmp_path).run()
    assert result.extra["cpp_scope_exclusions"] == {
        "lambda": 1,
        "macro_generated_function": 0,
    }


def test_cpp_scanner_masks_nested_lambdas_in_capture_initializers_and_bodies(
    tmp_path: Path,
):
    targets = _cpp_targets(
        tmp_path,
        "int outer(int value) {\n"
        "    auto callback = [value, captured = [value]() {\n"
        "        if (value) { return 1; }\n"
        "        return 0;\n"
        "    }]() {\n"
        "        auto nested = [value]() {\n"
        "            while (value) { return 2; }\n"
        "            return 0;\n"
        "        };\n"
        "        if (value > 1) {\n"
        "            return nested();\n"
        "        }\n"
        "        return captured();\n"
        "    };\n"
        "    if (value > 2) {\n"
        "        return callback();\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
    )

    target = targets["outer()"]
    assert target.metrics["complexity"] == 2
    assert target.metrics["nesting"] == 1
    assert target.metrics["excluded_nested_lambdas"] == 3

    result = ComplexityEngine(tmp_path).run()
    assert result.extra["cpp_scope_exclusions"] == {
        "lambda": 3,
        "macro_generated_function": 0,
    }


def test_cpp_scanner_excludes_generic_returned_lambda_from_enclosing_metrics(
    tmp_path: Path,
):
    targets = _cpp_targets(
        tmp_path,
        "auto make_callback() {\n"
        "    return []<class T>(T value) requires (sizeof(T) > 0) {\n"
        "        if (value) { return 1; }\n"
        "        return 0;\n"
        "    };\n"
        "}\n",
    )

    target = targets["make_callback()"]
    assert target.metrics["complexity"] == 1
    assert target.metrics["nesting"] == 0
    assert target.metrics["excluded_nested_lambdas"] == 1


def test_cpp_lambda_scope_scanner_does_not_confuse_cpp_bracket_constructs(
    tmp_path: Path,
):
    targets = _cpp_targets(
        tmp_path,
        "[[nodiscard]] int indexed(int* values, int index) {\n"
        "    auto [left, right] = pair();\n"
        "    if (values[index] && left) { return right; }\n"
        "    return 0;\n"
        "}\n",
    )

    target = targets["indexed()"]
    assert target.metrics["complexity"] == 3
    assert target.metrics["excluded_nested_lambdas"] == 0


def test_cpp_scanner_skips_initializers_and_extends_function_try_handlers(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "struct Widget { int value; Widget(int input); };\n"
        "Widget::Widget(int input) try : value{input} {\n"
        "    if (input < 0) { throw input; }\n"
        "} catch (...) {\n"
        "    value = 0;\n"
        "}\n"
        "int measured(int value = {1}) { if (value) { return value; } return 0; }\n",
    )

    assert set(targets) == {"Widget::Widget()", "measured()"}
    assert targets["Widget::Widget()"].end_line == 6
    assert targets["Widget::Widget()"].metrics["complexity"] == 3
    assert targets["measured()"].end_line == 7
    assert targets["measured()"].metrics["complexity"] == 2


def test_cpp_scanner_understands_brace_digraphs(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "int digraph(int value) <% if (value) <% return 1; %> return 0; %>\n",
    )

    assert set(targets) == {"digraph()"}
    assert targets["digraph()"].metrics["complexity"] == 2
    assert targets["digraph()"].metrics["nesting"] == 1


def test_cpp_scanner_skips_trailing_requires_expression(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "template <class T> int constrained(T value) requires requires(T candidate) "
        "{ candidate + 1; } { if (value) { return 1; } return 0; }\n",
    )

    assert set(targets) == {"constrained()"}
    assert targets["constrained()"].metrics["complexity"] == 2
    assert targets["constrained()"].metrics["nesting"] == 1


def test_cpp_scanner_does_not_invent_a_function_for_a_concept_requires_expression(
    tmp_path: Path,
):
    targets = _cpp_targets(
        tmp_path,
        "template <class T>\n"
        "concept HasValue = requires(T value) {\n"
        "    value + 1;\n"
        "};\n"
        "int measured(int value) { if (value) { return 1; } return 0; }\n",
    )

    assert set(targets) == {"measured()"}
    assert targets["measured()"].metrics["complexity"] == 2


def test_cpp_scanner_keeps_operator_prefixed_identifiers_as_functions(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "int operatorHelper() { return 1; }\nint operator_helper() { return 2; }\n",
    )

    assert set(targets) == {"operatorHelper()", "operator_helper()"}
    assert {target.metrics["function_kind"] for target in targets.values()} == {"function"}


@pytest.mark.parametrize("initializer", ["[]", "+[]"])
def test_cpp_scanner_does_not_invent_a_function_for_lambda_initializer(
    tmp_path: Path,
    initializer: str,
):
    targets = _cpp_targets(
        tmp_path,
        f"void (*callback)() = {initializer} {{ return; }};\n"
        "int measured(int value) { if (value) { return 1; } return 0; }\n",
    )

    assert set(targets) == {"measured()"}
    assert targets["measured()"].metrics["complexity"] == 2


def test_cpp_control_flow_is_not_reported_as_a_function(tmp_path: Path):
    """`for (int i = ...)` has parens, a brace and a type, but is not a definition."""
    targets = _cpp_targets(
        tmp_path,
        "int total(int n) {\n"
        "    int sum = 0;\n"
        "    for (int i = 0; i < n; ++i) {\n"
        "        sum += i;\n"
        "    }\n"
        "    return sum;\n"
        "}\n",
    )
    assert set(targets) == {"total()"}
    assert targets["total()"].end_line == 7


def test_cpp_multi_line_signature_is_detected(tmp_path: Path):
    """A signature wrapped across lines used to be invisible to the scanner."""
    targets = _cpp_targets(
        tmp_path,
        "void first() { return; }\n"
        "\n"
        "void wrapped(const int& a,\n"
        "             const int& b) {\n"
        "    if (a && b) {\n"
        "        return;\n"
        "    }\n"
        "}\n",
    )
    assert "wrapped()" in targets
    assert targets["wrapped()"].start_line == 3
    # The if and the && are both decision points.
    assert targets["wrapped()"].metrics["complexity"] == 3


def test_cpp_literals_do_not_create_decision_points(tmp_path: Path):
    """Braces and operators inside strings and comments must not be counted."""
    targets = _cpp_targets(
        tmp_path,
        'const char* text() { return "if (a && b) {"; }  // while (x) {\n',
    )
    assert set(targets) == {"text()"}
    assert targets["text()"].metrics["complexity"] == 1


def test_cpp_token_metric_counts_if_constexpr_and_case_branches(tmp_path: Path):
    targets = _cpp_targets(
        tmp_path,
        "int classify(int value) {\n"
        "    if constexpr (true) {\n"
        "        switch (value) {\n"
        "        case 1: return 1;\n"
        "        case 2: return 2;\n"
        "        default: return 0;\n"
        "        }\n"
        "    }\n"
        "    return -1;\n"
        "}\n",
    )

    # Base path + if constexpr + two explicit case alternatives. The switch
    # dispatch and default label do not add separate McCabe decision points.
    assert targets["classify()"].metrics["complexity"] == 4


def test_cpp_complexity_fails_closed_on_an_oversized_source(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "oversized.cpp").write_bytes(b"int oversized() {}\n" + b" " * (8 * 1024 * 1024))

    result = ComplexityEngine(tmp_path).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["total_functions"] == 0
    assert result.extra["issues_count"] == 1
    assert result.extra["metrics_summary"].endswith("1 issues / 0 funcs)")
    assert result.targets[0].target_name == "CppComplexityAnalysisError"


def test_cpp_nesting_depth_counts_blocks_inside_the_body(tmp_path: Path):
    """Three nested ifs are depth 3 — the function's own braces are not nesting."""
    targets = _cpp_targets(
        tmp_path,
        "void deep(int a) {\n"
        "    if (a) {\n"
        "        if (a) {\n"
        "            if (a) {\n"
        "                return;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}\n",
    )
    assert targets["deep()"].metrics["nesting"] == 3


def test_cpp_and_python_agree_on_nesting_depth(tmp_path: Path):
    """The same shape in either language must produce the same number.

    C++ used to count the function body as a level, so identical code read one
    deeper than Python and the shared warn_nesting threshold was quietly
    stricter for C++.
    """
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "same.cpp").write_text(
        "int threeLevels(int a, int b) {\n"
        "    if (a) {\n"
        "        while (b) {\n"
        "            for (int i = 0; i < 3; ++i) {\n"
        "                --b;\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "    return a;\n"
        "}\n",
        encoding="utf-8",
    )
    (src / "same.py").write_text(
        "def three_levels(a, b):\n"
        "    if a:\n"
        "        while b:\n"
        "            for i in range(3):\n"
        "                b -= 1\n"
        "    return a\n",
        encoding="utf-8",
    )
    by_file = {t.file_path: t for t in ComplexityEngine(tmp_path).run().targets}
    assert by_file["src/same.cpp"].metrics["nesting"] == 3
    assert by_file["src/same.py"].metrics["nesting"] == 3


def test_cpp_function_body_alone_is_zero_nesting(tmp_path: Path):
    targets = _cpp_targets(tmp_path, "int flat(int a) {\n    return a + 1;\n}\n")
    assert targets["flat()"].metrics["nesting"] == 0
