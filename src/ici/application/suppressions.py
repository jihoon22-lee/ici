"""Applying declared suppressions to the findings a run produced (#221).

A suppression never removes a finding and never finishes a check — it marks
the finding with its reason and the file that declared it, and a marked
finding no longer counts against the gate. The marking happens after the
providers have spoken and before the gate is judged, which is the only order
that satisfies both halves of SPEC-04 section 4: suppressed findings stay in
the result, and a required check that did not finish stays INCOMPLETE no
matter what was suppressed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from ici.config.composition import EffectiveSuppression
from ici.domain.finding import Finding, FindingSuppression

__all__ = ["apply_suppressions"]


def apply_suppressions(
    findings: tuple[Finding, ...],
    suppressions: Iterable[EffectiveSuppression],
) -> tuple[Finding, ...]:
    """Mark every finding a declared suppression selects.

    The first matching declaration wins: one reason is enough to explain why
    a finding does not gate, and keeping the earliest keeps the report
    pointing at the suppression the author wrote first.
    """

    declared = tuple(suppressions)
    if not declared:
        return findings
    return tuple(_suppress(finding, declared) for finding in findings)


def _suppress(
    finding: Finding,
    declared: tuple[EffectiveSuppression, ...],
) -> Finding:
    for item in declared:
        if _matches(item, finding):
            return replace(
                finding,
                suppression=FindingSuppression(
                    suppressed=True,
                    kind="config",
                    reason=item.reason,
                    origin=item.declared_in,
                ),
            )
    return finding


def _matches(item: EffectiveSuppression, finding: Finding) -> bool:
    """Every selector the declaration names must match — they are conjunctive."""

    if item.fingerprint and item.fingerprint != finding.fingerprint:
        return False
    if item.rule and not (
        finding.rule_id == item.rule or finding.rule_id.startswith(item.rule + ".")
    ):
        return False
    if item.component and item.component != (finding.component_id or ""):
        return False
    return not (item.path and not _path_matches(item.path, finding))


def _path_matches(pattern: str, finding: Finding) -> bool:
    """Match a finding's location against the declared glob.

    A pattern with a separator is anchored to the workspace root; one
    without matches the file's basename anywhere in the tree — the same
    convention a ``.gitignore`` reader already knows.
    """

    location = PurePosixPath(finding.primary_location.path)
    if "/" in pattern:
        return fnmatchcase(location.as_posix(), pattern)
    return fnmatchcase(location.name, pattern)
