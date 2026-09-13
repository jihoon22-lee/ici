"""Compare two run results without letting one axis answer for another.

WP03 (#201 step 5). A snapshot comparison exists to answer "what changed", and
the failure mode it has to avoid is the one this milestone keeps meeting: a
check that did not happen must never read as a check that passed.

#234 fixed the engine-level half of that in the v3 baseline path — an engine
that reported ERROR or SKIP no longer resolves its findings. This module covers
the half that was left: **a finding can also vanish because its file left the
analysed scope**, with every engine running normally. Measured against the
existing comparison, that case is indistinguishable from a fix:

    engine ran, finding gone, file no longer in scope  ->  resolved, no warning

So ``compare`` reports four axes separately, as #201 requires, and the split
between the first two is the whole point:

``findings``       what changed about problems in code that was looked at
``source_scope``   what stopped or started being looked at
``metrics``        numeric movement
``evidence``       how the numbers were obtained, which can change while the
                   number does not — an ESTIMATED 80% is not a MEASURED 80%

A finding whose file left scope is reported under ``source_scope`` as withheld,
never under ``findings`` as resolved. Nobody measured it, so there is nothing to
call resolved.

Normalisation is deliberately shallow (#201: sort and timestamps only). Ordering
is made deterministic and nothing else is touched, because a comparison that
normalised severities or messages would hide the differences it exists to find.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ici.domain.enums import EvidenceLevel
from ici.domain.finding import Finding
from ici.domain.result import RunResult

__all__ = [
    "EvidenceDiff",
    "FindingDiff",
    "MetricDiff",
    "ScopeDiff",
    "SnapshotDiff",
    "compare",
]


@dataclass(frozen=True)
class FindingDiff:
    """Problems, restricted to code both runs actually looked at.

    ``resolved`` is only ever populated for findings whose file is still in
    scope. Everything else the old run reported is in ``ScopeDiff.withheld``.
    """

    added: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.resolved)


@dataclass(frozen=True)
class ScopeDiff:
    """What each run looked at, and what that costs the finding comparison."""

    entered: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    # Findings the old run reported in files the new run did not look at. These
    # are not resolved and not unchanged; they are unmeasured.
    withheld: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.entered or self.left)

    @property
    def hides_findings(self) -> bool:
        """Whether the scope change makes the finding comparison partial."""

        return bool(self.withheld)


@dataclass(frozen=True)
class MetricDiff:
    """Numeric movement, by measurement name."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    # name -> (before, after), only where the value actually moved.
    changed_values: dict[str, tuple[float, float]] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.changed_values)


@dataclass(frozen=True)
class EvidenceDiff:
    """How a number was obtained, which moves independently of the number.

    A coverage figure that stays at 80% while its evidence drops from MEASURED
    to ESTIMATED has got worse, and a comparison that only watched the value
    would report no change at all.
    """

    # name -> (before, after)
    weakened: dict[str, tuple[str, str]] = field(default_factory=dict)
    strengthened: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.weakened or self.strengthened)


@dataclass(frozen=True)
class SnapshotDiff:
    """The four axes, kept apart."""

    findings: FindingDiff
    source_scope: ScopeDiff
    metrics: MetricDiff
    evidence: EvidenceDiff
    notes: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return (
            self.findings.changed
            or self.source_scope.changed
            or self.metrics.changed
            or self.evidence.changed
        )

    @property
    def comparable(self) -> bool:
        """Whether the finding comparison covered everything the old run saw.

        False means some of the old run's findings were never re-examined, so
        "no new problems" is a statement about part of the code only.
        """

        return not self.source_scope.hides_findings


# EvidenceLevel from strongest to weakest. Only the direction matters, so this
# is an order rather than a score: there is no meaningful distance between
# ESTIMATED and NOT_RUN.
_EVIDENCE_ORDER = (
    EvidenceLevel.MEASURED,
    EvidenceLevel.ESTIMATED,
    EvidenceLevel.NOT_APPLICABLE,
    EvidenceLevel.NOT_RUN,
)
_EVIDENCE_RANK = {level: index for index, level in enumerate(_EVIDENCE_ORDER)}


