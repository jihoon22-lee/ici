"""Immutable reporter-neutral issue projection records."""

from __future__ import annotations

from dataclasses import dataclass

from ici.core.models import Finding, FindingSeverity, SourceLocation


@dataclass(frozen=True)
class IssueLocation:
    """One display location, possibly the union of overlapping source spans."""

    path: str
    start_line: int
    end_line: int
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class IssueGroup:
    """A display-only logical issue with every represented location retained."""

    engine_name: str
    rule_id: str
    category: str
    severity: FindingSeverity
    fingerprints: tuple[str, ...]
    message: str
    snippet: str
    locations: tuple[IssueLocation, ...]
    original_finding_count: int
    clone_group_id: str = ""
    producer_counts: tuple[tuple[str, int], ...] = ()
    provenance: tuple[str, ...] = ()
    primary_location: SourceLocation | None = None
    related_locations: tuple[SourceLocation, ...] = ()


@dataclass(frozen=True)
class IssueComponent:
    """One pre-projection group and the producer findings it represents."""

    group: IssueGroup
    records: tuple[tuple[str, Finding], ...]
