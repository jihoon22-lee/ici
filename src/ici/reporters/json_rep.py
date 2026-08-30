"""JSON result serializers and v2-to-v3 migration for CI/CD pipelines."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ici.core.findings import findings_for_result
from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingMetric,
    FindingSuppression,
    InspectionTarget,
    SourceLocation,
    ToolEvidence,
    VerificationSuiteResult,
)
from ici.core.redaction import redact_engine_result, redact_suite

RESULT_SCHEMA_VERSION = "ici.result/v3"
LEGACY_RESULT_SCHEMA_VERSION = "ici.result/v2"


def _serialize_target(target: InspectionTarget) -> dict[str, Any]:
    """Serialize every legacy location field for compatibility consumers."""
    return {
        "file_path": target.file_path,
        "start_line": target.start_line,
        "end_line": target.end_line,
        "start_column": target.start_column,
        "end_column": target.end_column,
        "target_name": target.target_name,
        "status": target.status.value,
        "message": target.message,
        "snippet": target.snippet,
        "metrics": target.metrics,
    }


def _serialize_location(location: SourceLocation) -> dict[str, Any]:
    return {
        "path": location.path,
        "start_line": location.start_line,
        "end_line": location.end_line,
        "start_column": location.start_column,
        "end_column": location.end_column,
        "label": location.label,
    }


def _serialize_metric(metric: FindingMetric) -> dict[str, Any]:
    return {"value": metric.value, "unit": metric.unit}


def _serialize_suppression(suppression: FindingSuppression) -> dict[str, Any]:
    return {
        "suppressed": suppression.suppressed,
        "kind": suppression.kind.value,
        "reason": suppression.reason,
    }


def _serialize_finding(finding: Finding) -> dict[str, Any]:
    return {
        "rule_id": finding.rule_id,
        "category": finding.category.value,
        "severity": finding.severity.value,
        "confidence": finding.confidence.value,
        "fingerprint": finding.fingerprint,
        "primary_location": _serialize_location(finding.primary_location),
        "related_locations": [
            _serialize_location(location) for location in finding.related_locations
        ],
        "message": finding.message,
        "explanation": finding.explanation,
        "remediation": finding.remediation,
        "tool_rule_id": finding.tool_rule_id,
        "tool_name": finding.tool_name,
        "tool_version": finding.tool_version,
        "suppression": _serialize_suppression(finding.suppression),
        "metrics": {
            name: _serialize_metric(metric) for name, metric in sorted(finding.metrics.items())
        },
        "snippet": finding.snippet,
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


def serialize_engine_result(
    result: EngineResult, project_root: str | Path | None = None
) -> dict[str, Any]:
    """Return the canonical v3 representation of one sanitized engine result."""
    safe = redact_engine_result(result)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "engine_name": safe.engine_name,
        "status": safe.status.value,
        "summary": safe.summary,
        "score": safe.score,
        "max_score": safe.max_score,
        "duration": safe.duration,
        "raw_output": safe.raw_output,
        "extra": safe.extra,
        "required": safe.required,
        "evidence": safe.evidence.value,
        "tool_evidence": [_serialize_tool_evidence(item) for item in safe.tool_evidence],
        # targets remains intact through the v3 transition. Existing consumers
        # can ignore findings and continue to render the v2 shape.
        "targets": [_serialize_target(target) for target in safe.targets],
        "findings": [
            _serialize_finding(finding)
            for finding in findings_for_result(safe, project_root=project_root)
        ],
    }


def serialize_suite_result(
    suite: VerificationSuiteResult, project_root: str | Path | None = None
) -> dict[str, Any]:
    """Return the canonical v3 representation of a sanitized verification suite."""
    safe = redact_suite(suite)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "suite_status": safe.suite_status.value,
        "duration": safe.duration,
        "passed_count": safe.passed_count,
        "warned_count": safe.warned_count,
        # failed_count intentionally retains its historical FAIL+ERROR meaning.
        "failed_count": safe.failed_count,
        "error_count": safe.error_count,
        "skipped_count": safe.skipped_count,
        "total_count": safe.total_count,
        "tem_score": safe.tem_score,
        "max_tem_score": safe.max_tem_score,
        "results": [
            serialize_engine_result(result, project_root=project_root) for result in safe.results
        ],
    }


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_from_payload(payload: dict[str, Any]) -> InspectionTarget:
    try:
        status = EngineStatus(str(payload.get("status", "PASS")))
    except ValueError:
        status = EngineStatus.WARN
    metrics = payload.get("metrics", {})
    return InspectionTarget(
        file_path=str(payload.get("file_path", "unknown")),
        start_line=max(1, int(payload.get("start_line", 1) or 1)),
        end_line=_optional_int(payload.get("end_line")),
        target_name=str(payload.get("target_name", "")),
        status=status,
        message=str(payload.get("message", "")),
        snippet=str(payload.get("snippet", "")),
        metrics=metrics if isinstance(metrics, dict) else {},
        start_column=_optional_int(payload.get("start_column")),
        end_column=_optional_int(payload.get("end_column")),
    )


def _tool_from_payload(payload: dict[str, Any]) -> ToolEvidence:
    argv = payload.get("argv", [])
    return ToolEvidence(
        name=str(payload.get("name", "")),
        path=str(payload.get("path", "")),
        version=str(payload.get("version", "")),
        argv=[str(item) for item in argv] if isinstance(argv, list) else [],
        returncode=_optional_int(payload.get("returncode")),
        timed_out=bool(payload.get("timed_out", False)),
        truncated=bool(payload.get("truncated", False)),
        error=str(payload.get("error", "")),
    )


def _engine_from_v2(payload: dict[str, Any]) -> EngineResult:
    try:
        status = EngineStatus(str(payload.get("status", "ERROR")))
    except ValueError:
        status = EngineStatus.ERROR
    try:
        evidence = EvidenceState(str(payload.get("evidence", "NOT_RUN")))
    except ValueError:
        evidence = EvidenceState.NOT_RUN
    targets = payload.get("targets", [])
    tools = payload.get("tool_evidence", [])
    return EngineResult(
        engine_name=str(payload.get("engine_name", "unknown")),
        status=status,
        summary=str(payload.get("summary", "")),
        score=payload.get("score") if isinstance(payload.get("score"), (int, float)) else None,
        max_score=(
            payload.get("max_score") if isinstance(payload.get("max_score"), (int, float)) else None
        ),
        duration=float(payload.get("duration", 0.0) or 0.0),
        targets=[_target_from_payload(item) for item in targets if isinstance(item, dict)],
        raw_output=str(payload.get("raw_output", "")),
        extra=payload.get("extra", {}) if isinstance(payload.get("extra"), dict) else {},
        required=bool(payload.get("required", True)),
        evidence=evidence,
        tool_evidence=[_tool_from_payload(item) for item in tools if isinstance(item, dict)],
    )


def migrate_report_payload(
    payload: dict[str, Any], project_root: str | Path | None = None
) -> dict[str, Any]:
    """Return a redacted v3 copy of an engine or suite v2/v3 payload.

    Migration deliberately preserves all unknown top-level and engine fields.
    That makes the helper safe for CI archives containing producer extensions.
    """
    version = payload.get("schema_version")
    if version not in (LEGACY_RESULT_SCHEMA_VERSION, RESULT_SCHEMA_VERSION):
        raise ValueError(f"unsupported schema_version: {version!r}")

    migrated = copy.deepcopy(payload)
    engines = migrated.get("results")
    if isinstance(engines, list):
        candidates = [item for item in engines if isinstance(item, dict)]
    else:
        candidates = [migrated]

    for engine_payload in candidates:
        engine_payload["schema_version"] = RESULT_SCHEMA_VERSION
        if version == LEGACY_RESULT_SCHEMA_VERSION or not isinstance(
            engine_payload.get("findings"), list
        ):
            engine = _engine_from_v2(engine_payload)
            safe = redact_engine_result(engine)
            engine_payload["targets"] = [_serialize_target(item) for item in safe.targets]
            engine_payload["tool_evidence"] = [
                _serialize_tool_evidence(item) for item in safe.tool_evidence
            ]
            engine_payload["summary"] = safe.summary
            engine_payload["raw_output"] = safe.raw_output
            engine_payload["extra"] = safe.extra
            engine_payload["findings"] = [
                _serialize_finding(finding)
                for finding in findings_for_result(safe, project_root=project_root)
            ]

    migrated["schema_version"] = RESULT_SCHEMA_VERSION
    # Running an existing v3 archive through migration is also a supported
    # output boundary, so recursively mask producer-specific string fields.
    from ici.core.redaction import redact_data

    return redact_data(migrated)


def save_json_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_root: str | Path | None = None,
) -> None:
    """Serialize a verification suite to a canonical JSON v3 report."""
    _save_json(serialize_suite_result(suite, project_root=project_root), output_path)


def save_engine_json_report(
    result: EngineResult,
    output_path: Path,
    project_root: str | Path | None = None,
) -> None:
    """Serialize one standalone engine result using the same v3 contract."""
    _save_json(serialize_engine_result(result, project_root=project_root), output_path)


def _save_json(data: dict[str, Any], output_path: Path) -> None:
    content = json.dumps(
        data,
        indent=2,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        default=str,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
