"""The one place the new domain is allowed to know about the old models.

Every other module under ``ici.domain`` imports nothing from ``ici.core``; this
one does, and ``tests/test_domain_boundaries.py`` encodes that asymmetry. Making
the bridge a single named module means the coupling is a decision rather than a
habit, and it can be deleted in one piece when the old writer goes away.

``ici.core.models`` is safe to import here because it is pure data: standard
library only, with its two impure neighbours behind ``TYPE_CHECKING``.

The conversion is intentionally lossy in one direction and additive in the
other, and it says so in both:

- old to new gains fields the old record has no room for (provider, native rule
  id, component, analysis unit, variant, task). They come back empty, and
  ``LEGACY_UNMAPPED`` names them so that a caller can report the gap instead of
  presenting an empty string as an answer.
- new to old drops exactly those fields, and ``legacy_limitations`` returns the
  list of what a given finding would lose. SPEC-04 section 5 requires that a
  legacy reader never turns a new result into a silent empty PASS; naming the
  losses is the first half of keeping that promise.
"""

from __future__ import annotations

from ici.core.models import (
    EvidenceState,
)
from ici.core.models import (
    Finding as LegacyFinding,
)
from ici.core.models import (
    FindingSuppression as LegacySuppression,
)
from ici.core.models import (
    SourceLocation as LegacySourceLocation,
)
from ici.domain.enums import EvidenceLevel
from ici.domain.finding import Finding, FindingSuppression, SourceSpan

__all__ = [
    "LEGACY_UNMAPPED",
    "evidence_from_legacy",
    "evidence_to_legacy",
    "finding_from_legacy",
    "finding_to_legacy",
    "legacy_limitations",
    "span_from_legacy",
    "span_to_legacy",
]

# Domain fields that the v3 record cannot express. Kept as data so that both
# the conversion and its test read from the same list.
LEGACY_UNMAPPED = (
    "provider",
    "native_rule_id",
    "rule_version",
    "component_id",
    "analysis_unit_id",
    "variant",
    "task_id",
    "tags",
)

# The four evidence values are identical in both models by design, so the map is
# total in both directions and a new value on either side breaks a test rather
# than silently degrading to a default.
_EVIDENCE_TO_LEGACY = {
    EvidenceLevel.MEASURED: EvidenceState.MEASURED,
    EvidenceLevel.ESTIMATED: EvidenceState.ESTIMATED,
    EvidenceLevel.NOT_RUN: EvidenceState.NOT_RUN,
    EvidenceLevel.NOT_APPLICABLE: EvidenceState.NOT_APPLICABLE,
}
_EVIDENCE_FROM_LEGACY = {value: key for key, value in _EVIDENCE_TO_LEGACY.items()}


def evidence_to_legacy(value: EvidenceLevel) -> EvidenceState:
    """Map a domain evidence level onto the existing ``EvidenceState``."""

    try:
        return _EVIDENCE_TO_LEGACY[value]
    except KeyError as err:  # pragma: no cover - guarded by a totality test
        raise ValueError(f"no legacy EvidenceState for {value!r}") from err


def evidence_from_legacy(value: EvidenceState) -> EvidenceLevel:
    """Map an existing ``EvidenceState`` onto a domain evidence level."""

    try:
        return _EVIDENCE_FROM_LEGACY[value]
    except KeyError as err:  # pragma: no cover - guarded by a totality test
        raise ValueError(f"no domain EvidenceLevel for {value!r}") from err


def span_from_legacy(location: LegacySourceLocation) -> SourceSpan:
    """Convert a v3 ``SourceLocation`` into a domain ``SourceSpan``."""

    return SourceSpan(
        path=location.path,
        start_line=location.start_line,
        end_line=location.end_line,
        start_column=location.start_column,
        end_column=location.end_column,
        label=location.label,
    )


def span_to_legacy(span: SourceSpan) -> LegacySourceLocation:
    """Convert a domain ``SourceSpan`` back into a v3 ``SourceLocation``."""

    return LegacySourceLocation(
        path=span.path,
        start_line=span.start_line,
        end_line=span.end_line,
        start_column=span.start_column,
        end_column=span.end_column,
        label=span.label,
    )


def finding_from_legacy(finding: LegacyFinding, *, provider: str) -> Finding:
    """Lift a v3 finding into the domain model.

    ``provider`` has to be supplied by the caller: the v3 record carries
    ``tool_name``, which names the executable, not the provider that decided to
    run it. Guessing one from the other is how a finding ends up attributed to
    something that never produced it, so the adapter that knows must say.

    The fields listed in ``LEGACY_UNMAPPED`` stay empty here. That is visible in
    the result rather than papered over.
    """

    return Finding(
        fingerprint=finding.fingerprint,
        rule_id=finding.rule_id,
        message=finding.message,
        severity=finding.severity.value,
        confidence=finding.confidence.value,
        primary_location=span_from_legacy(finding.primary_location),
        provider=provider,
        native_rule_id=finding.tool_rule_id,
        rule_version=finding.tool_version,
        category=finding.category.value,
        related_locations=tuple(span_from_legacy(item) for item in finding.related_locations),
        suppression=FindingSuppression(
            suppressed=finding.suppression.suppressed,
            kind=finding.suppression.kind.value,
            reason=finding.suppression.reason,
            origin="legacy-v3",
        ),
    )


def legacy_limitations(finding: Finding) -> tuple[str, ...]:
    """Name what converting this finding to v3 would discard.

    Returns only fields that actually carry a value, so a caller can attach the
    result to a converted report and have it mean something specific rather than
    listing every theoretically lossy field every time.
    """

    losses: list[str] = []
    for name in LEGACY_UNMAPPED:
        value = getattr(finding, name)
        if name in ("native_rule_id", "rule_version"):
            # These two do have a v3 home (tool_rule_id / tool_version), so they
            # are not losses even though they are additions in the other
            # direction.
            continue
        if value:
            losses.append(name)
    return tuple(losses)


def finding_to_legacy(
    finding: Finding,
    *,
    category: object,
    severity: object,
    confidence: object,
    suppression_kind: object,
) -> LegacyFinding:
    """Lower a domain finding into a v3 record.

    The four enum values are passed in rather than looked up from strings. The
    domain stores them as strings so that a provider can report a value this
    build of ici has never seen, and silently coercing an unknown string into
    the nearest v3 enum member would invent a severity. The caller that knows
    the mapping supplies it; ``legacy_limitations`` reports what is lost.
    """

    return LegacyFinding(
        rule_id=finding.rule_id,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        fingerprint=finding.fingerprint,
        primary_location=span_to_legacy(finding.primary_location),
        message=finding.message,
        related_locations=[span_to_legacy(item) for item in finding.related_locations],
        tool_rule_id=finding.native_rule_id,
        tool_version=finding.rule_version,
        suppression=LegacySuppression(
            suppressed=finding.suppression.suppressed,
            kind=suppression_kind,  # type: ignore[arg-type]
            reason=finding.suppression.reason,
        ),
    )
