"""Strict JSON codec for cached engine results."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core.cache_identity import (
    _DIGEST_PREFIX,
    CACHE_KEY_VERSION,
    CACHE_SCHEMA_VERSION,
    AnalysisCacheKey,
    CacheEntryError,
    _require_digest,
    is_cacheable_result,
)
from ici.core.context import (
    ArtifactManifest,
    ArtifactRecord,
    ArtifactScope,
    BuildVariant,
)
from ici.core.models import (
    AnalysisMode,
    EngineResult,
    EngineStatus,
    EngineSupport,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    FindingSuppression,
    InspectionTarget,
    SourceLocation,
    SupportLanguage,
    SupportMatrix,
    SuppressionKind,
    ToolEvidence,
)
from ici.reporters.json_rep import serialize_engine_result


def _reject_json_constant(value: str) -> None:
    raise CacheEntryError(f"cache JSON contains non-finite constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CacheEntryError(f"cache JSON contains duplicate key: {key}")
        value[key] = item
    return value


def read_cache_json(path: Path, max_bytes: int) -> Any:
    """Read one bounded cache file, rejecting links and ambiguous JSON."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
            raise CacheEntryError("cache entry is not a bounded regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            encoded = stream.read(max_bytes + 1)
        if len(encoded) > max_bytes:
            raise CacheEntryError("cache entry exceeds the size limit")
        return json.loads(
            encoded.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    finally:
        os.close(descriptor)


def serialize_cache_result(result: EngineResult, project_root: Path) -> dict[str, Any]:
    """Serialize native findings only; legacy target findings are report-time projections."""

    serialized = serialize_engine_result(result, project_root=project_root)
    native_only = replace(result, targets=[])
    serialized["findings"] = serialize_engine_result(
        native_only,
        project_root=project_root,
    )["findings"]
    return serialized


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CacheEntryError(f"{context} must be an object")
    return value


def _sequence(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise CacheEntryError(f"{context} must be an array")
    return value


def _string(value: Any, context: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise CacheEntryError(f"{context} must be a string")
    return value


def _boolean(value: Any, context: str) -> bool:
    if type(value) is not bool:
        raise CacheEntryError(f"{context} must be a boolean")
    return value


def _number(value: Any, context: str, *, nullable: bool = False) -> int | float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CacheEntryError(f"{context} must be a number")
    number = float(value)
    if not (number >= 0.0) or number == float("inf"):
        raise CacheEntryError(f"{context} must be finite and non-negative")
    return value


def _optional_int(value: Any, context: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise CacheEntryError(f"{context} must be a positive integer or null")
    return value


def _string_list(value: Any, context: str) -> list[str]:
    return [_string(item, f"{context} item") for item in _sequence(value, context)]


def _location(payload: Any, context: str) -> SourceLocation:
    value = _mapping(payload, context)
    return SourceLocation(
        path=_string(value.get("path"), f"{context}.path", nonempty=True),
        start_line=_optional_int(value.get("start_line"), f"{context}.start_line") or 1,
        end_line=_optional_int(value.get("end_line"), f"{context}.end_line"),
        start_column=_optional_int(value.get("start_column"), f"{context}.start_column"),
        end_column=_optional_int(value.get("end_column"), f"{context}.end_column"),
        label=_string(value.get("label"), f"{context}.label"),
    )


def _target(payload: Any) -> InspectionTarget:
    value = _mapping(payload, "cache target")
    metrics = _mapping(value.get("metrics"), "cache target.metrics")
    return InspectionTarget(
        file_path=_string(value.get("file_path"), "cache target.file_path", nonempty=True),
        start_line=_optional_int(value.get("start_line"), "cache target.start_line") or 1,
        end_line=_optional_int(value.get("end_line"), "cache target.end_line"),
        target_name=_string(value.get("target_name"), "cache target.target_name"),
        status=EngineStatus(_string(value.get("status"), "cache target.status")),
        message=_string(value.get("message"), "cache target.message"),
        snippet=_string(value.get("snippet"), "cache target.snippet"),
        metrics=metrics,
        start_column=_optional_int(value.get("start_column"), "cache target.start_column"),
        end_column=_optional_int(value.get("end_column"), "cache target.end_column"),
    )


def _tool(payload: Any) -> ToolEvidence:
    value = _mapping(payload, "cache tool evidence")
    returncode = value.get("returncode")
    if returncode is not None and type(returncode) is not int:
        raise CacheEntryError("cache tool returncode must be an integer or null")
    return ToolEvidence(
        name=_string(value.get("name"), "cache tool.name"),
        path=_string(value.get("path"), "cache tool.path"),
        version=_string(value.get("version"), "cache tool.version"),
        argv=_string_list(value.get("argv"), "cache tool.argv"),
        returncode=returncode,
        timed_out=_boolean(value.get("timed_out"), "cache tool.timed_out"),
        truncated=_boolean(value.get("truncated"), "cache tool.truncated"),
        error=_string(value.get("error"), "cache tool.error"),
    )


def _finding(payload: Any) -> Finding:
    value = _mapping(payload, "cache finding")
    suppression_value = _mapping(value.get("suppression"), "cache finding.suppression")
    metrics_value = _mapping(value.get("metrics"), "cache finding.metrics")
    metrics: dict[str, FindingMetric] = {}
    for name, metric_payload in metrics_value.items():
        metric = _mapping(metric_payload, f"cache finding metric {name}")
        metric_number = metric.get("value")
        if isinstance(metric_number, bool) or not isinstance(metric_number, (int, float)):
            raise CacheEntryError("cache finding metric value must be numeric")
        metrics[name] = FindingMetric(
            value=metric_number,
            unit=_string(metric.get("unit"), "cache finding metric unit"),
        )
    related = _sequence(value.get("related_locations"), "cache related locations")
    return Finding(
        rule_id=_string(value.get("rule_id"), "cache finding.rule_id", nonempty=True),
        category=FindingCategory(_string(value.get("category"), "cache finding.category")),
        severity=FindingSeverity(_string(value.get("severity"), "cache finding.severity")),
        confidence=FindingConfidence(_string(value.get("confidence"), "cache finding.confidence")),
        fingerprint=_require_digest(value.get("fingerprint"), "cache finding fingerprint"),
        primary_location=_location(value.get("primary_location"), "cache primary location"),
        related_locations=[_location(item, "cache related location") for item in related],
        message=_string(value.get("message"), "cache finding.message"),
        explanation=_string(value.get("explanation"), "cache finding.explanation"),
        remediation=_string(value.get("remediation"), "cache finding.remediation"),
        tool_rule_id=_string(value.get("tool_rule_id"), "cache finding.tool_rule_id"),
        tool_name=_string(value.get("tool_name"), "cache finding.tool_name"),
        tool_version=_string(value.get("tool_version"), "cache finding.tool_version"),
        suppression=FindingSuppression(
            suppressed=_boolean(
                suppression_value.get("suppressed"), "cache suppression.suppressed"
            ),
            kind=SuppressionKind(_string(suppression_value.get("kind"), "cache suppression.kind")),
            reason=_string(suppression_value.get("reason"), "cache suppression.reason"),
        ),
        metrics=metrics,
        snippet=_string(value.get("snippet"), "cache finding.snippet"),
    )


def _support(payload: Any) -> SupportMatrix | None:
    if payload is None:
        return None
    value = _mapping(payload, "cache support matrix")
    entries: list[EngineSupport] = []
    for item in _sequence(value.get("entries"), "cache support entries"):
        entry = _mapping(item, "cache support entry")
        active_mode = entry.get("active_mode")
        fallback_mode = entry.get("fallback_mode")
        entries.append(
            EngineSupport(
                engine_name=_string(entry.get("engine_name"), "cache support engine"),
                language=SupportLanguage(_string(entry.get("language"), "cache support language")),
                mode=AnalysisMode(_string(entry.get("mode"), "cache support mode")),
                active_mode=(
                    AnalysisMode(_string(active_mode, "cache active mode"))
                    if active_mode is not None
                    else None
                ),
                applicable=_boolean(entry.get("applicable"), "cache support applicable"),
                enabled=_boolean(entry.get("enabled"), "cache support enabled"),
                evidence=EvidenceState(_string(entry.get("evidence"), "cache support evidence")),
                confidence=FindingConfidence(
                    _string(entry.get("confidence"), "cache support confidence")
                ),
                frameworks=_string_list(entry.get("frameworks"), "cache frameworks"),
                required_tools=_string_list(entry.get("required_tools"), "cache required tools"),
                optional_tools=_string_list(entry.get("optional_tools"), "cache optional tools"),
                fallback_mode=(
                    AnalysisMode(_string(fallback_mode, "cache fallback mode"))
                    if fallback_mode is not None
                    else None
                ),
                limitations=_string_list(entry.get("limitations"), "cache limitations"),
                reason=_string(entry.get("reason"), "cache support reason"),
            )
        )
    return SupportMatrix(
        project_languages=[
            SupportLanguage(_string(item, "cache project language"))
            for item in _sequence(value.get("project_languages"), "cache project languages")
        ],
        project_frameworks=_string_list(
            value.get("project_frameworks"), "cache project frameworks"
        ),
        entries=entries,
    )


def _manifest(payload: Any, project_root: Path) -> ArtifactManifest:
    value = _mapping(payload, "cache artifact manifest")
    if value.get("schema_version") != "ici.artifacts/v1" or value.get("project_root") != ".":
        raise CacheEntryError("cache artifact manifest identity is invalid")
    shadow_value = value.get("shadow_root")
    shadow_root = None
    if shadow_value is not None:
        shadow_text = _string(shadow_value, "cache shadow root", nonempty=True)
        shadow_relative = PurePosixPath(shadow_text)
        if shadow_relative.is_absolute() or ".." in shadow_relative.parts:
            raise CacheEntryError("cache shadow root must be project-relative")
        shadow_root = project_root / shadow_relative
    records = []
    for item in _sequence(value.get("artifacts"), "cache artifacts"):
        record = _mapping(item, "cache artifact")
        size = record.get("size")
        mode = record.get("mode")
        if type(size) is not int or size < 0 or type(mode) is not int or mode < 0:
            raise CacheEntryError("cache artifact size/mode is invalid")
        records.append(
            ArtifactRecord(
                path=_string(record.get("path"), "cache artifact path", nonempty=True),
                scope=ArtifactScope(_string(record.get("scope"), "cache artifact scope")),
                kind=_string(record.get("kind"), "cache artifact kind", nonempty=True),
                sha256=_require_digest(record.get("sha256"), "cache artifact digest"),
                size=size,
                mode=mode,
                producer=_string(record.get("producer"), "cache artifact producer", nonempty=True),
            )
        )
    manifest = ArtifactManifest(
        project_root=project_root,
        shadow_root=shadow_root,
        variant=BuildVariant(_string(value.get("variant"), "cache artifact variant")),
        source_commit=_string(
            value.get("source_commit"), "cache artifact source commit", nonempty=True
        ),
        config_digest=_require_digest(value.get("config_digest"), "cache artifact config digest"),
        toolchain_digest=_require_digest(
            value.get("toolchain_digest"), "cache artifact toolchain digest"
        ),
        artifacts=tuple(records),
    )
    return manifest.validate()


def _decode_result(payload: Any, project_root: Path) -> EngineResult:
    value = _mapping(payload, "cache result")
    if value.get("schema_version") != "ici.result/v3":
        raise CacheEntryError("cache result schema is unsupported")
    return EngineResult(
        engine_name=_string(value.get("engine_name"), "cache engine name", nonempty=True),
        status=EngineStatus(_string(value.get("status"), "cache result status")),
        summary=_string(value.get("summary"), "cache result summary"),
        score=_number(value.get("score"), "cache result score", nullable=True),
        max_score=_number(value.get("max_score"), "cache result max score", nullable=True),
        duration=_number(value.get("duration"), "cache result duration") or 0.0,
        targets=[_target(item) for item in _sequence(value.get("targets"), "cache targets")],
        raw_output=_string(value.get("raw_output"), "cache raw output"),
        extra=_mapping(value.get("extra"), "cache extra"),
        required=_boolean(value.get("required"), "cache required"),
        evidence=EvidenceState(_string(value.get("evidence"), "cache evidence")),
        tool_evidence=[
            _tool(item) for item in _sequence(value.get("tool_evidence"), "cache tool evidence")
        ],
        findings=[_finding(item) for item in _sequence(value.get("findings"), "cache findings")],
        support_matrix=_support(value.get("support_matrix")),
        artifact_manifests=tuple(
            _manifest(item, project_root)
            for item in _sequence(value.get("artifact_manifests"), "cache artifact manifests")
        ),
    )


def decode_entry(
    payload: Any,
    key: AnalysisCacheKey,
    project_root: Path,
) -> EngineResult:
    """Decode and validate a complete cache entry against its requested key."""

    value = _mapping(payload, "cache entry")
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CacheEntryError("cache entry schema is unsupported")
    if value.get("key") != key.digest or value.get("inputs") != key.payload():
        raise CacheEntryError("cache entry identity does not match its key")
    created_at = value.get("created_at")
    if type(created_at) is not int or created_at < 0:
        raise CacheEntryError("cache entry timestamp is invalid")
    result = _decode_result(value.get("result"), project_root)
    if result.engine_name != key.engine_name or not is_cacheable_result(result, key):
        raise CacheEntryError("cache result does not match its engine identity")
    return result


def validate_inventory_entry(payload: Any, path: Path) -> None:
    """Validate only the metadata needed to count one inventory entry."""

    value = _mapping(payload, "cache inventory entry")
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CacheEntryError("cache inventory schema is unsupported")
    key = _require_digest(value.get("key"), "cache inventory key")
    if path.stem != key.removeprefix(_DIGEST_PREFIX):
        raise CacheEntryError("cache inventory filename does not match key")
    inputs = _mapping(value.get("inputs"), "cache inventory inputs")
    if inputs.get("key_version") != CACHE_KEY_VERSION:
        raise CacheEntryError("cache inventory key version is unsupported")
    _mapping(value.get("result"), "cache inventory result")
