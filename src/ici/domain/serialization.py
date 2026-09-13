"""The external JSON contract for a run result, kept separable from the classes.

#200 asks for the wire format and the class structure to stay independent, so
this module owns the mapping and neither side reaches into the other. A field
can be renamed in a dataclass without breaking consumers, and the envelope can
grow a key without forcing a model change — the cost is that both directions
live here and are tested against each other.

Three properties matter more than convenience:

**Deterministic.** ``dumps`` sorts keys, uses fixed separators and rejects NaN
and infinity, so the same result serializes byte-for-byte the same way on any
run. That is what makes a digest over the output mean something and a golden
fixture diff readable.

**Loud about version.** Reading checks ``schema_id`` and ``schema_version``
before anything else and reports the value it actually found. SPEC-04 section 5
forbids a reader from turning an unrecognised result into a silent empty PASS;
refusing to guess is the first half of keeping that promise.

**No I/O.** Strings and dicts only. Files are ``ici.execution.results``; keeping
that out of here is what lets the domain purity test cover this module too.

The event codec is ``ici.domain.eventstream``; both share ``ici.domain._codec``.
"""

from __future__ import annotations

import json
from typing import Any

from ici.domain._codec import (
    SchemaError,
    UnsupportedSchemaError,
    check_envelope,
    dumps,
    read_enum,
    require_mapping,
)
from ici.domain.enums import EvidenceLevel, GateVerdict, PublicationState, ScopeKind
from ici.domain.finding import Finding, FindingSuppression, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.result import (
    SCHEMA_ID,
    SCHEMA_VERSION,
    ExecutionSummary,
    GateOutcome,
    Producer,
    PublicationOutcome,
    RunIdentity,
    RunResult,
    ScopeSelection,
)
from ici.domain.workspace import SourceSnapshot

__all__ = [
    "SchemaError",
    "UnsupportedSchemaError",
    "dumps",
    "loads",
    "run_result_from_dict",
    "run_result_to_dict",
]


# --- writing ------------------------------------------------------------


def _span_to_dict(span: SourceSpan) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": span.path, "start_line": span.start_line}
    for name in ("end_line", "start_column", "end_column"):
        value = getattr(span, name)
        if value is not None:
            payload[name] = value
    if span.label:
        payload["label"] = span.label
    return payload


def _finding_to_dict(finding: Finding) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "fingerprint": finding.fingerprint,
        "rule_id": finding.rule_id,
        "message": finding.message,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "provider": finding.provider,
        "evidence": finding.evidence.value,
        "primary_location": _span_to_dict(finding.primary_location),
    }
    if finding.related_locations:
        payload["related_locations"] = [_span_to_dict(item) for item in finding.related_locations]
    for name in ("native_rule_id", "rule_version", "category"):
        value = getattr(finding, name)
        if value:
            payload[name] = value
    if finding.tags:
        payload["tags"] = list(finding.tags)
    for name in ("component_id", "analysis_unit_id", "variant", "task_id"):
        value = getattr(finding, name)
        if value is not None:
            payload[name] = value
    if finding.suppression.suppressed:
        payload["suppression"] = {
            "suppressed": True,
            "kind": finding.suppression.kind,
            "reason": finding.suppression.reason,
            "origin": finding.suppression.origin,
        }
    if finding.limitations:
        payload["limitations"] = list(finding.limitations)
    return payload


def _measurement_to_dict(measurement: Measurement) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": measurement.name,
        "value": measurement.value,
        "evidence": measurement.evidence.value,
    }
    if measurement.unit:
        payload["unit"] = measurement.unit
    # Raw counts travel even when a ratio is present: SPEC-04 section 4 only
    # allows combining compatible numerators and denominators, which a consumer
    # cannot do from a percentage.
    for name in ("numerator", "denominator"):
        value = getattr(measurement, name)
        if value is not None:
            payload[name] = value
    return payload


def _snapshot_to_dict(snapshot: SourceSnapshot) -> dict[str, Any]:
    payload: dict[str, Any] = {"digest": snapshot.digest, "dirty": snapshot.dirty}
    for name in ("files", "generated", "external_inputs"):
        values = getattr(snapshot, name)
        if values:
            payload[name] = list(values)
    if snapshot.commit is not None:
        payload["commit"] = snapshot.commit
    return payload


def run_result_to_dict(result: RunResult) -> dict[str, Any]:
    """Render a run as the ``ici.next.run`` v1 envelope."""

    producer: dict[str, Any] = {"ici_version": result.producer.ici_version}
    if result.producer.bundle_digest is not None:
        producer["bundle_digest"] = result.producer.bundle_digest

    return {
        "schema_id": result.schema_id,
        "schema_version": result.schema_version,
        "run_id": result.run_id,
        "producer": producer,
        "identity": {
            "source": _snapshot_to_dict(result.identity.source),
            "policy_digest": result.identity.policy_digest,
            "toolchain_digest": result.identity.toolchain_digest,
        },
        "scope": {
            "kind": result.scope.kind.value,
            "selected_components": list(result.scope.selected_components),
            "selected_languages": list(result.scope.selected_languages),
            "required_components": list(result.scope.required_components),
            "omitted_components": list(result.scope.omitted_components),
            "full_required_satisfied": result.scope.full_required_satisfied,
        },
        "execution": {
            "required_complete": result.execution.required_complete,
            "cancelled": result.execution.cancelled,
            "blocked_task_ids": list(result.execution.blocked_task_ids),
            "failed_task_ids": list(result.execution.failed_task_ids),
        },
        "gate": {
            "selected": result.gate.selected.value,
            "workspace": result.gate.workspace.value,
            "has_violations": result.gate.has_violations,
            "reasons": list(result.gate.reasons),
        },
        "publication": {
            "state": result.publication.state.value,
            "detail": result.publication.detail,
        },
        "findings": [_finding_to_dict(item) for item in result.findings],
        "metrics": [_measurement_to_dict(item) for item in result.metrics],
        "limitations": list(result.limitations),
    }


# --- reading ------------------------------------------------------------


def _span_from_dict(payload: object) -> SourceSpan:
    data = require_mapping(payload, "location")
    return SourceSpan(
        path=data.get("path"),  # type: ignore[arg-type]
        start_line=data.get("start_line"),  # type: ignore[arg-type]
        end_line=data.get("end_line"),
        start_column=data.get("start_column"),
        end_column=data.get("end_column"),
        label=data.get("label", ""),
    )


def _suppression_from_dict(payload: object) -> FindingSuppression:
    if payload is None:
        return FindingSuppression()
    raw = require_mapping(payload, "finding suppression")
    return FindingSuppression(
        suppressed=bool(raw.get("suppressed", False)),
        kind=raw.get("kind", ""),
        reason=raw.get("reason", ""),
        origin=raw.get("origin", ""),
    )


def _finding_from_dict(payload: object) -> Finding:
    data = require_mapping(payload, "finding")
    return Finding(
        fingerprint=data.get("fingerprint"),  # type: ignore[arg-type]
        rule_id=data.get("rule_id"),  # type: ignore[arg-type]
        message=data.get("message"),  # type: ignore[arg-type]
        severity=data.get("severity"),  # type: ignore[arg-type]
        confidence=data.get("confidence"),  # type: ignore[arg-type]
        primary_location=_span_from_dict(data.get("primary_location")),
        provider=data.get("provider"),  # type: ignore[arg-type]
        native_rule_id=data.get("native_rule_id", ""),
        rule_version=data.get("rule_version", ""),
        category=data.get("category", ""),
        tags=tuple(data.get("tags", ())),
        related_locations=tuple(
            _span_from_dict(item) for item in data.get("related_locations", ())
        ),
        component_id=data.get("component_id"),
        analysis_unit_id=data.get("analysis_unit_id"),
        variant=data.get("variant"),
        task_id=data.get("task_id"),
        evidence=read_enum(data.get("evidence", "MEASURED"), EvidenceLevel, "finding evidence"),
        suppression=_suppression_from_dict(data.get("suppression")),
        limitations=tuple(data.get("limitations", ())),
    )


def _measurement_from_dict(payload: object) -> Measurement:
    data = require_mapping(payload, "metric")
    return Measurement(
        name=data.get("name"),  # type: ignore[arg-type]
        value=data.get("value"),  # type: ignore[arg-type]
        unit=data.get("unit", ""),
        numerator=data.get("numerator"),
        denominator=data.get("denominator"),
        evidence=read_enum(data.get("evidence", "MEASURED"), EvidenceLevel, "metric evidence"),
    )


