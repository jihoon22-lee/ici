"""JSON result serializers and v2-to-v3 migration for CI/CD pipelines."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from ici.core.capabilities import serialize_capability_inventory
from ici.core.context import AnalysisContext, ArtifactManifest
from ici.core.findings import findings_for_result, validate_source_region
from ici.core.models import (
    AnalysisMetadata,
    BaselineComparison,
    DeltaState,
    EngineResult,
    EngineStatus,
    EngineSupport,
    EvidenceState,
    Finding,
    FindingDelta,
    FindingMetric,
    FindingSeverity,
    FindingSuppression,
    InspectionTarget,
    SourceLocation,
    SupportMatrix,
    ToolEvidence,
    VerificationSuiteResult,
)
from ici.core.redaction import redact_engine_result, redact_suite

RESULT_SCHEMA_VERSION = "ici.result/v3"
LEGACY_RESULT_SCHEMA_VERSION = "ici.result/v2"
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _serialize_artifact_manifest(manifest: ArtifactManifest) -> dict[str, Any]:
    shadow_root = None
    if manifest.shadow_root is not None:
        try:
            shadow_root = manifest.shadow_root.relative_to(manifest.project_root).as_posix()
        except ValueError as err:
            raise ValueError("artifact shadow root must be project-relative") from err
    return {
        "schema_version": "ici.artifacts/v1",
        "project_root": ".",
        "shadow_root": shadow_root,
        "variant": manifest.variant.value,
        "source_commit": manifest.source_commit,
        "config_digest": _require_digest(manifest.config_digest, "manifest.config_digest"),
        "toolchain_digest": _require_digest(
            manifest.toolchain_digest,
            "manifest.toolchain_digest",
        ),
        "artifacts": [
            {
                "path": artifact.path,
                "scope": artifact.scope.value,
                "kind": artifact.kind,
                "sha256": _require_digest(artifact.sha256, "artifact.sha256"),
                "size": artifact.size,
                "mode": artifact.mode,
                "producer": artifact.producer,
            }
            for artifact in manifest.artifacts
        ],
    }


def _report_include_flag(flag: str, project_root: Path) -> str:
    if not flag.startswith("-I") or len(flag) == 2:
        return flag
    path = Path(flag[2:])
    if not path.is_absolute():
        return flag
    try:
        return "-I" + path.resolve(strict=False).relative_to(project_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return "-I[external]"


def _serialize_analysis_context(context: AnalysisContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    project = context.project
    return {
        "schema_version": "ici.analysis-context/v1",
        "project": {
            "name": _require_string(project.name, "context.project.name", nonempty=True),
            "version": _require_string(project.version, "context.project.version", nonempty=True),
            "type": _require_string(
                project.project_type,
                "context.project.type",
                nonempty=True,
            ),
            "source_dirs": list(project.source_dirs),
            "python_sources": list(project.python_sources),
            "cpp_sources": list(project.cpp_sources),
            "cpp_headers": list(project.cpp_headers),
            "compilable_cpp_sources": list(project.compilable_cpp_sources),
            "external_cpp_dirs": list(project.external_cpp_dirs),
            "cpp_include_flags": [
                _report_include_flag(flag, project.root) for flag in project.cpp_include_flags
            ],
            "backend": project.backend,
            "backend_descriptor": project.backend_descriptor,
            "backend_reason": project.backend_reason,
        },
        "identity": {
            "source_commit": context.identity.source_commit,
            "config_digest": _require_digest(
                context.identity.config_digest,
                "context.identity.config_digest",
            ),
            "toolchain_digest": _require_digest(
                context.identity.toolchain_digest,
                "context.identity.toolchain_digest",
            ),
        },
        "compilation": {
            "database_path": context.compilation.database_path,
            "units": [
                {
                    "source": unit.source,
                    "directory": unit.directory,
                    "argv": list(unit.argv),
                    "output": unit.output,
                }
                for unit in context.compilation.units
            ],
        },
        "requested_variants": [variant.value for variant in context.requested_variants],
        "artifact_manifests": [
            _serialize_artifact_manifest(manifest) for manifest in context.manifests
        ],
    }


def _require_string(value: Any, field_name: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise ValueError(f"{field_name} must be a {qualifier}string: {value!r}")
    return value


def _require_digest(value: Any, field_name: str) -> str:
    digest = _require_string(value, field_name, nonempty=True)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{field_name} must be a sha256 digest: {value!r}")
    return digest


def _finite_number(
    value: Any,
    field_name: str,
    *,
    nullable: bool = False,
    nonnegative: bool = False,
) -> int | float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number: {value!r}")
    if nonnegative and value < 0:
        raise ValueError(f"{field_name} must be non-negative: {value!r}")
    return value


def _serialize_target(target: InspectionTarget) -> dict[str, Any]:
    """Serialize every legacy location field for compatibility consumers."""
    validate_source_region(
        start_line=target.start_line,
        end_line=target.end_line,
        start_column=target.start_column,
        end_column=target.end_column,
        context=f"legacy target {target.file_path!r}",
    )
    if not isinstance(target.metrics, dict):
        raise ValueError(f"legacy target metrics must be an object: {target.metrics!r}")
    return {
        "file_path": _require_string(target.file_path, "target.file_path", nonempty=True),
        "start_line": target.start_line,
        "end_line": target.end_line,
        "start_column": target.start_column,
        "end_column": target.end_column,
        "target_name": _require_string(target.target_name, "target.target_name"),
        "status": target.status.value,
        "message": _require_string(target.message, "target.message"),
        "snippet": _require_string(target.snippet, "target.snippet"),
        "metrics": target.metrics,
    }


def _serialize_location(location: SourceLocation) -> dict[str, Any]:
    validate_source_region(
        start_line=location.start_line,
        end_line=location.end_line,
        start_column=location.start_column,
        end_column=location.end_column,
        context=f"finding location {location.path!r}",
    )
    return {
        "path": _require_string(location.path, "location.path", nonempty=True),
        "start_line": location.start_line,
        "end_line": location.end_line,
        "start_column": location.start_column,
        "end_column": location.end_column,
        "label": _require_string(location.label, "location.label"),
    }


def _serialize_metric(metric: FindingMetric) -> dict[str, Any]:
    return {
        "value": _finite_number(metric.value, "finding metric value"),
        "unit": _require_string(metric.unit, "finding metric unit"),
    }


def _serialize_suppression(suppression: FindingSuppression) -> dict[str, Any]:
    if type(suppression.suppressed) is not bool:
        raise ValueError("finding suppression.suppressed must be a boolean")
    return {
        "suppressed": suppression.suppressed,
        "kind": suppression.kind.value,
        "reason": _require_string(suppression.reason, "finding suppression.reason"),
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
        "message": _require_string(finding.message, "finding.message"),
        "explanation": _require_string(finding.explanation, "finding.explanation"),
        "remediation": _require_string(finding.remediation, "finding.remediation"),
        "tool_rule_id": _require_string(finding.tool_rule_id, "finding.tool_rule_id"),
        "tool_name": _require_string(finding.tool_name, "finding.tool_name"),
        "tool_version": _require_string(finding.tool_version, "finding.tool_version"),
        "suppression": _serialize_suppression(finding.suppression),
        "metrics": {
            name: _serialize_metric(metric) for name, metric in sorted(finding.metrics.items())
        },
        "snippet": _require_string(finding.snippet, "finding.snippet"),
    }


def _serialize_analysis_metadata(metadata: AnalysisMetadata | None) -> dict[str, Any] | None:
    if metadata is None:
        return None
    return {
        "producer_version": _require_string(
            metadata.producer_version, "analysis_metadata.producer_version", nonempty=True
        ),
        "fingerprint_version": _require_string(
            metadata.fingerprint_version,
            "analysis_metadata.fingerprint_version",
            nonempty=True,
        ),
        "policy_digest": _require_digest(metadata.policy_digest, "analysis_metadata.policy_digest"),
        "tool_policy_digest": _require_digest(
            metadata.tool_policy_digest,
            "analysis_metadata.tool_policy_digest",
        ),
    }


def _validate_delta_presence(delta: FindingDelta) -> None:
    """Enforce which side(s) of a delta must carry location and severity."""

    has_current = delta.current_location is not None and delta.current_severity is not None
    has_baseline = delta.baseline_location is not None and delta.baseline_severity is not None
    if delta.state == DeltaState.NEW:
        if not has_current:
            raise ValueError("new finding delta must contain current location and severity")
        if delta.baseline_location is not None or delta.baseline_severity is not None:
            raise ValueError("new finding delta must not contain baseline state")
        return
    if delta.state == DeltaState.RESOLVED:
        if not has_baseline:
            raise ValueError("resolved finding delta must contain baseline location and severity")
        if delta.current_location is not None or delta.current_severity is not None:
            raise ValueError("resolved finding delta must not contain current state")
        return
    if not has_current or not has_baseline:
        raise ValueError("paired finding delta must contain current and baseline state")


def _validate_delta_relationship(delta: FindingDelta) -> None:
    if delta.state == DeltaState.UNCHANGED and delta.current_location != delta.baseline_location:
        raise ValueError("unchanged finding delta locations must match")
    if delta.state == DeltaState.MOVED and delta.current_location == delta.baseline_location:
        raise ValueError("moved finding delta locations must differ")
    if delta.regressed and delta.state in (DeltaState.NEW, DeltaState.RESOLVED):
        raise ValueError("only paired finding deltas can be regressed")


def _validate_delta_flags(delta: FindingDelta) -> None:
    if type(delta.regressed) is not bool or type(delta.suppressed) is not bool:
        raise ValueError("finding delta regressed/suppressed must be booleans")
    if type(delta.gated) is not bool:
        raise ValueError("finding delta gated must be a boolean")
    if delta.gated and (
        delta.suppressed
        or delta.current_severity == FindingSeverity.INFO
        or (delta.state != DeltaState.NEW and not delta.regressed)
    ):
        raise ValueError("finding delta gate state contradicts its severity or suppression")


def _serialize_optional_location(location: SourceLocation | None) -> dict[str, Any] | None:
    return _serialize_location(location) if location is not None else None


def _serialize_delta(delta: FindingDelta) -> dict[str, Any]:
    _validate_delta_presence(delta)
    _validate_delta_relationship(delta)
    _validate_delta_flags(delta)
    return {
        "state": delta.state.value,
        "engine_name": _require_string(
            delta.engine_name, "baseline delta engine_name", nonempty=True
        ),
        "fingerprint": _require_digest(delta.fingerprint, "baseline delta fingerprint"),
        "rule_id": _require_string(delta.rule_id, "baseline delta rule_id", nonempty=True),
        "message": _require_string(delta.message, "baseline delta message"),
        "current_location": _serialize_optional_location(delta.current_location),
        "baseline_location": _serialize_optional_location(delta.baseline_location),
        "current_severity": (
            delta.current_severity.value if delta.current_severity is not None else None
        ),
        "baseline_severity": (
            delta.baseline_severity.value if delta.baseline_severity is not None else None
        ),
        "regressed": delta.regressed,
        "suppressed": delta.suppressed,
        "gated": delta.gated,
    }


def _serialize_baseline_comparison(
    comparison: BaselineComparison | None,
) -> dict[str, Any] | None:
    if comparison is None:
        return None
    if type(comparison.fail_on_new) is not bool or type(comparison.gate_failed) is not bool:
        raise ValueError("baseline fail_on_new/gate_failed must be booleans")
    expected_gate_failed = comparison.fail_on_new and comparison.gated_count > 0
    if comparison.gate_failed != expected_gate_failed:
        raise ValueError("baseline gate_failed contradicts fail_on_new or gated entries")
    return {
        "source_path": _require_string(
            comparison.source_path, "baseline source_path", nonempty=True
        ),
        "warnings": _serialize_string_list(comparison.warnings, "baseline warnings"),
        "baseline_metadata": _serialize_analysis_metadata(comparison.baseline_metadata),
        "fail_on_new": comparison.fail_on_new,
        "gate_failed": comparison.gate_failed,
        "new_count": comparison.count(DeltaState.NEW),
        "unchanged_count": comparison.count(DeltaState.UNCHANGED),
        "moved_count": comparison.count(DeltaState.MOVED),
        "resolved_count": comparison.count(DeltaState.RESOLVED),
        "regressed_count": comparison.regressed_count,
        "gated_count": comparison.gated_count,
        "entries": [_serialize_delta(entry) for entry in comparison.entries],
    }


def _serialize_tool_evidence(tool: ToolEvidence) -> dict[str, Any]:
    """Serialize the complete external-tool execution evidence contract."""
    if not isinstance(tool.argv, list) or not all(isinstance(item, str) for item in tool.argv):
        raise ValueError("tool_evidence.argv must be an array of strings")
    if tool.returncode is not None and type(tool.returncode) is not int:
        raise ValueError("tool_evidence.returncode must be an integer or null")
    if type(tool.timed_out) is not bool or type(tool.truncated) is not bool:
        raise ValueError("tool_evidence timed_out/truncated must be booleans")
    return {
        "name": _require_string(tool.name, "tool_evidence.name"),
        "path": _require_string(tool.path, "tool_evidence.path"),
        "version": _require_string(tool.version, "tool_evidence.version"),
        "argv": list(tool.argv),
        "returncode": tool.returncode,
        "timed_out": tool.timed_out,
        "truncated": tool.truncated,
        "error": _require_string(tool.error, "tool_evidence.error"),
    }


def _serialize_string_list(
    values: list[str], field_name: str, *, unique: bool = False
) -> list[str]:
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be an array of strings")
    serialized = [_require_string(value, f"{field_name} item", nonempty=True) for value in values]
    if unique and len(serialized) != len(set(serialized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return serialized


def _serialize_support_entry(entry: EngineSupport) -> dict[str, Any]:
    if type(entry.applicable) is not bool or type(entry.enabled) is not bool:
        raise ValueError("support applicable/enabled must be booleans")
    if entry.mode.value == "unsupported" and entry.applicable:
        raise ValueError("unsupported support entry cannot be applicable")
    if (not entry.applicable or not entry.enabled) and entry.active_mode is not None:
        raise ValueError("inactive support entry cannot declare active_mode")
    if entry.evidence in (EvidenceState.NOT_APPLICABLE, EvidenceState.NOT_RUN):
        if entry.active_mode is not None:
            raise ValueError("unobserved support entry cannot declare active_mode")
    elif entry.applicable and entry.enabled and entry.active_mode is None:
        raise ValueError("observed support entry must declare active_mode")
    required_tools = _serialize_string_list(
        entry.required_tools, "support.required_tools", unique=True
    )
    optional_tools = _serialize_string_list(
        entry.optional_tools, "support.optional_tools", unique=True
    )
    if set(required_tools) & set(optional_tools):
        raise ValueError("support tool cannot be both required and optional")
    return {
        "engine_name": _require_string(entry.engine_name, "support.engine_name", nonempty=True),
        "language": entry.language.value,
        "mode": entry.mode.value,
        "active_mode": entry.active_mode.value if entry.active_mode is not None else None,
        "applicable": entry.applicable,
        "enabled": entry.enabled,
        "evidence": entry.evidence.value,
        "confidence": entry.confidence.value,
        "frameworks": _serialize_string_list(entry.frameworks, "support.frameworks", unique=True),
        "required_tools": required_tools,
        "optional_tools": optional_tools,
        "fallback_mode": entry.fallback_mode.value if entry.fallback_mode is not None else None,
        "limitations": _serialize_string_list(entry.limitations, "support.limitations"),
        "reason": _require_string(entry.reason, "support.reason"),
    }


def serialize_support_matrix(matrix: SupportMatrix | None) -> dict[str, Any] | None:
    """Serialize a project capability matrix using the v3 enum vocabulary."""

    if matrix is None:
        return None
    if not isinstance(matrix.project_languages, list):
        raise ValueError("support.project_languages must be an array")
    project_languages = [item.value for item in matrix.project_languages]
    if len(project_languages) != len(set(project_languages)):
        raise ValueError("support.project_languages must not contain duplicates")
    if not isinstance(matrix.entries, list) or not all(
        isinstance(entry, EngineSupport) for entry in matrix.entries
    ):
        raise ValueError("support.entries must be an array of EngineSupport values")
    entry_keys = [(entry.engine_name, entry.language.value) for entry in matrix.entries]
    if len(entry_keys) != len(set(entry_keys)):
        raise ValueError("support.entries must contain unique engine/language pairs")
    return {
        "project_languages": project_languages,
        "project_frameworks": _serialize_string_list(
            matrix.project_frameworks, "support.project_frameworks", unique=True
        ),
        "entries": [_serialize_support_entry(entry) for entry in matrix.entries],
    }


def serialize_engine_result(
    result: EngineResult, project_root: str | Path | None = None
) -> dict[str, Any]:
    """Return the canonical v3 representation of one sanitized engine result."""
    safe = redact_engine_result(result)
    if not isinstance(safe.extra, dict):
        raise ValueError(f"engine.extra must be an object: {safe.extra!r}")
    if type(safe.required) is not bool:
        raise ValueError(f"engine.required must be a boolean: {safe.required!r}")
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "engine_name": _require_string(safe.engine_name, "engine.engine_name", nonempty=True),
        "status": safe.status.value,
        "summary": _require_string(safe.summary, "engine.summary"),
        "score": _finite_number(safe.score, "engine.score", nullable=True),
        "max_score": _finite_number(safe.max_score, "engine.max_score", nullable=True),
        "duration": _finite_number(safe.duration, "engine.duration", nonnegative=True),
        "raw_output": _require_string(safe.raw_output, "engine.raw_output"),
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
        "support_matrix": serialize_support_matrix(safe.support_matrix),
        "artifact_manifests": [
            _serialize_artifact_manifest(manifest) for manifest in safe.artifact_manifests
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
        "duration": _finite_number(safe.duration, "suite.duration", nonnegative=True),
        "passed_count": safe.passed_count,
        "warned_count": safe.warned_count,
        # failed_count intentionally retains its historical FAIL+ERROR meaning.
        "failed_count": safe.failed_count,
        "error_count": safe.error_count,
        "skipped_count": safe.skipped_count,
        "total_count": safe.total_count,
        "tem_score": _finite_number(safe.tem_score, "suite.tem_score", nullable=True),
        "max_tem_score": _finite_number(
            safe.max_tem_score, "suite.max_tem_score", nonnegative=True
        ),
        "results": [
            serialize_engine_result(result, project_root=project_root) for result in safe.results
        ],
        "support_matrix": serialize_support_matrix(safe.support_matrix),
        "analysis_metadata": _serialize_analysis_metadata(safe.analysis_metadata),
        "baseline_comparison": _serialize_baseline_comparison(safe.baseline_comparison),
        "capability_inventory": (
            serialize_capability_inventory(safe.capability_inventory)
            if safe.capability_inventory is not None
            else None
        ),
        "analysis_context": _serialize_analysis_context(safe.analysis_context),
    }


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_number(
    value: Any,
    *,
    default: float | None,
    nonnegative: bool = False,
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0):
        return default
    return number


def _target_from_payload(payload: dict[str, Any]) -> InspectionTarget:
    try:
        status = EngineStatus(str(payload.get("status", "PASS")))
    except ValueError:
        status = EngineStatus.WARN
    metrics = payload.get("metrics", {})
    start_line = _optional_int(payload.get("start_line")) or 1
    if start_line < 1:
        start_line = 1
    end_line = _optional_int(payload.get("end_line"))
    if end_line is not None and end_line < start_line:
        end_line = None
    start_column = _optional_int(payload.get("start_column"))
    if start_column is not None and start_column < 1:
        start_column = None
    end_column = _optional_int(payload.get("end_column"))
    if end_column is not None and end_column < 1:
        end_column = None
    if (
        start_column is not None
        and end_column is not None
        and end_line in (None, start_line)
        and end_column < start_column
    ):
        end_column = None
    file_path = payload.get("file_path")
    return InspectionTarget(
        file_path=file_path if isinstance(file_path, str) and file_path else "unknown",
        start_line=start_line,
        end_line=end_line,
        target_name=str(payload.get("target_name", "")),
        status=status,
        message=str(payload.get("message", "")),
        snippet=str(payload.get("snippet", "")),
        metrics=metrics if isinstance(metrics, dict) else {},
        start_column=start_column,
        end_column=end_column,
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
    engine_name = payload.get("engine_name")
    duration = _payload_number(payload.get("duration"), default=0.0, nonnegative=True)
    return EngineResult(
        engine_name=(engine_name if isinstance(engine_name, str) and engine_name else "unknown"),
        status=status,
        summary=str(payload.get("summary", "")),
        score=_payload_number(payload.get("score"), default=None),
        max_score=_payload_number(payload.get("max_score"), default=None),
        duration=duration if duration is not None else 0.0,
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

    engine_models: list[EngineResult] = []
    for engine_payload in candidates:
        engine_payload["schema_version"] = RESULT_SCHEMA_VERSION
        if version == LEGACY_RESULT_SCHEMA_VERSION or not isinstance(
            engine_payload.get("findings"), list
        ):
            engine = _engine_from_v2(engine_payload)
            engine_models.append(engine)
            # Canonical fields replace malformed or missing legacy fields;
            # producer-specific extension keys remain alongside them.
            engine_payload.update(serialize_engine_result(engine, project_root=project_root))

    if isinstance(engines, list) and version == LEGACY_RESULT_SCHEMA_VERSION:
        migrated["results"] = candidates
        try:
            suite_status = EngineStatus(str(migrated.get("suite_status", "ERROR")))
        except ValueError:
            suite_status = EngineStatus.ERROR
        duration = _payload_number(migrated.get("duration"), default=0.0, nonnegative=True)
        max_tem = _payload_number(migrated.get("max_tem_score"), default=5.0, nonnegative=True)
        suite = VerificationSuiteResult(
            suite_status=suite_status,
            results=engine_models,
            duration=duration if duration is not None else 0.0,
            tem_score=_payload_number(migrated.get("tem_score"), default=None),
            max_tem_score=max_tem if max_tem is not None else 5.0,
        )
        canonical_suite = serialize_suite_result(suite, project_root=project_root)
        migrated.update({key: value for key, value in canonical_suite.items() if key != "results"})

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
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(output_path)
    except OSError:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise
