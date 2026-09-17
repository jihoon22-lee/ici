"""SARIF 2.1.0 export of a stored ``ici.next.run`` result (#221 PR C).

The same rule as the HTML page applies: this is a rendering of what was
saved, never a re-measurement. A finding keeps the fingerprint, the rule id,
the provider and the location the run recorded — SARIF consumers that diff
results need the identity to be exactly what the envelope carried, not a
re-computation that could disagree with it.

Two mappings are deliberately honest rather than complete:

- ``baselineState`` is only set for fingerprints the comparison named
  ``new`` or ``unchanged``. ``resolved`` findings are absent from this run's
  results — that *is* what resolved means — and ``carried`` findings were
  never in this run at all. The full delta stays readable under
  ``run.properties.baseline``.
- A suppressed finding keeps its mark as a SARIF suppression with
  ``kind: "external"`` and the declared reason — SARIF has no finer channel
  for "the workspace policy accepts this", and flattening it away would hide
  the finding from consumers that count suppressions.
"""

from __future__ import annotations

from typing import Any

from ici.domain.enums import BaselineState
from ici.domain.finding import Finding
from ici.domain.result import RunResult

__all__ = ["document"]

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
MAX_RESULTS = 100_000

_LEVEL = {
    "critical": "error",
    "fatal": "error",
    "error": "error",
    "high": "error",
    "warning": "warning",
    "medium": "warning",
    "low": "note",
    "note": "note",
    "info": "note",
    "information": "note",
    "none": "none",
}


class SarifBoundsError(ValueError):
    """The result cannot be represented within the SARIF limits."""


def document(result: RunResult) -> dict[str, Any]:
    """Serialize one stored result as a SARIF log — pure, deterministic."""

    if len(result.findings) > MAX_RESULTS:
        raise SarifBoundsError(
            f"{len(result.findings)} findings exceed the SARIF result bound of {MAX_RESULTS}"
        )
    baseline_state = _baseline_states(result)
    run: dict[str, Any] = {
        "tool": {
            "driver": {
                "name": "ici",
                "version": result.producer.ici_version,
                "informationUri": "https://github.com/jihoon22-lee/ici",
                "rules": _rules(result.findings),
            }
        },
        "invocations": [
            {
                "executionSuccessful": result.execution.required_complete,
                "exitCode": result.gate.exit_code,
                "properties": {"limitations": list(result.limitations)},
            }
        ],
        "columnKind": "utf16CodeUnits",
        "results": [
            _result(finding, baseline_state.get(finding.fingerprint)) for finding in result.findings
        ],
        "properties": {
            "ici.schemaId": result.schema_id,
            "policyDigest": result.identity.policy_digest,
            "toolchainDigest": result.identity.toolchain_digest,
            "sourceDigest": result.identity.source.digest,
            "scopeKind": result.scope.kind.value,
            "selectedComponents": list(result.scope.selected_components),
            "gate": result.gate.selected.value,
            "baseline": _baseline_summary(result),
        },
    }
    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [run],
    }


def _rules(findings: tuple[Finding, ...]) -> list[dict[str, Any]]:
    """One SARIF rule per rule id the run reported, deterministically ordered."""

    rules: dict[str, Finding] = {}
    for finding in findings:
        rules.setdefault(finding.rule_id, finding)
    return [
        {
            "id": rule_id,
            "properties": {
                "provider": rules[rule_id].provider,
                "nativeRuleId": rules[rule_id].native_rule_id,
                "tags": list(rules[rule_id].tags),
            },
            "defaultConfiguration": {"level": _level(rules[rule_id])},
        }
        for rule_id in sorted(rules)
    ]


def _result(finding: Finding, baseline_state: str | None) -> dict[str, Any]:
    span = finding.primary_location
    region: dict[str, Any] = {"startLine": span.start_line}
    if span.start_column is not None:
        region["startColumn"] = span.start_column
    if span.end_line is not None:
        region["endLine"] = span.end_line
    if span.end_column is not None:
        region["endColumn"] = span.end_column
    entry: dict[str, Any] = {
        "ruleId": finding.rule_id,
        "level": _level(finding),
        "message": {"text": finding.message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": span.path,
                        "uriBaseId": "SRCROOT",
                    },
                    "region": region,
                }
            }
        ],
        "partialFingerprints": {"ici.fingerprint": finding.fingerprint},
        "properties": {
            "provider": finding.provider,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "evidence": finding.evidence.value,
            "nativeRuleId": finding.native_rule_id,
            "componentId": finding.component_id,
            "analysisUnitId": finding.analysis_unit_id,
            "variant": finding.variant,
            "taskId": finding.task_id,
            "category": finding.category,
            "tags": list(finding.tags),
            "limitations": list(finding.limitations),
        },
    }
    if baseline_state is not None:
        entry["baselineState"] = baseline_state
    if finding.suppression.suppressed:
        entry["suppressions"] = [
            {
                "kind": "external",
                "justification": finding.suppression.reason,
                "properties": {"origin": finding.suppression.origin},
            }
        ]
    return entry


def _level(finding: Finding) -> str:
    # The raw severity stays in properties; the level is only the SARIF
    # bucket, and an unknown severity is a warning rather than a guess.
    return _LEVEL.get(finding.severity, "warning")


def _baseline_states(result: RunResult) -> dict[str, str]:
    if result.baseline is None or result.baseline.state is not BaselineState.COMPARABLE:
        return {}
    states = {fingerprint: "new" for fingerprint in result.baseline.new}
    states.update({fingerprint: "unchanged" for fingerprint in result.baseline.unchanged})
    return states


def _baseline_summary(result: RunResult) -> dict[str, Any]:
    if result.baseline is None:
        return {"state": "none"}
    delta = result.baseline
    summary: dict[str, Any] = {"state": delta.state.value, "origin": delta.origin}
    if delta.state is BaselineState.COMPARABLE:
        summary["counts"] = {
            "new": len(delta.new),
            "unchanged": len(delta.unchanged),
            "resolved": len(delta.resolved),
            "carried": len(delta.carried),
        }
    else:
        summary["reason"] = delta.reason
    return summary
