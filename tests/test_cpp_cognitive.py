"""C++ cognitive-complexity metric and engine contracts."""

from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines._cpp_cognitive import _source_slice, analyze_cpp_cognitive, cpp_cognitive_metric
from ici.engines._cpp_function_boundaries import (
    CppFunctionBoundary,
    CppFunctionBoundaryOutcome,
)
from ici.engines.cognitive import CognitiveEngine


def test_metric_weights_braced_control_flow_and_logical_sequences() -> None:
    metric = cpp_cognitive_metric(
        """{
if (ready && valid) {
    for (;;) {
        while (retry || forced) {
            break;
        }
    }
}
}"""
    )

    assert metric.cognitive == 9
    assert metric.max_nesting == 3
    assert metric.logical_sequences == 2
    assert metric.unbraced_controls == 0


def test_metric_excludes_literals_comments_lambdas_and_directives() -> None:
    metric = cpp_cognitive_metric(
        r"""{
const char* text = "if (x) { while (y) {} }";
// if (commented) { for (;;) {} }
auto nested = [value]() { if (value) { return 1; } return 0; };
#if FEATURE
if (actual) { return 1; }
#endif
return 0;
}"""
    )

    assert metric.cognitive == 1
    assert metric.max_nesting == 1
    assert metric.excluded_lambdas == 1
    assert metric.preprocessor_conditional is True


def test_metric_marks_unbraced_control_as_lower_confidence() -> None:
    metric = cpp_cognitive_metric("{ if (ready) return 1; return 0; }")

    assert metric.cognitive == 1
    assert metric.unbraced_controls == 1
    assert metric.max_nesting == 0


def test_do_while_is_counted_as_one_loop() -> None:
    metric = cpp_cognitive_metric("{ do { work(); } while (retry && ready); }")

    assert metric.cognitive == 2
    assert metric.max_nesting == 1
    assert metric.logical_sequences == 1


def test_unbraced_nested_controls_preserve_cognitive_nesting() -> None:
    metric = cpp_cognitive_metric("{ if (ready) while (retry) work(); }")

    assert metric.cognitive == 3
    assert metric.unbraced_controls == 2


def test_unbraced_do_while_is_counted_as_one_loop() -> None:
    metric = cpp_cognitive_metric("{ do work(); while (retry); }")

    assert metric.cognitive == 1
    assert metric.unbraced_controls == 1


def test_initializer_braces_are_not_control_bodies() -> None:
    metric = cpp_cognitive_metric("{ if (ready) Widget value{1}; return 0; }")

    assert metric.cognitive == 1
    assert metric.max_nesting == 0
    assert metric.unbraced_controls == 1


def test_initializer_in_condition_does_not_hide_nested_control() -> None:
    metric = cpp_cognitive_metric("{ if (Widget{1}) { if (ready) { return 1; } } return 0; }")

    assert metric.cognitive == 3
    assert metric.max_nesting == 2


def test_metric_supports_cpp_brace_digraphs() -> None:
    metric = cpp_cognitive_metric("<% if (ready) <% return 1; %> return 0; %>")

    assert metric.cognitive == 1
    assert metric.max_nesting == 1
    assert metric.unbraced_controls == 0


def test_alternative_logical_tokens_match_symbolic_operators() -> None:
    symbolic = cpp_cognitive_metric("{ if (ready && valid) return 1; }")
    alternative = cpp_cognitive_metric("{ if (ready and valid) return 1; }")

    assert alternative.cognitive == symbolic.cognitive
    assert alternative.logical_sequences == symbolic.logical_sequences


@pytest.mark.parametrize(
    "condition",
    ["constexpr (ready)", "consteval", "! consteval"],
)
def test_cpp_if_variants_preserve_control_nesting(condition: str) -> None:
    metric = cpp_cognitive_metric(
        f"{{ if {condition} {{ while (ready) work(); }} else {{ work(); }} }}"
    )

    assert metric.cognitive == 4
    assert metric.max_nesting == 1
    assert metric.unbraced_controls == 1


