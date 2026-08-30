from __future__ import annotations

import copy
import json
from pathlib import Path

from ici.config import DEFAULT_CONFIG
from ici.core.models import (
    AnalysisMode,
    EngineResult,
    EngineStatus,
    EvidenceState,
    FindingConfidence,
    SupportLanguage,
    VerificationSuiteResult,
)
from ici.core.support import (
    ENGINE_NAMES,
    evaluate_support_matrix,
    render_support_markdown,
    support_declarations,
)
from ici.reporters.json_rep import serialize_suite_result


def _config(*source_dirs: str) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["project"]["source_dirs"] = list(source_dirs)
    return config


def _entry(matrix, engine: str, language: SupportLanguage):
    return next(
        item for item in matrix.entries if item.engine_name == engine and item.language == language
    )


def test_registry_declares_every_engine_language_pair_once():
    declarations = support_declarations()

    assert len(declarations) == len(ENGINE_NAMES) * 2
    assert len({(item.engine_name, item.language) for item in declarations}) == len(declarations)
    assert {item.engine_name for item in declarations} == set(ENGINE_NAMES)
    for engine_name in ENGINE_NAMES:
        assert [item.language for item in declarations if item.engine_name == engine_name] == [
            SupportLanguage.PYTHON,
            SupportLanguage.CPP,
        ]


def test_matrix_discovers_hybrid_qt_scope_and_does_not_claim_unrun_evidence(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    config = _config("src")
    config["project"]["cpp_pkg_config"] = ["Qt6Widgets", "Qt5Widgets"]

    matrix = evaluate_support_matrix(tmp_path, config)

    assert matrix.project_languages == [SupportLanguage.CPP, SupportLanguage.PYTHON]
    assert matrix.project_frameworks == ["qt"]
    py_lint = _entry(matrix, "lint", SupportLanguage.PYTHON)
    assert py_lint.applicable is True
    assert py_lint.enabled is True
    assert py_lint.evidence == EvidenceState.NOT_RUN
    assert py_lint.active_mode is None
    assert py_lint.confidence == FindingConfidence.LOW

    cpp_type = _entry(matrix, "type", SupportLanguage.CPP)
    assert cpp_type.applicable is False
    assert cpp_type.evidence == EvidenceState.NOT_APPLICABLE
    assert cpp_type.mode == AnalysisMode.UNSUPPORTED

    py_cognitive = _entry(matrix, "cognitive", SupportLanguage.PYTHON)
    assert py_cognitive.applicable is True
    assert py_cognitive.enabled is False
    assert py_cognitive.evidence == EvidenceState.NOT_RUN
    assert "disabled" in py_cognitive.reason


def test_observed_evidence_selects_declared_or_fallback_mode(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = _config("src")
    result = EngineResult(
        engine_name="lint",
        status=EngineStatus.WARN,
        summary="Ruff unavailable",
        evidence=EvidenceState.ESTIMATED,
    )

    estimated = evaluate_support_matrix(tmp_path, config, [result])
    py_lint = _entry(estimated, "lint", SupportLanguage.PYTHON)
    assert py_lint.active_mode == AnalysisMode.HEURISTIC
    assert py_lint.confidence == FindingConfidence.MEDIUM
    assert py_lint.evidence == EvidenceState.ESTIMATED

    result.evidence = EvidenceState.MEASURED
    measured = evaluate_support_matrix(tmp_path, config, [result])
    py_lint = _entry(measured, "lint", SupportLanguage.PYTHON)
    assert py_lint.active_mode == AnalysisMode.TOOL_BACKED
    assert py_lint.confidence == FindingConfidence.HIGH


def test_effective_policy_promotes_optional_tools_to_required(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = _config("src")
    config["engines"]["lint"]["ruff_required"] = True
    config["engines"]["type"]["mypy_required"] = True
    config["engines"]["test"]["coverage_required"] = True

    matrix = evaluate_support_matrix(tmp_path, config)

    assert "ruff" in _entry(matrix, "lint", SupportLanguage.PYTHON).required_tools
    assert "ruff" not in _entry(matrix, "lint", SupportLanguage.PYTHON).optional_tools
    assert "mypy" in _entry(matrix, "type", SupportLanguage.PYTHON).required_tools
    assert "coverage" in _entry(matrix, "test", SupportLanguage.PYTHON).required_tools


def test_absent_language_is_not_applicable_even_when_engine_is_disabled(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
    config = _config("src")

    matrix = evaluate_support_matrix(tmp_path, config)

    py_cognitive = _entry(matrix, "cognitive", SupportLanguage.PYTHON)
    assert py_cognitive.enabled is False
    assert py_cognitive.applicable is False
    assert py_cognitive.evidence == EvidenceState.NOT_APPLICABLE
    assert "no discovered python" in py_cognitive.reason


def test_declared_empty_scope_and_cpp_headers_are_discovered(tmp_path: Path):
    source = tmp_path / "include"
    source.mkdir()
    (source / "widget.hpp").write_text("class Widget {};\n", encoding="utf-8")
    config = _config("include")
    config["type"] = "hybrid"

    matrix = evaluate_support_matrix(tmp_path, config)

    assert matrix.project_languages == [SupportLanguage.CPP, SupportLanguage.PYTHON]


def test_markdown_table_is_generated_in_registry_order():
    table = render_support_markdown()

    assert table.startswith("| Engine | Python | C++ / Qt |")
    rows = table.splitlines()[2:]
    assert [row.split("`")[1] for row in rows] == list(ENGINE_NAMES)
    assert "| `type` | tool-backed → heuristic fallback | unsupported |" in table
    assert "| `sanitize` | tool-backed | tool-backed (Qt) |" in table


def test_v3_serializer_and_schema_share_the_complete_matrix_contract(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    config = _config("src")
    result = EngineResult("line", EngineStatus.PASS, "ok")
    matrix = evaluate_support_matrix(tmp_path, config, [result])
    matrix.entries[0].reason = "api_key=matrixsecret"
    suite = VerificationSuiteResult(EngineStatus.PASS, [result], support_matrix=matrix)

    payload = serialize_suite_result(suite, project_root=tmp_path)
    assert "matrixsecret" not in json.dumps(payload)
    serialized = payload["support_matrix"]
    assert serialized["project_languages"] == ["python"]
    assert len(serialized["entries"]) == len(ENGINE_NAMES) * 2
    line_python = next(
        item
        for item in serialized["entries"]
        if item["engine_name"] == "line" and item["language"] == "python"
    )
    assert line_python["mode"] == "exact"
    assert line_python["active_mode"] == "exact"
    assert line_python["evidence"] == "MEASURED"

    schema_path = (
        Path(__file__).parents[1] / "src" / "ici" / "schemas" / "ici-result-v3.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$defs"]["supportEntry"]["additionalProperties"] is False
    assert schema["$defs"]["suite"]["properties"]["support_matrix"] == {
        "$ref": "#/$defs/nullableSupportMatrix"
    }
