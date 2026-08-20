"""JSON result serializers for CI/CD pipelines."""

import json
from pathlib import Path
from typing import Any

from ici.core.models import EngineResult, InspectionTarget, ToolEvidence, VerificationSuiteResult

RESULT_SCHEMA_VERSION = "ici.result/v2"


def _serialize_target(target: InspectionTarget) -> dict[str, Any]:
    """Serialize every location field so consumers can reproduce the finding."""
    return {
        "file_path": target.file_path,
        "start_line": target.start_line,
        "end_line": target.end_line,
        "target_name": target.target_name,
        "status": target.status.value,
        "message": target.message,
        "snippet": target.snippet,
        "metrics": target.metrics,
    }


def _serialize_tool_evidence(tool: ToolEvidence) -> dict[str, Any]:
    """Serialize the complete external-tool execution evidence contract."""
    return {
        "name": tool.name,
        "path": tool.path,
        "version": tool.version,
        "argv": list(tool.argv),
        "returncode": tool.returncode,
        "timed_out": tool.timed_out,
        "truncated": tool.truncated,
        "error": tool.error,
    }


def serialize_engine_result(result: EngineResult) -> dict[str, Any]:
    """Return the canonical v2 representation of one engine result."""
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "engine_name": result.engine_name,
        "status": result.status.value,
        "summary": result.summary,
        "score": result.score,
        "max_score": result.max_score,
        "duration": result.duration,
        "raw_output": result.raw_output,
        "extra": result.extra,
        "required": result.required,
        "evidence": result.evidence.value,
        "tool_evidence": [_serialize_tool_evidence(item) for item in result.tool_evidence],
        "targets": [_serialize_target(target) for target in result.targets],
    }


def serialize_suite_result(suite: VerificationSuiteResult) -> dict[str, Any]:
    """Return the canonical v2 representation of a verification suite."""
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "suite_status": suite.suite_status.value,
        "duration": suite.duration,
        "passed_count": suite.passed_count,
        "warned_count": suite.warned_count,
        # failed_count intentionally retains its historical FAIL+ERROR meaning.
        "failed_count": suite.failed_count,
        "error_count": suite.error_count,
        "skipped_count": suite.skipped_count,
        "total_count": suite.total_count,
        "tem_score": suite.tem_score,
        "max_tem_score": suite.max_tem_score,
        "results": [serialize_engine_result(result) for result in suite.results],
    }


def save_json_report(suite: VerificationSuiteResult, output_path: Path) -> None:
    """Serialize a verification suite to a canonical JSON v2 report."""
    _save_json(serialize_suite_result(suite), output_path)


def save_engine_json_report(result: EngineResult, output_path: Path) -> None:
    """Serialize one standalone engine result using the same v2 contract."""
    _save_json(serialize_engine_result(result), output_path)


def _save_json(data: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False, default=str)