def _identity_from_dict(payload: object) -> RunIdentity:
    data = require_mapping(payload, "run identity")
    snapshot = require_mapping(data.get("source"), "source snapshot")
    return RunIdentity(
        source=SourceSnapshot(
            digest=snapshot.get("digest"),  # type: ignore[arg-type]
            files=tuple(snapshot.get("files", ())),
            generated=tuple(snapshot.get("generated", ())),
            external_inputs=tuple(snapshot.get("external_inputs", ())),
            commit=snapshot.get("commit"),
            dirty=bool(snapshot.get("dirty", False)),
        ),
        policy_digest=data.get("policy_digest"),  # type: ignore[arg-type]
        toolchain_digest=data.get("toolchain_digest"),  # type: ignore[arg-type]
    )


def _scope_from_dict(payload: object) -> ScopeSelection:
    data = require_mapping(payload, "scope")
    return ScopeSelection(
        kind=read_enum(data.get("kind"), ScopeKind, "scope kind"),
        selected_components=tuple(data.get("selected_components", ())),
        selected_languages=tuple(data.get("selected_languages", ())),
        required_components=tuple(data.get("required_components", ())),
        omitted_components=tuple(data.get("omitted_components", ())),
        full_required_satisfied=bool(data.get("full_required_satisfied", False)),
    )


def _gate_from_dict(payload: object) -> GateOutcome:
    data = require_mapping(payload, "gate")
    return GateOutcome(
        selected=read_enum(data.get("selected"), GateVerdict, "selected gate"),
        workspace=read_enum(data.get("workspace", "NOT_EVALUATED"), GateVerdict, "workspace gate"),
        has_violations=bool(data.get("has_violations", False)),
        reasons=tuple(data.get("reasons", ())),
    )


def _publication_from_dict(payload: object) -> PublicationOutcome:
    if payload is None:
        return PublicationOutcome()
    data = require_mapping(payload, "publication")
    return PublicationOutcome(
        state=read_enum(data.get("state", "NOT_CONFIGURED"), PublicationState, "publication state"),
        detail=data.get("detail", ""),
    )


def run_result_from_dict(payload: object) -> RunResult:
    """Read an ``ici.next.run`` v1 envelope back into a ``RunResult``.

    Model validation runs as a side effect of construction, so a payload that
    parses but is internally inconsistent — a passing gate that also claims
    violations, say — is rejected here rather than flowing onward.
    """

    data = require_mapping(payload, "run result")
    check_envelope(data, SCHEMA_ID, SCHEMA_VERSION)
    execution = require_mapping(data.get("execution"), "execution")
    producer = require_mapping(data.get("producer"), "producer")

    try:
        return RunResult(
            run_id=data.get("run_id"),  # type: ignore[arg-type]
            producer=Producer(
                ici_version=producer.get("ici_version"),  # type: ignore[arg-type]
                bundle_digest=producer.get("bundle_digest"),
            ),
            identity=_identity_from_dict(data.get("identity")),
            scope=_scope_from_dict(data.get("scope")),
            execution=ExecutionSummary(
                required_complete=bool(execution.get("required_complete", False)),
                cancelled=bool(execution.get("cancelled", False)),
                blocked_task_ids=tuple(execution.get("blocked_task_ids", ())),
                failed_task_ids=tuple(execution.get("failed_task_ids", ())),
            ),
            gate=_gate_from_dict(data.get("gate")),
            findings=tuple(_finding_from_dict(item) for item in data.get("findings", ())),
            metrics=tuple(_measurement_from_dict(item) for item in data.get("metrics", ())),
            publication=_publication_from_dict(data.get("publication")),
            limitations=tuple(data.get("limitations", ())),
        )
    except SchemaError:
        raise
    except ValueError as err:
        # A model invariant rejected the payload. Surfacing it as SchemaError
        # keeps one exception type at the boundary while preserving the reason.
        raise SchemaError(f"result is not valid: {err}") from err


def loads(text: str) -> RunResult:
    """Parse result JSON text, with a diagnostic rather than a traceback."""

    try:
        payload = json.loads(text)
    except ValueError as err:
        raise SchemaError(f"result is not valid JSON: {err}") from err
    return run_result_from_dict(payload)
