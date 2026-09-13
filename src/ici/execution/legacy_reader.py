"""Read an existing ``ici.result/v3`` report into the next domain model.

This is the half of the v3 boundary that reads. ``ici.domain.legacy`` converts
one finding at a time; this module converts a whole document, which is where
the interesting losses live.

The constraint that shapes the whole module is that **a v3 report cannot become
a comparable run result on its own.** ``RunIdentity`` requires a
``SourceSnapshot``, whose ``digest`` covers the content the analysis actually
read, and v3 never recorded such a digest: it has a config digest, a toolchain
digest and a commit id, and SPEC-02 section 6 is explicit that a commit id does
not describe dirty files, generated inputs or external headers. Filling that
field with one of the digests v3 does have would make two unrelated runs look
comparable, and a baseline comparison would then report "no change" between
them.

So the reader returns a ``LegacyReport``: everything a v3 document can honestly
supply, and nothing more. A caller that can compute the snapshot — because it
has the working tree the report describes — passes it to :func:`promote` to get
a ``RunResult``. This is the same shape as ``finding_from_legacy``, which makes
its caller supply the provider rather than guessing one: the component that
knows says so, and the conversion never invents.

Losses that are merely losses, rather than refusals, are named in
``limitations`` so that a caller can report the gap instead of presenting an
empty value as an answer, which is what SPEC-04 section 5 and #200's
"no silent empty PASS" criterion require.

``docs/design/ici-next/compatibility-v3-next.md`` is the table this module
implements; ``tests/test_legacy_reader.py`` keeps the two in step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ici.domain._codec import SchemaError
from ici.domain.enums import EvidenceLevel, GateVerdict, ScopeKind
from ici.domain.finding import Finding, FindingSuppression, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.result import (
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
    "LEGACY_SCHEMA_VERSION",
    "LegacyReadError",
    "LegacyReport",
    "promote",
    "read_legacy_document",
    "read_legacy_report",
]

LEGACY_SCHEMA_VERSION = "ici.result/v3"

# Where each digest can be read from, in the order the reader tries. The
# ``analysis_context.identity`` record describes the run; ``analysis_metadata``
# describes the producer. Either will do, and both being absent is a refusal.
_POLICY_DIGEST_SOURCES = (
    ("analysis_metadata", "policy_digest"),
    ("analysis_context", "identity", "config_digest"),
)
_TOOLCHAIN_DIGEST_SOURCES = (
    ("analysis_context", "identity", "toolchain_digest"),
    ("analysis_metadata", "tool_policy_digest"),
)

# Recorded on every conversion: these are structural gaps in v3, not properties
# of a particular report.
_STRUCTURAL_LIMITATIONS = (
    "no source snapshot: v3 records no digest of the content it read, so this "
    "report cannot be compared against another run without a snapshot supplied "
    "by the caller",
    "no scope: v3 has no components, so an imported run is STANDALONE and never "
    "stands in for a workspace verdict",
    "no task identities: v3 records engines, so blocked and failed work cannot be named per task",
    "no component or analysis-unit attribution on any finding",
)


class LegacyReadError(SchemaError):
    """A v3 document cannot be represented, and why.

    A subclass of ``SchemaError`` so a caller reading both formats can catch one
    type, while still being able to tell the legacy boundary apart when it wants
    to say "this old report is missing X".
    """


@dataclass(frozen=True)
class LegacyReport:
    """Everything a v3 document can supply, with its identity left open.

    There is no ``RunIdentity`` here and that is the point: see the module
    docstring. ``policy_digest`` and ``toolchain_digest`` are carried separately
    because v3 does record those two honestly.
    """

    producer: Producer
    gate: GateOutcome
    execution: ExecutionSummary
    policy_digest: str
    toolchain_digest: str
    findings: tuple[Finding, ...] = ()
    metrics: tuple[Measurement, ...] = ()
    commit: str | None = None
    limitations: tuple[str, ...] = ()


def _dig(payload: Any, *keys: str) -> Any:
    """Follow a key path, returning None at the first missing or non-mapping."""

    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_digest(
    document: dict[str, Any], paths: tuple[tuple[str, ...], ...], description: str
) -> str:
    """Return the first digest present, or refuse naming where it looked."""

    for path in paths:
        value = _dig(document, *path)
        if isinstance(value, str) and value:
            return value
    readable = " or ".join(".".join(path) for path in paths)
    raise LegacyReadError(f"this v3 report has no {description} ({readable})")


def _span(value: Any) -> SourceSpan:
    location = value if isinstance(value, dict) else {}
    path = location.get("path")
    start = location.get("start_line")
    return SourceSpan(
        path=path if isinstance(path, str) and path else "<unknown>",
        start_line=start if isinstance(start, int) and start > 0 else 1,
        end_line=location.get("end_line"),
        start_column=location.get("start_column"),
        end_column=location.get("end_column"),
        label=location.get("label") or "",
    )


def _finding(value: dict[str, Any], provider: str) -> Finding:
    """Lift one v3 finding, attributing it to the engine that reported it.

    ``provider`` is the engine name rather than v3's ``tool_name``: ``tool_name``
    is the executable, and several engines share one executable, so attributing
    by it would merge distinct providers.
    """

    suppression = value.get("suppression")
    suppression = suppression if isinstance(suppression, dict) else {}
    return Finding(
        fingerprint=str(value.get("fingerprint") or ""),
        rule_id=str(value.get("rule_id") or ""),
        message=str(value.get("message") or ""),
        severity=str(value.get("severity") or ""),
        confidence=str(value.get("confidence") or ""),
        primary_location=_span(value.get("primary_location")),
        provider=provider,
        native_rule_id=str(value.get("tool_rule_id") or ""),
        rule_version=str(value.get("tool_version") or ""),
        category=str(value.get("category") or ""),
        related_locations=tuple(
            _span(item) for item in value.get("related_locations") or () if isinstance(item, dict)
        ),
        suppression=FindingSuppression(
            suppressed=bool(suppression.get("suppressed", False)),
            kind=str(suppression.get("kind") or ""),
            reason=str(suppression.get("reason") or ""),
            origin="legacy-v3",
        ),
    )


def _gate(suite_status: str, losses: list[str]) -> GateOutcome:
    """Map a v3 suite status onto the gate axes.

    ERROR becomes INCOMPLETE rather than FAIL: an engine that could not run is
    not a verdict about the code, and keeping those apart is the reason the axes
    were split in the first place.

    WARN is the lossy one. It becomes PASS, because the model forbids a passing
    scope from also reporting violations and v3's WARN means exactly "findings
    exist, none of them fail the gate". The findings survive in ``findings``;
    what is lost is v3's three-way suite distinction, so it is recorded.
    """

    if suite_status == "PASS":
        return GateOutcome(selected=GateVerdict.PASS)
    if suite_status == "WARN":
        losses.append(
            "suite_status WARN became PASS: the next gate is two-valued, and the "
            "findings that made the suite WARN are carried in findings"
        )
        return GateOutcome(selected=GateVerdict.PASS)
    if suite_status == "FAIL":
        return GateOutcome(
            selected=GateVerdict.FAIL,
            has_violations=True,
            reasons=("v3 suite_status was FAIL",),
        )
    if suite_status == "ERROR":
        return GateOutcome(
            selected=GateVerdict.INCOMPLETE,
            reasons=("v3 suite_status was ERROR: at least one engine could not run",),
        )
    if suite_status == "SKIP":
        return GateOutcome(selected=GateVerdict.NOT_EVALUATED)
    raise LegacyReadError(
        f"unknown v3 suite_status {suite_status!r}; refusing rather than guessing a verdict"
    )


def _metrics(document: dict[str, Any]) -> tuple[Measurement, ...]:
    """Carry the suite TEM score, and nothing else.

    Per-engine scores stay behind: v3 gives them no unit and no denominator, so
    promoting them to ``Measurement`` would attach a precision the source never
    had.
    """

    tem = document.get("tem_score")
    if not isinstance(tem, (int, float)) or isinstance(tem, bool):
        return ()
    return (
        Measurement(
            name="tem_score", value=float(tem), unit="score", evidence=EvidenceLevel.MEASURED
        ),
    )


def _engine_findings(engines: list[Any], losses: list[str]) -> tuple[list[Finding], list[str]]:
    """Collect findings per engine and the names of engines that errored."""

    findings: list[Finding] = []
    errored: list[str] = []
    for entry in engines:
        if not isinstance(entry, dict):
            raise LegacyReadError("every entry of a v3 'results' list must be an object")
        name = str(entry.get("engine_name") or "")
        if not name:
            raise LegacyReadError("a v3 engine result has no engine_name to attribute it to")
        if entry.get("status") == "ERROR":
            errored.append(name)
        raw = entry.get("findings")
        if raw is None:
            losses.append(f"engine {name} reports no findings list, so its detail is unavailable")
            continue
        if not isinstance(raw, list):
            raise LegacyReadError(f"engine {name} has a non-list 'findings'")
        findings.extend(_finding(item, provider=name) for item in raw if isinstance(item, dict))
    return findings, errored


def read_legacy_document(document: Any) -> LegacyReport:
    """Convert a parsed v3 suite payload, or raise ``LegacyReadError``."""

    try:
        return _convert(document)
    except LegacyReadError:
        raise
    except ValueError as err:
        # The domain models validate on construction, and a v3 document that
        # conforms to its own schema always satisfies them. One that does not is
        # still a legacy-boundary problem, so it leaves through the same door
        # with the same type instead of a bare ValueError from three frames down.
        raise LegacyReadError(f"this v3 report does not fit the domain model: {err}") from err


def _convert(document: Any) -> LegacyReport:
    """The body of :func:`read_legacy_document`, minus the error translation."""

    if not isinstance(document, dict):
        raise LegacyReadError("a v3 report must be a JSON object")
    version = document.get("schema_version")
    if version != LEGACY_SCHEMA_VERSION:
        raise LegacyReadError(
            f"expected schema_version {LEGACY_SCHEMA_VERSION!r}, found {version!r}"
        )
    engines = document.get("results")
    if not isinstance(engines, list):
        raise LegacyReadError("a v3 suite report must carry a 'results' list")
    suite_status = document.get("suite_status")
    if not isinstance(suite_status, str):
        raise LegacyReadError("a v3 suite report must carry a string 'suite_status'")

    producer_version = _dig(document, "analysis_metadata", "producer_version")
    if not isinstance(producer_version, str) or not producer_version:
        raise LegacyReadError(
            "this v3 report has no analysis_metadata.producer_version, so the build "
            "of ici that produced it is unknown"
        )

    losses: list[str] = []
    gate = _gate(suite_status, losses)
    findings, errored = _engine_findings(engines, losses)

    commit = _dig(document, "analysis_context", "identity", "source_commit")
    if not isinstance(commit, str) or commit in ("", "unavailable"):
        commit = None
        losses.append("source commit is unknown: v3 writes 'unavailable' when it cannot read one")
    if _dig(document, "analysis_metadata", "fingerprint_version") is None:
        losses.append(
            "no fingerprint_version, so these fingerprints are not known to be comparable "
            "with another report's"
        )
    losses.extend(_STRUCTURAL_LIMITATIONS)

    return LegacyReport(
        producer=Producer(ici_version=producer_version),
        gate=gate,
        execution=ExecutionSummary(required_complete=not errored),
        policy_digest=_first_digest(document, _POLICY_DIGEST_SOURCES, "policy digest"),
        toolchain_digest=_first_digest(document, _TOOLCHAIN_DIGEST_SOURCES, "toolchain digest"),
        findings=tuple(findings),
        metrics=_metrics(document),
        commit=commit,
        limitations=tuple(losses),
    )


def read_legacy_report(path: Path) -> LegacyReport:
    """Read a v3 report from disk, naming the file in every failure."""

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as err:
        raise LegacyReadError(f"could not read legacy report {path}: {err}") from err
    try:
        document = json.loads(text)
    except ValueError as err:
        raise LegacyReadError(f"{path} is not valid JSON: {err}") from err
    try:
        return read_legacy_document(document)
    except LegacyReadError as err:
        raise LegacyReadError(f"{path}: {err}") from err


def promote(report: LegacyReport, *, run_id: str, source: SourceSnapshot) -> RunResult:
    """Complete a ``LegacyReport`` into a ``RunResult`` with a caller's snapshot.

    ``source`` is required rather than defaulted because it is the one thing the
    v3 document could not supply. A caller reaches this function only when it has
    computed the snapshot from the tree the report describes; anything else would
    reintroduce the fabricated identity the reader exists to avoid.

    The snapshot's own commit wins when it has one, since it was measured now
    against the tree, while the report's commit was recorded by an older build.
    """

    if source.commit is None and report.commit is not None:
        source = SourceSnapshot(
            digest=source.digest,
            files=source.files,
            generated=source.generated,
            external_inputs=source.external_inputs,
            commit=report.commit,
            dirty=source.dirty,
        )
    return RunResult(
        run_id=run_id,
        producer=report.producer,
        identity=RunIdentity(
            source=source,
            policy_digest=report.policy_digest,
            toolchain_digest=report.toolchain_digest,
        ),
        # STANDALONE, not FULL. The model refuses a FULL scope that does not
        # also claim full_required_satisfied, and an imported v3 report cannot
        # claim it: v3 has no components, so there is no set of required ones to
        # have covered. STANDALONE is the value for a single run that must not
        # stand in for a workspace verdict, which is exactly what this is.
        scope=ScopeSelection(kind=ScopeKind.STANDALONE, full_required_satisfied=False),
        execution=report.execution,
        gate=report.gate,
        findings=report.findings,
        metrics=report.metrics,
        publication=PublicationOutcome(),
        limitations=report.limitations,
    )
