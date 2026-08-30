"""Central output-boundary redaction for reports and returned verification results."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from ici.core.models import (
    BaselineComparison,
    EngineResult,
    Finding,
    FindingDelta,
    FindingMetric,
    FindingSuppression,
    SourceLocation,
    SupportMatrix,
    VerificationSuiteResult,
)

REDACTED = "***REDACTED***"

_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)"
    r"(?P<op>\s*[=:]\s*)(?P<quote>[\"'])(?P<value>[^\r\n]*?)(?P=quote)"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>password|passwd|secret|client[_-]?secret|api[_-]?key|access[_-]?key|auth[_-]?token|token)"
    r"(?P<op>\s*[=:]\s*)(?P<value>[^\s,;\"']+)"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?P<kind>[A-Z ]*PRIVATE KEY)-----.*?"
    r"(?:-----END (?P=kind)-----|\Z)",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_FLAG_VALUE_RE = re.compile(
    r"(?i)(--?(?:password|passwd|secret|token|api[_-]?key)(?:=|\s+))([^\s]+)"
)
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"
)
_SECRET_FLAGS = {
    "--api-key",
    "--api_key",
    "--password",
    "--passwd",
    "--secret",
    "--token",
}


def _mask_assignment(match: re.Match[str]) -> str:
    quote = match.groupdict().get("quote", "")
    return f"{match.group('key')}{match.group('op')}{quote}{REDACTED}{quote}"


def redact_text(value: str) -> str:
    """Mask common credential forms while leaving diagnostic structure intact."""

    if not isinstance(value, str):
        raise ValueError(f"redaction input must be a string: {value!r}")
    masked = _QUOTED_SECRET_ASSIGNMENT_RE.sub(_mask_assignment, value)
    masked = _SECRET_ASSIGNMENT_RE.sub(_mask_assignment, masked)
    masked = _PRIVATE_KEY_RE.sub("-----BEGIN [REDACTED] PRIVATE KEY-----", masked)
    masked = _BEARER_RE.sub(rf"\1{REDACTED}", masked)
    masked = _FLAG_VALUE_RE.sub(rf"\1{REDACTED}", masked)
    return _KNOWN_TOKEN_RE.sub(REDACTED, masked)


def redact_data(value: Any) -> Any:
    """Recursively redact strings in reporter metadata without changing its shape."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            base_key = redact_text(str(key))
            safe_key = base_key
            suffix = 2
            while safe_key in redacted:
                safe_key = f"{base_key}#{suffix}"
                suffix += 1
            redacted[safe_key] = redact_data(item)
        return redacted
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    return value


def _redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    hide_next = False
    for value in argv:
        if hide_next:
            redacted.append(REDACTED)
            hide_next = False
            continue
        redacted_value = redact_text(value)
        redacted.append(redacted_value)
        hide_next = value.casefold() in _SECRET_FLAGS
    return redacted


def _redact_location(location: SourceLocation) -> SourceLocation:
    return replace(
        location,
        path=redact_text(location.path),
        label=redact_text(location.label),
    )


def _redact_finding_metrics(metrics: dict[str, FindingMetric]) -> dict[str, FindingMetric]:
    redacted: dict[str, FindingMetric] = {}
    for name, metric in metrics.items():
        base_name = redact_text(name)
        safe_name = base_name
        suffix = 2
        while safe_name in redacted:
            safe_name = f"{base_name}#{suffix}"
            suffix += 1
        redacted[safe_name] = replace(metric, unit=redact_text(metric.unit))
    return redacted


def _redact_finding(finding: Finding) -> Finding:
    suppression = FindingSuppression(
        suppressed=finding.suppression.suppressed,
        kind=finding.suppression.kind,
        reason=redact_text(finding.suppression.reason),
    )
    return replace(
        finding,
        primary_location=_redact_location(finding.primary_location),
        related_locations=[_redact_location(item) for item in finding.related_locations],
        message=redact_text(finding.message),
        explanation=redact_text(finding.explanation),
        remediation=redact_text(finding.remediation),
        tool_rule_id=redact_text(finding.tool_rule_id),
        tool_name=redact_text(finding.tool_name),
        tool_version=redact_text(finding.tool_version),
        snippet=redact_text(finding.snippet),
        suppression=suppression,
        metrics=_redact_finding_metrics(finding.metrics),
    )


def _redact_support_matrix(matrix: SupportMatrix | None) -> SupportMatrix | None:
    if matrix is None:
        return None
    entries = [
        replace(
            entry,
            engine_name=redact_text(entry.engine_name),
            frameworks=[redact_text(value) for value in entry.frameworks],
            required_tools=[redact_text(value) for value in entry.required_tools],
            optional_tools=[redact_text(value) for value in entry.optional_tools],
            limitations=[redact_text(value) for value in entry.limitations],
            reason=redact_text(entry.reason),
        )
        for entry in matrix.entries
    ]
    return replace(
        matrix,
        project_frameworks=[redact_text(value) for value in matrix.project_frameworks],
        entries=entries,
    )


def _redact_delta(delta: FindingDelta) -> FindingDelta:
    return replace(
        delta,
        engine_name=redact_text(delta.engine_name),
        rule_id=redact_text(delta.rule_id),
        message=redact_text(delta.message),
        current_location=(
            _redact_location(delta.current_location) if delta.current_location is not None else None
        ),
        baseline_location=(
            _redact_location(delta.baseline_location)
            if delta.baseline_location is not None
            else None
        ),
    )


def _redact_baseline(
    comparison: BaselineComparison | None,
) -> BaselineComparison | None:
    if comparison is None:
        return None
    return replace(
        comparison,
        source_path=redact_text(comparison.source_path),
        warnings=[redact_text(warning) for warning in comparison.warnings],
        entries=[_redact_delta(entry) for entry in comparison.entries],
        baseline_metadata=(
            replace(
                comparison.baseline_metadata,
                producer_version=redact_text(comparison.baseline_metadata.producer_version),
                fingerprint_version=redact_text(comparison.baseline_metadata.fingerprint_version),
                policy_digest=redact_text(comparison.baseline_metadata.policy_digest),
                tool_policy_digest=redact_text(comparison.baseline_metadata.tool_policy_digest),
            )
            if comparison.baseline_metadata is not None
            else None
        ),
    )


def redact_engine_result(result: EngineResult) -> EngineResult:
    """Return a reporting-safe copy of an engine result."""

    targets = [
        replace(
            target,
            file_path=redact_text(target.file_path),
            target_name=redact_text(target.target_name),
            message=redact_text(target.message),
            snippet=redact_text(target.snippet),
            metrics=redact_data(target.metrics),
        )
        for target in result.targets
    ]
    tools = [
        replace(
            tool,
            name=redact_text(tool.name),
            path=redact_text(tool.path),
            version=redact_text(tool.version),
            argv=_redact_argv(tool.argv),
            error=redact_text(tool.error),
        )
        for tool in result.tool_evidence
    ]
    return replace(
        result,
        summary=redact_text(result.summary),
        targets=targets,
        raw_output=redact_text(result.raw_output),
        extra=redact_data(result.extra),
        tool_evidence=tools,
        findings=[_redact_finding(finding) for finding in result.findings],
        support_matrix=_redact_support_matrix(result.support_matrix),
    )


def redact_suite(suite: VerificationSuiteResult) -> VerificationSuiteResult:
    """Return a reporting-safe suite shared by every output format."""

    return replace(
        suite,
        results=[redact_engine_result(result) for result in suite.results],
        support_matrix=_redact_support_matrix(suite.support_matrix),
        baseline_comparison=_redact_baseline(suite.baseline_comparison),
    )
