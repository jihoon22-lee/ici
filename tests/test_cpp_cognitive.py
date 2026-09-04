"""C++ cognitive-complexity metric and engine contracts."""

from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines._cpp_cognitive import cpp_cognitive_metric
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
