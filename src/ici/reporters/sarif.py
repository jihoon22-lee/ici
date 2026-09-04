"""Deterministic SARIF 2.1.0 reporter for ici verification results.

The reporter deliberately consumes the same canonical finding projection as
the JSON, Markdown, and HTML reporters.  ``findings_for_result`` adapts legacy
``InspectionTarget`` values and merges native v3 findings, so SARIF consumers
see one stable result per finding rather than a second, reporter-specific
interpretation of an engine result.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ici import __version__
from ici.core.findings import canonicalize_finding, findings_for_result
from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    FindingSuppression,
    SourceLocation,
    SuppressionKind,
    VerificationSuiteResult,
    exit_code_for_status,
)
from ici.core.redaction import redact_suite
from ici.reporters.json_rep import _save_json

SARIF_SCHEMA_URL = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
MAX_SARIF_RESULTS = 100_000
MAX_SARIF_RULES = 10_000

_SEVERITY_RANK = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}
_SARIF_LEVEL = {
    FindingSeverity.INFO: "note",
    FindingSeverity.LOW: "note",
    FindingSeverity.MEDIUM: "warning",
    FindingSeverity.HIGH: "error",
    FindingSeverity.CRITICAL: "error",
}
_SARIF_BASELINE_STATE = {
    DeltaState.NEW: "new",
    DeltaState.UNCHANGED: "unchanged",
    # SARIF calls a location/severity change "updated".  ici's baseline
    # comparer calls the same relationship "moved" because its primary
    # identity is the source location.
    DeltaState.MOVED: "updated",
    DeltaState.RESOLVED: "absent",
}
_SARIF_SUPPRESSION_KIND = {
    "inline": "inSource",
    "config": "external",
    "baseline": "external",
}


class SarifBoundsError(ValueError):
    """Raised when a report cannot be represented within the SARIF limits."""


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _finding_severity(value: Any) -> FindingSeverity:
    if isinstance(value, FindingSeverity):
        return value
    try:
        return FindingSeverity(str(value))
    except ValueError:
        return FindingSeverity.INFO


def _delta_state(value: Any) -> DeltaState | None:
    if value is None:
        return None
    if isinstance(value, DeltaState):
        return value
    try:
        return DeltaState(str(value))
    except ValueError:
        return None


def _location_sort_key(location: SourceLocation | None) -> tuple[Any, ...]:
    if location is None:
        return ("", 0, 0, 0, 0, "")
    return (
        location.path,
        location.start_line,
        location.start_column or 0,
        location.end_line or 0,
        location.end_column or 0,
        location.label,
    )


def _location(location: SourceLocation) -> dict[str, Any]:
    """Serialize an ici source location as a SARIF physical location."""

    region: dict[str, int] = {"startLine": location.start_line}
    if location.end_line is not None:
        region["endLine"] = location.end_line
    if location.start_column is not None:
        region["startColumn"] = location.start_column
    if location.end_column is not None:
        region["endColumn"] = location.end_column
    physical: dict[str, Any] = {
        "artifactLocation": {
            "uri": quote(location.path, safe="/"),
            "uriBaseId": "%SRCROOT%",
        },
        "region": region,
    }
    return {"physicalLocation": physical}


def _related_locations(finding: Finding, delta: Any = None) -> list[dict[str, Any]]:
    related = []
    locations = [(location, location.label) for location in finding.related_locations]
    if (
        delta is not None
        and _delta_state(delta.state) == DeltaState.MOVED
        and delta.baseline_location is not None
        and delta.baseline_location != finding.primary_location
    ):
        locations.append((delta.baseline_location, "Baseline location before move"))
    for index, (location, label) in enumerate(locations, start=1):
        item = _location(location)
        item["id"] = index
        if label:
            item["message"] = {"text": label}
        related.append(item)
    return related


def _suppression(finding: Finding) -> list[dict[str, str]]:
    suppression = finding.suppression
    if not suppression.suppressed:
        return []
    kind = _SARIF_SUPPRESSION_KIND.get(_enum_value(suppression.kind), "external")
    item: dict[str, str] = {"kind": kind}
    if suppression.reason:
        item["justification"] = suppression.reason
    return [item]


def _finding_properties(
    finding: Finding,
    engine_name: str,
    delta: Any = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "engine": engine_name,
        "category": _enum_value(finding.category),
        "confidence": _enum_value(finding.confidence),
        "suppressed": bool(finding.suppression.suppressed),
    }
    if finding.tool_name:
        properties["tool"] = finding.tool_name
    if finding.tool_rule_id:
        properties["tool_rule_id"] = finding.tool_rule_id
    if finding.tool_version:
        properties["tool_version"] = finding.tool_version
    if delta is not None:
        properties["delta_state"] = _enum_value(delta.state)
        properties["delta_regressed"] = bool(delta.regressed)
        properties["delta_gated"] = bool(delta.gated)
    return properties


def _rule_level(severity: FindingSeverity) -> str:
    return _SARIF_LEVEL[severity]


def _rule_for_finding(finding: Finding) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": finding.rule_id,
        "name": finding.rule_id,
        "shortDescription": {"text": finding.rule_id},
        "defaultConfiguration": {"level": _rule_level(_finding_severity(finding.severity))},
        "properties": {
            "category": _enum_value(finding.category),
            "confidence": _enum_value(finding.confidence),
        },
    }
    if finding.explanation:
        rule["fullDescription"] = {"text": finding.explanation}
    if finding.remediation:
        rule["help"] = {"text": finding.remediation}
    if finding.tool_name:
        rule["properties"]["tool"] = finding.tool_name
    return rule


def _result_for_finding(
    finding: Finding,
    engine_name: str,
    delta: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ruleId": finding.rule_id,
        "level": _SARIF_LEVEL[_finding_severity(finding.severity)],
        "message": {"text": finding.message or finding.rule_id},
        "locations": [_location(finding.primary_location)],
        "fingerprints": {"ici/v3": finding.fingerprint},
        "properties": _finding_properties(finding, engine_name, delta),
    }
    related = _related_locations(finding, delta)
    if related:
        result["relatedLocations"] = related
    suppressions = _suppression(finding)
    if suppressions:
        result["suppressions"] = suppressions
    if delta is not None:
        state = _delta_state(delta.state)
        if state is not None:
            result["baselineState"] = _SARIF_BASELINE_STATE[state]
    return result


def _resolved_finding(
    delta: Any,
    project_root: str | Path | None,
) -> Finding | None:
    """Build the minimal canonical finding needed for an absent result."""

    location = delta.baseline_location
    if location is None:
        return None
    severity = _finding_severity(delta.baseline_severity)
    suppressed = bool(delta.suppressed)
    finding = Finding(
        rule_id=delta.rule_id,
        category=FindingCategory.CORRECTNESS,
        severity=severity,
        confidence=FindingConfidence.LOW,
        fingerprint=delta.fingerprint,
        primary_location=location,
        message=delta.message,
        suppression=FindingSuppression(
            suppressed=suppressed,
            kind=SuppressionKind.BASELINE if suppressed else SuppressionKind.NONE,
        ),
    )
    return canonicalize_finding(finding, project_root=project_root)


def _finding_sort_key(engine_name: str, finding: Finding) -> tuple[Any, ...]:
    """Order every field that can affect a SARIF rule or result."""

    suppression = finding.suppression
    metrics = tuple(
        (name, repr(metric.value), metric.unit) for name, metric in sorted(finding.metrics.items())
    )
    return (
        engine_name,
        finding.rule_id,
        finding.fingerprint,
        _location_sort_key(finding.primary_location),
        finding.message,
        _enum_value(finding.severity),
        _enum_value(finding.category),
        _enum_value(finding.confidence),
        finding.explanation,
        finding.remediation,
        finding.tool_name,
        finding.tool_rule_id,
        finding.tool_version,
        bool(suppression.suppressed),
        _enum_value(suppression.kind),
        suppression.reason,
        tuple(_location_sort_key(location) for location in finding.related_locations),
        metrics,
        finding.snippet,
    )


def _delta_sort_key(delta: Any) -> tuple[Any, ...]:
    if delta is None:
        return ("", "", "", (), (), "", "", False, False, False)
    return (
        str(delta.engine_name),
        str(delta.fingerprint),
        _enum_value(delta.state),
        _location_sort_key(delta.current_location),
        _location_sort_key(delta.baseline_location),
        str(delta.message),
        _enum_value(delta.current_severity) if delta.current_severity is not None else "",
        _enum_value(delta.baseline_severity) if delta.baseline_severity is not None else "",
        bool(delta.regressed),
        bool(delta.suppressed),
        bool(delta.gated),
    )


def _current_delta_key(delta: Any) -> tuple[Any, ...]:
    return (
        str(delta.engine_name),
        str(delta.fingerprint),
        _location_sort_key(delta.current_location),
        str(delta.message),
        _enum_value(delta.current_severity) if delta.current_severity is not None else "",
    )


def _all_findings(
    suite: VerificationSuiteResult,
    project_root: str | Path | None,
) -> tuple[list[tuple[str, Finding, Any]], BaselineComparison | None]:
    records: list[tuple[str, Finding, Any]] = []
    baseline = suite.baseline_comparison
    delta_by_key: dict[tuple[Any, ...], deque[Any]] = defaultdict(deque)
    resolved: list[Any] = []
    if baseline is not None:
        for delta in sorted(baseline.entries, key=_delta_sort_key):
            state = _delta_state(delta.state)
            if state == DeltaState.RESOLVED:
                resolved.append(delta)
            elif delta.current_location is not None:
                delta_by_key[_current_delta_key(delta)].append(delta)

    current: list[tuple[str, Finding]] = []
    for result in suite.results:
        engine_name = str(result.engine_name)
        for finding in findings_for_result(result, project_root=project_root):
            current.append((engine_name, finding))
    current.sort(key=lambda item: _finding_sort_key(item[0], item[1]))
    for engine_name, finding in current:
        candidates = delta_by_key[
            (
                engine_name,
                finding.fingerprint,
                _location_sort_key(finding.primary_location),
                finding.message,
                _enum_value(finding.severity),
            )
        ]
        current_delta = candidates.popleft() if candidates else None
        records.append((engine_name, finding, current_delta))

    for delta in resolved:
        resolved_finding = _resolved_finding(delta, project_root)
        if resolved_finding is not None:
            records.append((str(delta.engine_name), resolved_finding, delta))

    records.sort(key=lambda item: (*_finding_sort_key(item[0], item[1]), _delta_sort_key(item[2])))
    return records, baseline


def _rules(findings: list[tuple[str, Finding, Any]]) -> list[dict[str, Any]]:
    by_rule: dict[str, Finding] = {}
    for _engine_name, finding, _delta in findings:
        current = by_rule.get(finding.rule_id)
        if current is None:
            by_rule[finding.rule_id] = finding
            continue
        current_severity = _finding_severity(current.severity)
        severity = _finding_severity(finding.severity)
        if _SEVERITY_RANK[severity] > _SEVERITY_RANK[current_severity]:
            by_rule[finding.rule_id] = finding
    ordered = [by_rule[key] for key in sorted(by_rule)]
    if len(ordered) > MAX_SARIF_RULES:
        raise SarifBoundsError(f"SARIF rule count exceeds the bounded limit of {MAX_SARIF_RULES}")
    return [_rule_for_finding(finding) for finding in ordered]


def _run_properties(
    suite: VerificationSuiteResult,
    baseline: BaselineComparison | None,
    finding_count: int,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "suite_status": _enum_value(suite.suite_status),
        "engine_count": len(suite.results),
        "finding_count": finding_count,
    }
    if baseline is not None:
        properties["baseline_source"] = baseline.source_path
        properties["baseline_gate_failed"] = bool(baseline.gate_failed)
    return properties


def serialize_sarif(
    suite: VerificationSuiteResult,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Return a deterministic SARIF 2.1.0 document for ``suite``.

    The result and rule limits are hard safety bounds.  A caller must choose
    another report format (the v3 JSON report retains the complete inventory)
    rather than receive a silently truncated SARIF document.
    """

    safe = redact_suite(suite)
    records, baseline = _all_findings(safe, project_root)
    if len(records) > MAX_SARIF_RESULTS:
        raise SarifBoundsError(
            f"SARIF result count exceeds the bounded limit of {MAX_SARIF_RESULTS}"
        )
    rules = _rules(records)
    results = [
        _result_for_finding(finding, engine_name, delta) for engine_name, finding, delta in records
    ]
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "ici",
                "version": __version__,
                "rules": rules,
            }
        },
        "results": results,
        "invocations": [
            {
                "executionSuccessful": safe.suite_status not in (EngineStatus.ERROR,),
                "exitCode": exit_code_for_status(safe.suite_status),
            }
        ],
        "properties": _run_properties(safe, baseline, len(records)),
    }
    return {
        "$schema": SARIF_SCHEMA_URL,
        "version": SARIF_VERSION,
        "runs": [run],
    }


def save_sarif_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_root: str | Path | None = None,
) -> None:
    """Serialize a suite and atomically write its SARIF report."""

    _save_json(serialize_sarif(suite, project_root=project_root), output_path)


def generate_sarif_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_root: str | Path | None = None,
) -> None:
    """Compatibility alias matching the HTML reporter's generator spelling."""

    save_sarif_report(suite, output_path, project_root=project_root)


__all__ = [
    "MAX_SARIF_RESULTS",
    "MAX_SARIF_RULES",
    "SARIF_SCHEMA_URL",
    "SARIF_VERSION",
    "SarifBoundsError",
    "generate_sarif_report",
    "save_sarif_report",
    "serialize_sarif",
]
