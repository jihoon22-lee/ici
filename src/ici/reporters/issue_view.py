"""Pure, bounded issue projection used by the terminal reporter.

The projection deliberately never mutates an ``EngineResult``.  Baselines and
structured reporters therefore continue to observe the complete v3 finding
inventory while the terminal can coalesce duplicate display regions and cap
noise per engine.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ici.core.findings import canonical_project_path, findings_for_result
from ici.core.models import (
    EngineResult,
    Finding,
    FindingSeverity,
    SourceLocation,
    VerificationSuiteResult,
)
from ici.reporters.issue_models import IssueComponent, IssueGroup, IssueLocation
from ici.reporters.python_issue_projection import merge_python_components

DEFAULT_MAX_FINDINGS = 5
DEFAULT_MAX_LOCATIONS = 4


class ConsoleGroupBy(str, Enum):
    """Supported terminal presentation buckets."""

    ENGINE = "engine"
    SEVERITY = "severity"
    CATEGORY = "category"
    FILE = "file"
    RULE = "rule"


@dataclass(frozen=True)
class ConsoleOptions:
    """Controls only the terminal projection, never the result inventory."""

    verbose: bool = False
    max_findings: int = DEFAULT_MAX_FINDINGS
    group_by: ConsoleGroupBy = ConsoleGroupBy.ENGINE
    rerun_command: str = "ici verify --verbose"

    def __post_init__(self) -> None:
        if self.max_findings < 0:
            raise ValueError("max_findings must be zero or greater")
        if isinstance(self.group_by, str):
            object.__setattr__(self, "group_by", ConsoleGroupBy(self.group_by))


@dataclass(frozen=True)
class IssueSelection:
    """Full and bounded issue views plus honest inventory counts."""

    all_groups: tuple[IssueGroup, ...]
    visible_groups: tuple[IssueGroup, ...]
    total_findings: int
    visible_findings: int
    hidden_findings: int
    hidden_groups: int

    @property
    def total_groups(self) -> int:
        return len(self.all_groups)


_SEVERITY_RANK = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.HIGH: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.LOW: 3,
    FindingSeverity.INFO: 4,
}


def _end_line(location: SourceLocation) -> int:
    return location.end_line or location.start_line


def _overlaps(
    left_start: int,
    left_end: int,
    right_start: int,
    right_end: int,
) -> bool:
    return left_start <= right_end and right_start <= left_end


def _merge_locations(locations: Iterable[IssueLocation]) -> tuple[IssueLocation, ...]:
    """Union transitively overlapping intervals, but never merely adjacent ones."""

    ordered = sorted(
        locations,
        key=lambda item: (item.path, item.start_line, item.end_line, item.labels),
    )
    merged: list[IssueLocation] = []
    for location in ordered:
        if (
            merged
            and merged[-1].path == location.path
            and _overlaps(
                merged[-1].start_line,
                merged[-1].end_line,
                location.start_line,
                location.end_line,
            )
        ):
            previous = merged[-1]
            merged[-1] = IssueLocation(
                path=previous.path,
                start_line=min(previous.start_line, location.start_line),
                end_line=max(previous.end_line, location.end_line),
                labels=tuple(sorted(set(previous.labels + location.labels))),
            )
        else:
            merged.append(location)
    return tuple(merged)


def _actionable(findings: Iterable[Finding]) -> list[Finding]:
    return [
        finding
        for finding in findings
        if finding.severity != FindingSeverity.INFO and not finding.suppression.suppressed
    ]


def _representative(findings: Iterable[Finding]) -> Finding:
    return min(
        findings,
        key=lambda finding: (
            _SEVERITY_RANK[finding.severity],
            finding.primary_location.path,
            finding.primary_location.start_line,
            finding.rule_id,
            finding.fingerprint,
            finding.message,
        ),
    )


def _location_from_source(location: SourceLocation) -> IssueLocation:
    return IssueLocation(
        path=location.path,
        start_line=location.start_line,
        end_line=_end_line(location),
        labels=(location.label,) if location.label else (),
    )


def _generic_components(engine_name: str, findings: list[Finding]) -> list[IssueComponent]:
    """Merge only overlapping occurrences with an identical v3 identity."""

    by_identity: dict[tuple[str, str, str], list[Finding]] = {}
    for finding in findings:
        location = finding.primary_location
        key = (finding.rule_id, finding.fingerprint, location.path)
        by_identity.setdefault(key, []).append(finding)

    components_out: list[IssueComponent] = []
    for identity in sorted(by_identity):
        ordered = sorted(
            by_identity[identity],
            key=lambda finding: (
                finding.primary_location.start_line,
                _end_line(finding.primary_location),
                finding.message,
            ),
        )
        components: list[list[Finding]] = []
        component_end = 0
        for finding in ordered:
            start = finding.primary_location.start_line
            end = _end_line(finding.primary_location)
            if components and start <= component_end:
                components[-1].append(finding)
                component_end = max(component_end, end)
            else:
                components.append([finding])
                component_end = end

        for component in components:
            representative = _representative(component)
            display_locations = [
                _location_from_source(location)
                for finding in component
                for location in (finding.primary_location, *finding.related_locations)
            ]
            group = IssueGroup(
                engine_name=engine_name,
                rule_id=representative.rule_id,
                category=representative.category.value,
                severity=representative.severity,
                fingerprints=tuple(sorted({finding.fingerprint for finding in component})),
                message=representative.message,
                snippet=representative.snippet,
                locations=_merge_locations(display_locations),
                original_finding_count=len(component),
                producer_counts=((engine_name, len(component)),),
                primary_location=representative.primary_location,
                related_locations=tuple(representative.related_locations),
            )
            components_out.append(
                IssueComponent(group, tuple((engine_name, finding) for finding in component))
            )
    return components_out


def _positive_line(value: object) -> int | None:
    if type(value) is not int or value < 1:
        return None
    return value


def _canonical_occurrence(
    occurrence: object,
    project_root: Path,
) -> IssueLocation | None:
    if not isinstance(occurrence, dict):
        return None
    path = occurrence.get("file_path")
    start = _positive_line(occurrence.get("start_line"))
    end = _positive_line(occurrence.get("end_line"))
    if not isinstance(path, str) or start is None:
        return None
    if end is None:
        end = start
    if end < start:
        return None
    try:
        canonical_path = canonical_project_path(path, project_root)
    except ValueError:
        return None
    return IssueLocation(canonical_path, start, end)


def _clone_groups(
    result: EngineResult,
    findings: list[Finding],
    project_root: Path,
) -> tuple[list[IssueGroup], set[int]]:
    """Project runtime clone relations without treating group ids as stable identity."""

    raw_groups = result.extra.get("clone_groups")
    if not isinstance(raw_groups, list):
        return [], set()

    groups: list[IssueGroup] = []
    consumed: set[int] = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        raw_id = raw_group.get("id")
        if not isinstance(raw_id, (str, int)) or isinstance(raw_id, bool) or str(raw_id) == "":
            continue
        group_id = str(raw_id)
        raw_occurrences = raw_group.get("occurrences")
        if not isinstance(raw_occurrences, list):
            continue
        locations = [
            location
            for occurrence in raw_occurrences
            if (location := _canonical_occurrence(occurrence, project_root)) is not None
        ]
        if not locations:
            continue

        expected_label = f"CloneGroup#{group_id}"
        matched: list[Finding] = []
        for index, finding in enumerate(findings):
            if index in consumed:
                continue
            primary = finding.primary_location
            if primary.label != expected_label:
                continue
            if any(
                location.path == primary.path
                and _overlaps(
                    location.start_line,
                    location.end_line,
                    primary.start_line,
                    _end_line(primary),
                )
                for location in locations
            ):
                matched.append(finding)
                consumed.add(index)
        if not matched:
            continue

        representative = _representative(matched)
        lines_count = raw_group.get("lines_count")
        line_text = f"{lines_count} matched lines" if type(lines_count) is int else "matched code"
        merged_locations = _merge_locations(locations)
        message = f"Clone group {group_id}: {line_text} across {len(merged_locations)} location(s)"
        snippet = raw_group.get("snippet")
        groups.append(
            IssueGroup(
                engine_name=result.engine_name,
                rule_id=representative.rule_id,
                category=representative.category.value,
                severity=representative.severity,
                fingerprints=tuple(sorted({finding.fingerprint for finding in matched})),
                message=message,
                snippet=snippet if isinstance(snippet, str) else representative.snippet,
                locations=merged_locations,
                original_finding_count=len(matched),
                clone_group_id=group_id,
                producer_counts=((result.engine_name, len(matched)),),
                primary_location=representative.primary_location,
                related_locations=tuple(representative.related_locations),
            )
        )
    return groups, consumed


def _group_sort_key(group: IssueGroup) -> tuple[object, ...]:
    first = group.locations[0] if group.locations else IssueLocation("", 0, 0)
    return (
        _SEVERITY_RANK[group.severity],
        group.engine_name,
        group.category,
        group.rule_id,
        first.path,
        first.start_line,
        first.end_line,
        group.fingerprints,
        group.message,
    )


def project_issue_groups(
    results: Iterable[EngineResult],
    project_root: str | Path,
) -> tuple[tuple[IssueGroup, ...], int]:
    """Return the full reporter-neutral issue projection and source count."""

    root = Path(project_root).resolve()
    all_groups: list[IssueGroup] = []
    generic_components: list[IssueComponent] = []
    total_findings = 0

    for result in results:
        try:
            findings = _actionable(findings_for_result(result, root))
        except (TypeError, ValueError):
            # A malformed display record must not prevent structured reporters
            # from preserving and diagnosing the original result.
            findings = []
        total_findings += len(findings)
        if result.engine_name == "dup":
            clone_groups, consumed = _clone_groups(result, findings, root)
            all_groups.extend(clone_groups)
            remaining = [finding for index, finding in enumerate(findings) if index not in consumed]
            generic_components.extend(_generic_components(result.engine_name, remaining))
        else:
            generic_components.extend(_generic_components(result.engine_name, findings))

    all_groups.extend(merge_python_components(generic_components, root))
    return tuple(sorted(all_groups, key=_group_sort_key)), total_findings


def select_issue_groups(
    suite: VerificationSuiteResult,
    project_root: str | Path,
    options: ConsoleOptions | None = None,
) -> IssueSelection:
    """Return a deterministic console projection capped independently per engine."""

    selected_options = options or ConsoleOptions()
    ordered, total_findings = project_issue_groups(suite.results, project_root)

    if selected_options.verbose:
        visible = ordered
    else:
        engine_counts: dict[str, int] = {}
        bounded: list[IssueGroup] = []
        for group in ordered:
            count = engine_counts.get(group.engine_name, 0)
            if count < selected_options.max_findings:
                bounded.append(group)
            engine_counts[group.engine_name] = count + 1
        visible = tuple(bounded)

    visible_findings = sum(group.original_finding_count for group in visible)
    return IssueSelection(
        all_groups=ordered,
        visible_groups=visible,
        total_findings=total_findings,
        visible_findings=visible_findings,
        hidden_findings=max(0, total_findings - visible_findings),
        hidden_groups=max(0, len(ordered) - len(visible)),
    )


def issue_bucket(group: IssueGroup, group_by: ConsoleGroupBy) -> str:
    """Return a human-readable display bucket without changing issue identity."""

    if group_by == ConsoleGroupBy.SEVERITY:
        return group.severity.value
    if group_by == ConsoleGroupBy.CATEGORY:
        return group.category
    if group_by == ConsoleGroupBy.FILE:
        paths = {location.path for location in group.locations}
        if len(paths) > 1:
            return "(multiple files)"
        return group.locations[0].path if group.locations else "(no location)"
    if group_by == ConsoleGroupBy.RULE:
        return group.rule_id
    return group.engine_name
