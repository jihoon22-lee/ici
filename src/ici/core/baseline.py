"""Secure v3 baseline loading, compatibility checks, and finding deltas."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ici import __version__
from ici.core.findings import (
    FINGERPRINT_VERSION,
    canonical_project_path,
    findings_for_result,
    validate_source_region,
)
from ici.core.models import (
    AnalysisMetadata,
    BaselineComparison,
    DeltaState,
    Finding,
    FindingDelta,
    FindingSeverity,
    SourceLocation,
    SupportMatrix,
    VerificationSuiteResult,
)
from ici.core.path_utils import resolve_project_path

BASELINE_MAX_BYTES = 64 * 1024 * 1024
RESULT_SCHEMA_VERSION = "ici.result/v3"

_FINGERPRINT_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SEVERITY_RANK = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}
_STATE_ORDER = {
    DeltaState.NEW: 0,
    DeltaState.MOVED: 1,
    DeltaState.UNCHANGED: 2,
    DeltaState.RESOLVED: 3,
}


class BaselineError(ValueError):
    """Raised when a baseline is unreadable, unsafe, or outside the v3 contract."""


@dataclass(frozen=True)
class _FindingRecord:
    engine_name: str
    fingerprint: str
    rule_id: str
    severity: FindingSeverity
    location: SourceLocation
    message: str
    suppressed: bool


@dataclass(frozen=True)
class _BaselineDocument:
    metadata: AnalysisMetadata | None
    findings: tuple[_FindingRecord, ...]


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def build_analysis_metadata(
    config: dict[str, Any], support_matrix: SupportMatrix
) -> AnalysisMetadata:
    """Describe the policy identities that can affect a finding comparison."""

    engine_policy = config.get("engines", {})
    tool_policy = [
        {
            "engine_name": entry.engine_name,
            "language": entry.language.value,
            "mode": entry.mode.value,
            "enabled": entry.enabled,
            "frameworks": sorted(entry.frameworks),
            "required_tools": sorted(entry.required_tools),
            "optional_tools": sorted(entry.optional_tools),
            "fallback_mode": (
                entry.fallback_mode.value if entry.fallback_mode is not None else None
            ),
        }
        for entry in sorted(
            support_matrix.entries,
            key=lambda item: (item.engine_name, item.language.value),
        )
    ]
    return AnalysisMetadata(
        producer_version=__version__,
        fingerprint_version=FINGERPRINT_VERSION,
        policy_digest=_digest(engine_policy),
        tool_policy_digest=_digest(tool_policy),
    )


def _require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BaselineError(f"{context} must be an object")
    return value


def _require_list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise BaselineError(f"{context} must be an array")
    return value


def _require_string(value: Any, context: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise BaselineError(f"{context} must be a {qualifier}string")
    return value


def _optional_positive_int(value: Any, context: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise BaselineError(f"{context} must be a 1-indexed integer or null")
    return value


def _location_from_payload(value: Any, project_root: Path, context: str) -> SourceLocation:
    payload = _require_object(value, context)
    raw_path = _require_string(payload.get("path"), f"{context}.path", nonempty=True)
    try:
        canonical_path = canonical_project_path(raw_path)
        resolve_project_path(project_root, canonical_path)
    except ValueError as err:
        raise BaselineError(f"{context}.path is unsafe: {err}") from err

    start_line = _optional_positive_int(payload.get("start_line"), f"{context}.start_line")
    if start_line is None:
        raise BaselineError(f"{context}.start_line must be a 1-indexed integer")
    end_line = _optional_positive_int(payload.get("end_line"), f"{context}.end_line")
    start_column = _optional_positive_int(payload.get("start_column"), f"{context}.start_column")
    end_column = _optional_positive_int(payload.get("end_column"), f"{context}.end_column")
    try:
        validate_source_region(
            start_line=start_line,
            end_line=end_line,
            start_column=start_column,
            end_column=end_column,
            context=context,
        )
    except ValueError as err:
        raise BaselineError(str(err)) from err
    return SourceLocation(
        path=canonical_path,
        start_line=start_line,
        end_line=end_line,
        start_column=start_column,
        end_column=end_column,
        label=_require_string(payload.get("label"), f"{context}.label"),
    )


def _metadata_from_payload(value: Any) -> AnalysisMetadata | None:
    if value is None:
        return None
    payload = _require_object(value, "analysis_metadata")
    return AnalysisMetadata(
        producer_version=_require_string(
            payload.get("producer_version"), "analysis_metadata.producer_version", nonempty=True
        ),
        fingerprint_version=_require_string(
            payload.get("fingerprint_version"),
            "analysis_metadata.fingerprint_version",
            nonempty=True,
        ),
        policy_digest=_require_digest(
            payload.get("policy_digest"), "analysis_metadata.policy_digest"
        ),
        tool_policy_digest=_require_digest(
            payload.get("tool_policy_digest"), "analysis_metadata.tool_policy_digest"
        ),
    )


def _require_digest(value: Any, context: str) -> str:
    digest = _require_string(value, context, nonempty=True)
    if _FINGERPRINT_RE.fullmatch(digest) is None:
        raise BaselineError(f"{context} must be a sha256 digest")
    return digest


def _finding_from_payload(
    value: Any, *, engine_name: str, project_root: Path, index: int
) -> _FindingRecord:
    context = f"results[{engine_name!r}].findings[{index}]"
    payload = _require_object(value, context)
    fingerprint = _require_digest(payload.get("fingerprint"), f"{context}.fingerprint")
    rule_id = _require_string(payload.get("rule_id"), f"{context}.rule_id", nonempty=True)
    severity_text = _require_string(payload.get("severity"), f"{context}.severity", nonempty=True)
    try:
        severity = FindingSeverity(severity_text)
    except ValueError as err:
        raise BaselineError(f"{context}.severity is unsupported: {severity_text!r}") from err
    location = _location_from_payload(
        payload.get("primary_location"), project_root, f"{context}.primary_location"
    )
    for related_index, related in enumerate(
        _require_list(payload.get("related_locations"), f"{context}.related_locations")
    ):
        _location_from_payload(
            related,
            project_root,
            f"{context}.related_locations[{related_index}]",
        )
    suppression = _require_object(payload.get("suppression"), f"{context}.suppression")
    suppressed = suppression.get("suppressed")
    if type(suppressed) is not bool:
        raise BaselineError(f"{context}.suppression.suppressed must be a boolean")
    return _FindingRecord(
        engine_name=engine_name,
        fingerprint=fingerprint,
        rule_id=rule_id,
        severity=severity,
        location=location,
        message=_require_string(payload.get("message"), f"{context}.message"),
        suppressed=suppressed,
    )


def load_baseline(path: Path, project_root: Path) -> _BaselineDocument:
    """Read a root-contained v3 report and validate every referenced location."""

    root = project_root.resolve()
    try:
        baseline_path = resolve_project_path(root, str(path))
        if not baseline_path.is_file():
            raise BaselineError(f"baseline is not a file: {path}")
        size = baseline_path.stat().st_size
        if size > BASELINE_MAX_BYTES:
            raise BaselineError(
                f"baseline exceeds the {BASELINE_MAX_BYTES // (1024 * 1024)} MiB limit: {path}"
            )
        text = baseline_path.read_text(encoding="utf-8")
    except BaselineError:
        raise
    except (OSError, UnicodeError, ValueError) as err:
        raise BaselineError(f"could not read baseline {path}: {err}") from err
    try:
        root_payload = _require_object(json.loads(text), "baseline root")
    except (json.JSONDecodeError, RecursionError) as err:
        raise BaselineError(f"could not parse baseline {path}: {err}") from err
    schema = root_payload.get("schema_version")
    if schema != RESULT_SCHEMA_VERSION:
        raise BaselineError(
            f"unsupported baseline schema_version {schema!r}; expected {RESULT_SCHEMA_VERSION!r}"
        )

    records: list[_FindingRecord] = []
    for engine_index, engine_value in enumerate(
        _require_list(root_payload.get("results"), "baseline.results")
    ):
        engine = _require_object(engine_value, f"baseline.results[{engine_index}]")
        engine_name = _require_string(
            engine.get("engine_name"),
            f"baseline.results[{engine_index}].engine_name",
            nonempty=True,
        )
        findings = _require_list(
            engine.get("findings"), f"baseline.results[{engine_index}].findings"
        )
        records.extend(
            _finding_from_payload(
                finding,
                engine_name=engine_name,
                project_root=root,
                index=finding_index,
            )
            for finding_index, finding in enumerate(findings)
        )
    return _BaselineDocument(
        metadata=_metadata_from_payload(root_payload.get("analysis_metadata")),
        findings=tuple(records),
    )


def _record_from_finding(engine_name: str, finding: Finding) -> _FindingRecord:
    return _FindingRecord(
        engine_name=engine_name,
        fingerprint=finding.fingerprint,
        rule_id=finding.rule_id,
        severity=finding.severity,
        location=finding.primary_location,
        message=finding.message,
        suppressed=finding.suppression.suppressed,
    )


def _location_key(location: SourceLocation) -> tuple[Any, ...]:
    return (
        location.path,
        location.start_line,
        location.start_column or 0,
        location.end_line or 0,
        location.end_column or 0,
        location.label,
    )


def _record_key(record: _FindingRecord) -> tuple[Any, ...]:
    return (*_location_key(record.location), _SEVERITY_RANK[record.severity], record.message)


def _delta_location_key(delta: FindingDelta) -> tuple[Any, ...]:
    location = delta.current_location or delta.baseline_location
    if location is None:
        raise RuntimeError("finding delta has neither a current nor baseline location")
    return _location_key(location)


def _is_regressed(current: _FindingRecord, baseline: _FindingRecord) -> bool:
    return _SEVERITY_RANK[current.severity] > _SEVERITY_RANK[baseline.severity]


def _is_actionable(current: _FindingRecord) -> bool:
    return not current.suppressed and current.severity != FindingSeverity.INFO


def _paired_delta(
    current: _FindingRecord, baseline: _FindingRecord, state: DeltaState
) -> FindingDelta:
    regressed = _is_regressed(current, baseline)
    return FindingDelta(
        state=state,
        engine_name=current.engine_name,
        fingerprint=current.fingerprint,
        rule_id=current.rule_id,
        message=current.message,
        current_location=current.location,
        baseline_location=baseline.location,
        current_severity=current.severity,
        baseline_severity=baseline.severity,
        regressed=regressed,
        suppressed=current.suppressed,
        gated=regressed and _is_actionable(current),
    )


def _new_delta(current: _FindingRecord) -> FindingDelta:
    return FindingDelta(
        state=DeltaState.NEW,
        engine_name=current.engine_name,
        fingerprint=current.fingerprint,
        rule_id=current.rule_id,
        message=current.message,
        current_location=current.location,
        current_severity=current.severity,
        suppressed=current.suppressed,
        gated=_is_actionable(current),
    )


def _resolved_delta(baseline: _FindingRecord) -> FindingDelta:
    return FindingDelta(
        state=DeltaState.RESOLVED,
        engine_name=baseline.engine_name,
        fingerprint=baseline.fingerprint,
        rule_id=baseline.rule_id,
        message=baseline.message,
        baseline_location=baseline.location,
        baseline_severity=baseline.severity,
        suppressed=baseline.suppressed,
    )


def _compare_group(
    current_records: list[_FindingRecord], baseline_records: list[_FindingRecord]
) -> list[FindingDelta]:
    current = sorted(current_records, key=_record_key)
    baseline_by_location: dict[tuple[Any, ...], deque[_FindingRecord]] = defaultdict(deque)
    for record in sorted(baseline_records, key=_record_key):
        baseline_by_location[_location_key(record.location)].append(record)

    deltas: list[FindingDelta] = []
    unmatched_current: list[_FindingRecord] = []
    for record in current:
        candidates = baseline_by_location[_location_key(record.location)]
        if candidates:
            deltas.append(_paired_delta(record, candidates.popleft(), DeltaState.UNCHANGED))
        else:
            unmatched_current.append(record)

    unmatched_baseline = sorted(
        (record for records in baseline_by_location.values() for record in records),
        key=_record_key,
    )
    pair_count = min(len(unmatched_current), len(unmatched_baseline))
    deltas.extend(
        _paired_delta(unmatched_current[index], unmatched_baseline[index], DeltaState.MOVED)
        for index in range(pair_count)
    )
    deltas.extend(_new_delta(record) for record in unmatched_current[pair_count:])
    deltas.extend(_resolved_delta(record) for record in unmatched_baseline[pair_count:])
    return deltas


def _compatibility_warnings(
    baseline: AnalysisMetadata | None, current: AnalysisMetadata
) -> list[str]:
    if baseline is None:
        return [
            "baseline has no analysis_metadata; producer, fingerprint, policy, and tool "
            "compatibility could not be verified"
        ]
    warnings: list[str] = []
    comparisons = (
        ("producer version", baseline.producer_version, current.producer_version),
        ("fingerprint version", baseline.fingerprint_version, current.fingerprint_version),
        ("analysis policy", baseline.policy_digest, current.policy_digest),
        ("tool policy", baseline.tool_policy_digest, current.tool_policy_digest),
    )
    for label, old, new in comparisons:
        if old != new:
            warnings.append(f"baseline {label} differs: {old} != {new}")
    return warnings


def compare_suite_to_baseline(
    suite: VerificationSuiteResult,
    *,
    baseline_path: Path,
    project_root: Path,
    current_metadata: AnalysisMetadata,
    fail_on_new: bool = False,
) -> BaselineComparison:
    """Compare all finding occurrences without collapsing duplicate fingerprints."""

    root = project_root.resolve()
    document = load_baseline(baseline_path, root)
    resolved_baseline_path = resolve_project_path(root, str(baseline_path))
    current_records = [
        _record_from_finding(result.engine_name, finding)
        for result in suite.results
        for finding in findings_for_result(result, project_root=root)
    ]
    current_groups: dict[tuple[str, str], list[_FindingRecord]] = defaultdict(list)
    baseline_groups: dict[tuple[str, str], list[_FindingRecord]] = defaultdict(list)
    for record in current_records:
        current_groups[(record.engine_name, record.fingerprint)].append(record)
    for record in document.findings:
        baseline_groups[(record.engine_name, record.fingerprint)].append(record)

    entries: list[FindingDelta] = []
    for key in sorted(set(current_groups) | set(baseline_groups)):
        entries.extend(_compare_group(current_groups[key], baseline_groups[key]))
    entries.sort(
        key=lambda entry: (
            not entry.gated,
            _STATE_ORDER[entry.state],
            entry.engine_name,
            entry.fingerprint,
            _delta_location_key(entry),
        )
    )
    try:
        source_path = resolved_baseline_path.relative_to(root).as_posix()
    except ValueError as err:
        raise BaselineError(f"baseline path is outside project root: {baseline_path}") from err
    gated_count = sum(1 for entry in entries if entry.gated)
    return BaselineComparison(
        source_path=source_path,
        entries=entries,
        warnings=_compatibility_warnings(document.metadata, current_metadata),
        baseline_metadata=document.metadata,
        fail_on_new=fail_on_new,
        gate_failed=fail_on_new and gated_count > 0,
    )