def test_try_catch_and_labelled_controls_are_not_swallowed() -> None:
    metric = cpp_cognitive_metric(
        "{ try { work(); } catch (const Error&) { retry: if (ready) work(); } "
        "catch (...) { switch (code) { case bad: while (retry) work(); default: break; } } }"
    )

    assert metric.cognitive == 10
    assert metric.max_nesting == 2
    assert metric.unbraced_controls == 2


def test_control_attributes_preserve_the_following_statement_shape() -> None:
    metric = cpp_cognitive_metric(
        "{ if (ready) [[likely]] { if (nested) [[unlikely]] { work(); } } }"
    )

    assert metric.cognitive == 3
    assert metric.max_nesting == 2
    assert metric.unbraced_controls == 0


def test_statement_attribute_before_control_preserves_statement_shape() -> None:
    metric = cpp_cognitive_metric(
        "{ [[likely]] if (ready) { [[unlikely]] while (retry) work(); } }"
    )

    assert metric.cognitive == 3
    assert metric.max_nesting == 1
    assert metric.unbraced_controls == 1


def test_digraph_lambda_body_is_excluded_from_enclosing_metric() -> None:
    metric = cpp_cognitive_metric(
        "<% auto nested = [](int value) <% if (value && ready) return 1; %>; "
        "if (actual) return 2; %>"
    )

    assert metric.cognitive == 1
    assert metric.logical_sequences == 0
    assert metric.excluded_lambdas == 1


def test_function_try_block_is_a_valid_function_region(tmp_path: Path) -> None:
    source = tmp_path / "src" / "guarded.cpp"
    source.parent.mkdir()
    source.write_text(
        "int guarded(bool ready) try {\n"
        "    if (ready) return 1;\n"
        "    return 0;\n"
        "} catch (...) {\n"
        "    return -1;\n"
        "}\n",
        encoding="utf-8",
    )

    result = CognitiveEngine(
        tmp_path,
        {
            "project": {"type": "cpp", "source_dirs": ["src"]},
            "engines": {"cognitive": {"cpp_boundaries": "off"}},
        },
    ).run()

    assert result.status != EngineStatus.ERROR
    assert result.extra["cpp_functions"] == 1
    assert len(result.targets) == 1
    assert result.targets[0].file_path == "src/guarded.cpp"


def test_long_else_if_chain_fails_with_a_bounded_error() -> None:
    body = "{ " + "if (ready) work(); else " * 1000 + "work(); }"

    with pytest.raises(ValueError, match="else-if chain exceeds"):
        cpp_cognitive_metric(body)


