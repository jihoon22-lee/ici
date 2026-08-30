"""Finding v3 construction, canonical paths, fingerprints, and legacy migration."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core.models import (
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
)

FINGERPRINT_VERSION = "ici-fingerprint/v1"

_CATEGORY_BY_ENGINE = {
    "build": FindingCategory.BUILD,
    "complexity": FindingCategory.MAINTAINABILITY,
    "cognitive": FindingCategory.MAINTAINABILITY,
    "cycle": FindingCategory.ARCHITECTURE,
    "dead": FindingCategory.MAINTAINABILITY,
    "dup": FindingCategory.MAINTAINABILITY,
    "exception": FindingCategory.CORRECTNESS,
    "line": FindingCategory.MAINTAINABILITY,
    "lint": FindingCategory.MAINTAINABILITY,
    "resource": FindingCategory.RESOURCE,
    "sanitize": FindingCategory.CORRECTNESS,
    "security": FindingCategory.SECURITY,
    "test": FindingCategory.TEST,
    "type": FindingCategory.TYPE,
}

_SEVERITY_BY_STATUS = {
    EngineStatus.PASS: FindingSeverity.INFO,
    EngineStatus.SKIP: FindingSeverity.INFO,
    EngineStatus.WARN: FindingSeverity.MEDIUM,
    EngineStatus.FAIL: FindingSeverity.HIGH,
    EngineStatus.ERROR: FindingSeverity.CRITICAL,
}

_METRIC_UNITS = {
    "branch_coverage": "percent",
    "coverage": "percent",
    "dup_pct": "percent",
    "function_coverage": "percent",
    "line_coverage": "percent",
    "lines": "lines",
    "missed": "lines",
    "nesting": "levels",
}


def _slash_path(value: str | Path) -> str:
    return str(value).replace("\\", "/")


def _comparison_value(value: str) -> str:
    # Windows paths are case-insensitive at least for the drive component. A
    # case-folded comparison also lets reports produced on Windows and WSL use
    # one canonical project-relative identity.
    return value.casefold() if re.match(r"^[A-Za-z]:/", value) else value


def _collapse_relative(value: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(value).parts:
        if part in ("", ".", "/"):
            continue
        if re.fullmatch(r"[A-Za-z]:", part):
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"path escapes the project root: {value!r}")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) or "."


def canonical_project_path(file_path: str | Path, project_root: str | Path | None = None) -> str:
    """Return a lexical, slash-separated project-relative path.

    No filesystem resolution is used, so a checkout path, symlink layout, or
    source file that no longer exists cannot change a finding identity.
    Absolute inputs require an explicit root and must stay inside it.
    """

    value = _slash_path(file_path).rstrip("/") or "."
    if re.match(r"^[A-Za-z]:(?!/)", value):
        raise ValueError(f"drive-relative finding path is ambiguous: {value!r}")
    root_value = _slash_path(project_root) if project_root is not None else ""
    root = root_value.rstrip("/") or ("/" if root_value.startswith("/") else "")
    is_absolute = value.startswith("/") or bool(re.match(r"^[A-Za-z]:/", value))

    if is_absolute:
        if not root:
            raise ValueError(f"absolute finding path requires project_root: {value!r}")
        value_cmp = _comparison_value(value)
        root_cmp = _comparison_value(root)
        if value_cmp == root_cmp:
            value = "."
        elif root == "/":
            value = value.lstrip("/")
        elif value_cmp.startswith(root_cmp + "/"):
            value = value[len(root) + 1 :]
        else:
            raise ValueError(f"finding path is outside project_root: {value!r}")

    return _collapse_relative(value)


def validate_source_region(
    *,
    start_line: int,
    end_line: int | None,
    start_column: int | None,
    end_column: int | None,
    context: str = "source location",
) -> None:
    """Enforce the 1-indexed region invariant promised by the v3 schema."""

    values = {
        "start_line": start_line,
        "end_line": end_line,
        "start_column": start_column,
        "end_column": end_column,
    }
    for name, value in values.items():
        if value is None and name != "start_line":
            continue
        if type(value) is not int or value < 1:
            raise ValueError(f"{context} {name} must be a 1-indexed integer: {value!r}")
    if end_line is not None and end_line < start_line:
        raise ValueError(
            f"{context} end_line must not precede start_line: {end_line} < {start_line}"
        )
    if (
        start_column is not None
        and end_column is not None
        and end_line in (None, start_line)
        and end_column < start_column
    ):
        raise ValueError(
            f"{context} end_column must not precede start_column on one line: "
            f"{end_column} < {start_column}"
        )


def finding_fingerprint(
    rule_id: str,
    location: SourceLocation,
    *,
    symbol: str = "",
) -> str:
    """Build a deterministic identity independent of checkout root and separators."""

    region: dict[str, int | None] | None = None
    if not symbol.strip():
        region = {
            "start_line": location.start_line,
            "end_line": location.end_line,
            "start_column": location.start_column,
            "end_column": location.end_column,
        }
    payload = {
        "version": FINGERPRINT_VERSION,
        "rule_id": rule_id,
        "path": location.path,
        "symbol": symbol.strip(),
        "region": region,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _legacy_rule_id(engine_name: str) -> str:
    engine = re.sub(r"[^a-z0-9]+", "-", engine_name.casefold()).strip("-") or "unknown"
    # This intentionally stays coarse. Native v3 producers can introduce a
    # finer ici rule namespace without turning a symbol/function name from the
    # legacy target into an unstable rule id.
    return f"ici.legacy.{engine}.target"


def _confidence(evidence: EvidenceState) -> FindingConfidence:
    if evidence == EvidenceState.MEASURED:
        return FindingConfidence.HIGH
    if evidence == EvidenceState.ESTIMATED:
        return FindingConfidence.MEDIUM
    return FindingConfidence.LOW


def _metric_unit(name: str) -> str:
    key = name.casefold()
    if key.endswith("_pct") or key.endswith("_percent"):
        return "percent"
    for fragment, unit in _METRIC_UNITS.items():
        if fragment in key:
            return unit
    return ""


def _numeric_metrics(values: dict[str, Any]) -> dict[str, FindingMetric]:
    metrics: dict[str, FindingMetric] = {}
    for name, value in sorted(values.items()):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            continue
        metrics[str(name)] = FindingMetric(value=value, unit=_metric_unit(str(name)))
    return metrics


def legacy_target_to_finding(
    result: EngineResult,
    target: InspectionTarget,
    project_root: str | Path | None = None,
    *,
    fingerprint_symbol: str | None = None,
) -> Finding:
    """Adapt one v2 InspectionTarget without inventing tool-specific semantics."""

    validate_source_region(
        start_line=target.start_line,
        end_line=target.end_line,
        start_column=target.start_column,
        end_column=target.end_column,
        context=f"legacy target {target.file_path!r}",
    )
    path = canonical_project_path(target.file_path, project_root)
    location = SourceLocation(
        path=path,
        start_line=target.start_line,
        end_line=target.end_line,
        start_column=target.start_column,
        end_column=target.end_column,
        label=target.target_name,
    )
    rule_id = _legacy_rule_id(result.engine_name)
    symbol = target.target_name if fingerprint_symbol is None else fingerprint_symbol
    tool = result.tool_evidence[0] if result.tool_evidence else None
    tool_rule_id = target.target_name if ":" in target.target_name else ""
    return Finding(
        rule_id=rule_id,
        category=_CATEGORY_BY_ENGINE.get(result.engine_name, FindingCategory.CORRECTNESS),
        severity=_SEVERITY_BY_STATUS[target.status],
        confidence=_confidence(result.evidence),
        fingerprint=finding_fingerprint(rule_id, location, symbol=symbol),
        primary_location=location,
        message=target.message,
        explanation=f"Adapted from the legacy {result.engine_name} InspectionTarget contract.",
        tool_rule_id=tool_rule_id,
        tool_name=tool.name if tool else "",
        tool_version=tool.version if tool else "",
        metrics=_numeric_metrics(target.metrics),
        snippet=target.snippet,
    )


def _canonical_location(
    location: SourceLocation, project_root: str | Path | None
) -> SourceLocation:
    validate_source_region(
        start_line=location.start_line,
        end_line=location.end_line,
        start_column=location.start_column,
        end_column=location.end_column,
        context=f"finding location {location.path!r}",
    )
    return replace(location, path=canonical_project_path(location.path, project_root))


def canonicalize_finding(finding: Finding, project_root: str | Path | None = None) -> Finding:
    """Normalize a native finding and derive its identity from canonical fields."""

    if not re.fullmatch(r"ici\.[a-z0-9][a-z0-9.-]*", finding.rule_id):
        raise ValueError(f"finding rule_id must use the ici namespace: {finding.rule_id!r}")
    primary = _canonical_location(finding.primary_location, project_root)
    related = sorted(
        (_canonical_location(location, project_root) for location in finding.related_locations),
        key=lambda location: (
            location.path,
            location.start_line,
            location.start_column or 0,
            location.end_line or 0,
            location.end_column or 0,
            location.label,
        ),
    )
    return replace(
        finding,
        primary_location=primary,
        related_locations=related,
        fingerprint=finding_fingerprint(
            finding.rule_id,
            primary,
            symbol=primary.label,
        ),
    )


def findings_for_result(
    result: EngineResult, project_root: str | Path | None = None
) -> list[Finding]:
    """Return native findings plus every non-duplicated legacy target adapter."""

    canonical_keys = [
        (canonical_project_path(target.file_path, project_root), target.target_name.strip())
        for target in result.targets
    ]
    key_counts = Counter(canonical_keys)
    adapted = [
        legacy_target_to_finding(
            result,
            target,
            project_root,
            # A legacy unqualified symbol is stable only when it uniquely
            # identifies a target in that file. Overloads, repeated pytest
            # parameters and clone occurrences retain their regions instead
            # of silently collapsing locations.
            fingerprint_symbol=(key[1] if key[1] and key_counts[key] == 1 else ""),
        )
        for target, key in zip(result.targets, canonical_keys, strict=True)
    ]

    # Native v3 data wins when it deliberately describes the same identity,
    # but adapters are otherwise a lossless one-finding-per-target migration.
    native = [canonicalize_finding(finding, project_root) for finding in result.findings]
    native_by_fingerprint = {finding.fingerprint: finding for finding in native}
    emitted_native: set[str] = set()
    findings: list[Finding] = []
    for finding in adapted:
        replacement = native_by_fingerprint.get(finding.fingerprint)
        if replacement is None:
            findings.append(finding)
        elif replacement.fingerprint not in emitted_native:
            findings.append(replacement)
            emitted_native.add(replacement.fingerprint)
    findings.extend(finding for finding in native if finding.fingerprint not in emitted_native)
    return sorted(
        findings,
        key=lambda finding: (
            finding.fingerprint,
            finding.primary_location.path,
            finding.primary_location.start_line,
            finding.message,
        ),
    )