def _by_fingerprint(findings: tuple[Finding, ...]) -> dict[str, Finding]:
    return {finding.fingerprint: finding for finding in findings}


def _scope(result: RunResult) -> set[str]:
    return set(result.identity.source.files)


def _compare_findings(before: RunResult, after: RunResult) -> tuple[FindingDiff, tuple[str, ...]]:
    """Split the old run's findings into resolved, unchanged and withheld.

    A finding counts as resolved only when the new run looked at its file. When
    the new run records no scope at all, nothing can be shown to have been
    looked at, so nothing is resolved and everything gone is withheld — which is
    the honest reading of a result that does not say what it read.
    """

    old = _by_fingerprint(before.findings)
    new = _by_fingerprint(after.findings)
    new_scope = _scope(after)

    resolved: list[str] = []
    withheld: list[str] = []
    for fingerprint, finding in old.items():
        if fingerprint in new:
            continue
        if finding.primary_location.path in new_scope:
            resolved.append(fingerprint)
        else:
            withheld.append(fingerprint)

    diff = FindingDiff(
        added=tuple(sorted(set(new) - set(old))),
        resolved=tuple(sorted(resolved)),
        unchanged=tuple(sorted(set(old) & set(new))),
    )
    return diff, tuple(sorted(withheld))


def _compare_metrics(before: RunResult, after: RunResult) -> tuple[MetricDiff, EvidenceDiff]:
    old = {item.name: item for item in before.metrics}
    new = {item.name: item for item in after.metrics}

    changed_values: dict[str, tuple[float, float]] = {}
    weakened: dict[str, tuple[str, str]] = {}
    strengthened: dict[str, tuple[str, str]] = {}
    for name in sorted(set(old) & set(new)):
        first, second = old[name], new[name]
        if first.value != second.value:
            changed_values[name] = (first.value, second.value)
        before_rank = _EVIDENCE_RANK[first.evidence]
        after_rank = _EVIDENCE_RANK[second.evidence]
        if after_rank > before_rank:
            weakened[name] = (first.evidence.value, second.evidence.value)
        elif after_rank < before_rank:
            strengthened[name] = (first.evidence.value, second.evidence.value)

    metrics = MetricDiff(
        added=tuple(sorted(set(new) - set(old))),
        removed=tuple(sorted(set(old) - set(new))),
        changed_values=changed_values,
    )
    return metrics, EvidenceDiff(weakened=weakened, strengthened=strengthened)


def _notes(scope: ScopeDiff, before: RunResult, after: RunResult) -> tuple[str, ...]:
    """Say in words what a reader must not conclude from this comparison."""

    notes: list[str] = []
    if scope.withheld:
        notes.append(
            f"{len(scope.withheld)} finding(s) from the earlier run are in files the later "
            f"run did not analyse; they are withheld rather than resolved"
        )
    if not _scope(after) and (before.findings or after.findings):
        # Only when it costs something. A run with no scope and no findings on
        # either side has no finding comparison to qualify, and a note about a
        # limitation with no consequence trains readers to skip the notes.
        notes.append(
            "the later run records no source scope, so no finding can be shown to have "
            "been re-examined"
        )
    if before.identity.policy_digest != after.identity.policy_digest:
        notes.append("the analysis policy differs between the runs")
    if before.identity.toolchain_digest != after.identity.toolchain_digest:
        notes.append("the toolchain differs between the runs")
    return tuple(notes)


def compare(before: RunResult, after: RunResult) -> SnapshotDiff:
    """Compare two runs across four axes that never answer for one another."""

    findings, withheld = _compare_findings(before, after)
    old_scope, new_scope = _scope(before), _scope(after)
    scope = ScopeDiff(
        entered=tuple(sorted(new_scope - old_scope)),
        left=tuple(sorted(old_scope - new_scope)),
        withheld=withheld,
    )
    metrics, evidence = _compare_metrics(before, after)
    return SnapshotDiff(
        findings=findings,
        source_scope=scope,
        metrics=metrics,
        evidence=evidence,
        notes=_notes(scope, before, after),
    )