@pytest.mark.parametrize(
    "body",
    [
        "{ if (ready) return 1 }",
        "{ work() }",
        "{ do { work(); } while (ready) }",
        "{ case value: return 1; }",
        "{ break; }",
        "{ continue; }",
        "{ if (values[0) { work(); } }",
        "{ if (Widget{1) { work(); } }",
    ],
)
def test_malformed_cpp_statements_and_delimiters_fail_closed(body: str) -> None:
    with pytest.raises(ValueError):
        cpp_cognitive_metric(body)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("{ if ! (ready) work(); }", "if ! must be followed by consteval"),
        ("{ if consteval work(); }", "requires a compound statement"),
        ("{ catch (...) { work(); } }", "missing its matching try"),
        ("{ try { work(); } }", "missing a catch handler"),
        ("{ else work(); }", "missing its matching if"),
    ],
)
def test_malformed_cpp_control_flow_fails_closed(body: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        cpp_cognitive_metric(body)


def test_cpp_control_nesting_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ici.engines._cpp_cognitive._MAX_CONTROL_NESTING", 4)

    with pytest.raises(ValueError, match="control nesting exceeds"):
        cpp_cognitive_metric("{ " + "if (ready) " * 6 + "work(); }")
    with pytest.raises(ValueError, match="control nesting exceeds"):
        cpp_cognitive_metric("{" * 6 + "work();" + "}" * 6)


@pytest.mark.parametrize(
    "body",
    [
        "{ for (int i = first && second; i < limit && ready; ++i) {} }",
        "{ consume(first && second, ready && valid); }",
    ],
)
def test_logical_sequences_reset_at_expression_boundaries(body: str) -> None:
    metric = cpp_cognitive_metric(body)

    assert metric.logical_sequences == 2


def test_source_slice_rejects_columns_outside_the_source_line() -> None:
    with pytest.raises(ValueError, match="outside its source"):
        _source_slice("abc\n", 1, 10, 1, 10)

    with pytest.raises(ValueError, match="outside its source"):
        _source_slice("abc\n", 1, 4, 1, 4)


def test_exact_cpp_boundary_path_uses_compiler_geometry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "metrics.cpp"
    source.parent.mkdir()
    source.write_text(
        "int measured(bool ready) {\n"
        "    if (ready) {\n"
        "        return 1;\n"
        "    }\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    boundary = CppFunctionBoundary(
        file_path="src/metrics.cpp",
        start_line=1,
        end_line=6,
        start_column=5,
        end_column=1,
        body_start_line=1,
        body_start_column=26,
        name="measured",
        configurations=("sha256:test",),
    )

    def fake_boundaries(*args, **kwargs) -> CppFunctionBoundaryOutcome:
        return CppFunctionBoundaryOutcome(
            boundaries=[boundary],
            mode="exact",
            configurations_checked=1,
            sources_checked=1,
        )

    monkeypatch.setattr(
        "ici.engines._cpp_cognitive.run_cpp_function_boundaries",
        fake_boundaries,
    )
    outcome = analyze_cpp_cognitive(
        tmp_path,
        [source],
        [source],
        None,
        warn=30,
        fail=60,
        warn_nesting=4,
        boundary_policy="auto",
        runner=lambda *args, **kwargs: None,
    )

    assert outcome.errors == []
    assert outcome.boundary_mode == "exact"
    assert outcome.exact_boundaries == 1
    assert outcome.estimated_boundaries == 0
    assert len(outcome.targets) == 1
    assert outcome.targets[0].metrics["boundary_source"] == "clang-tidy-ast"
    assert outcome.targets[0].metrics["cognitive"] == 1
    assert outcome.targets[0].metrics["nesting"] == 1
    assert outcome.targets[0].start_column == 5
    assert outcome.targets[0].end_column == 1


def test_cpp_source_intake_error_is_located_at_the_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "broken.cpp"
    source.parent.mkdir()
    source.write_bytes(b"int broken() {\xff }\n")

    result = CognitiveEngine(
        tmp_path,
        {
            "project": {"type": "cpp", "source_dirs": ["src"]},
            "engines": {"cognitive": {"cpp_boundaries": "off"}},
        },
    ).run()

    assert result.status == EngineStatus.ERROR
    error_target = next(target for target in result.targets if target.file_path == "src/broken.cpp")
    assert error_target.target_name == "CppCognitiveSourceError"
    assert error_target.file_path == "src/broken.cpp"
    assert error_target.start_line == 1


def test_cpp_geometry_error_is_located_at_the_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "metrics.cpp"
    source.parent.mkdir()
    source.write_text("int measured() { return 1; }\n", encoding="utf-8")
    boundary = CppFunctionBoundary(
        file_path="src/metrics.cpp",
        start_line=1,
        end_line=1,
        start_column=5,
        end_column=999,
        body_start_line=1,
        body_start_column=16,
        name="measured",
    )

    def fake_boundaries(*args, **kwargs) -> CppFunctionBoundaryOutcome:
        return CppFunctionBoundaryOutcome(boundaries=[boundary], mode="exact")

    monkeypatch.setattr(
        "ici.engines._cpp_cognitive.run_cpp_function_boundaries",
        fake_boundaries,
    )
    result = CognitiveEngine(
        tmp_path,
        {
            "project": {"type": "cpp", "source_dirs": ["src"]},
            "engines": {"cognitive": {"cpp_boundaries": "auto"}},
        },
    ).run()

    assert result.status == EngineStatus.ERROR
    error_target = next(
        target for target in result.targets if target.file_path == "src/metrics.cpp"
    )
    assert error_target.status == EngineStatus.ERROR
    assert error_target.file_path == "src/metrics.cpp"
    assert error_target.start_line == 1


def test_missing_exact_end_column_is_error_without_fallback_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "metrics.cpp"
    source.parent.mkdir()
    source.write_text("int measured() { return 1; }\n", encoding="utf-8")
    boundary = CppFunctionBoundary(
        file_path="src/metrics.cpp",
        start_line=1,
        end_line=1,
        start_column=5,
        end_column=None,
        body_start_line=1,
        body_start_column=16,
        name="measured",
    )

    monkeypatch.setattr(
        "ici.engines._cpp_cognitive.run_cpp_function_boundaries",
        lambda *args, **kwargs: CppFunctionBoundaryOutcome(boundaries=[boundary], mode="exact"),
    )
    result = CognitiveEngine(
        tmp_path,
        {
            "project": {"type": "cpp", "source_dirs": ["src"]},
            "engines": {"cognitive": {"cpp_boundaries": "auto"}},
        },
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.extra["cpp_functions"] == 0
    assert len(result.targets) == 1
    assert result.targets[0].status == EngineStatus.ERROR
    assert "no end column" in result.targets[0].message


def test_unterminated_cpp_function_does_not_silently_pass(tmp_path: Path) -> None:
    source = tmp_path / "src" / "broken.cpp"
    source.parent.mkdir()
    source.write_text(
        "int broken(bool ready) {\n    if (ready) {\n        return 1;\n    }\n",
        encoding="utf-8",
    )

    result = CognitiveEngine(
        tmp_path,
        {
            "project": {"type": "cpp", "source_dirs": ["src"]},
            "engines": {"cognitive": {"cpp_boundaries": "off"}},
        },
    ).run()

    assert result.status == EngineStatus.ERROR
    assert any(target.file_path == "src/broken.cpp" for target in result.targets)


def test_cpp_cognitive_cache_tracks_fallback_scanner_dependency() -> None:
    assert "ici.engines.complexity" in CognitiveEngine.CACHE_IMPLEMENTATION_MODULES


def test_cpp_engine_reports_every_function_with_estimated_evidence(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "metrics.cpp").write_text(
        """int simple() { return 1; }
int nested(bool ready) {
    if (ready) {
        while (ready) {
            return 2;
        }
    }
    return 0;
}
""",
        encoding="utf-8",
    )
    config = {
        "project": {"type": "cpp", "source_dirs": ["src"]},
        "engines": {
            "cognitive": {
                "mode": "pass_warn_fail",
                "warn": 3,
                "fail": 20,
                "warn_nesting": 9,
                "cpp_boundaries": "off",
            }
        },
    }

    result = CognitiveEngine(tmp_path, config).run()

    assert result.status == EngineStatus.WARN
    assert result.evidence == EvidenceState.ESTIMATED
    assert result.extra["cpp_functions"] == 2
    assert result.extra["cpp_boundary_mode"] == "heuristic"
    assert [target.status for target in result.targets] == [
        EngineStatus.PASS,
        EngineStatus.WARN,
    ]
    assert all(target.file_path == "src/metrics.cpp" for target in result.targets)
    assert result.targets[1].metrics["boundary_source"] == "source-scanner"


def test_required_cpp_boundaries_fail_closed_without_context(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "metrics.cpp").write_text("int measured() { return 1; }\n", encoding="utf-8")
    config = {
        "project": {"type": "cpp", "source_dirs": ["src"]},
        "engines": {"cognitive": {"cpp_boundaries": "required"}},
    }

    result = CognitiveEngine(tmp_path, config).run()

    assert result.status == EngineStatus.ERROR
    assert result.evidence == EvidenceState.NOT_RUN
    assert result.extra["cpp_boundary_mode"] == "error"
    assert result.targets[-1].target_name == "CppCognitiveAnalysisError"


def test_cpp_source_inventory_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "metrics.cpp").write_text("int measured() { return 1; }\n", encoding="utf-8")
    monkeypatch.setattr("ici.engines._cpp_cognitive._MAX_SOURCE_BYTES", 1)

    result = CognitiveEngine(
        tmp_path,
        {
            "project": {"type": "cpp", "source_dirs": ["src"]},
            "engines": {"cognitive": {"cpp_boundaries": "off"}},
        },
    ).run()

    assert result.status == EngineStatus.ERROR
    assert result.extra["cpp_boundary_errors"] == [
        "C++ cognitive source inventory exceeds the bounded limit"
    ]
