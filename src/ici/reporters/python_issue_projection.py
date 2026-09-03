"""Conservative cross-producer projection for equivalent Python findings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ici.core.findings import canonical_project_path
from ici.core.models import Finding, FindingSeverity, SourceLocation
from ici.core.python_rules import can_merge_python_findings, python_rule_identity
from ici.reporters.issue_models import IssueComponent, IssueGroup, IssueLocation

_SEVERITY_RANK = {
    FindingSeverity.CRITICAL: 0,
    FindingSeverity.HIGH: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.LOW: 3,
    FindingSeverity.INFO: 4,
}

_CONTEXTUAL_GROUPS = {
    "ici.python.security.weak-hash": (
        "ici.python.security.weak-md5",
        "ici.python.security.weak-sha1",
    ),
    "ici.python.security.dynamic-execution": (
        "ici.python.security.eval",
        "ici.python.security.exec",
    ),
}

_TRUSTED_CONTEXTS = {
    ("exception", "baseexception"): "baseexception",
    ("resource", "openwithoutwith"): "confirmed-leak",
    ("security", "weakcryptomd5"): "md5",
    ("security", "weakcryptosha1"): "sha1",
    ("security", "shelltrue"): "subprocess",
    ("security", "commandprocessor"): "os.system",
    ("lint", "s307"): "eval",
    ("lint", "s602"): "subprocess",
    ("lint", "s604"): "subprocess",
    ("lint", "s605"): "os.system",
}


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, index: int) -> int:
        while self.parents[index] != index:
            self.parents[index] = self.parents[self.parents[index]]
            index = self.parents[index]
        return index

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


def _end_line(location: SourceLocation) -> int:
    return location.end_line or location.start_line


def _python_source(finding: Finding) -> bool:
    return finding.primary_location.path.casefold().endswith((".py", ".pyi"))


def _source_label(engine_name: str, finding: Finding) -> str:
    identity = python_rule_identity(finding, engine_name=engine_name)
    source_rule = identity.source_rule_id or finding.rule_id
    tool_identity = " ".join(part for part in (finding.tool_name, finding.tool_version) if part)
    tool = f" ({tool_identity})" if tool_identity else ""
    return f"{engine_name}/{source_rule}{tool}"


def _trusted_semantic_context(engine_name: str, finding: Finding) -> str | None:
    """Return context asserted by a built-in AST rule or narrow tool code."""

    source = (finding.tool_rule_id or finding.primary_location.label).casefold()
    return _TRUSTED_CONTEXTS.get((engine_name.casefold(), source.rsplit(":", 1)[-1]))


def _can_merge(
    left_engine: str,
    left: Finding,
    right_engine: str,
    right: Finding,
    project_root: Path,
) -> bool:
    left_context = _trusted_semantic_context(left_engine, left)
    right_context = _trusted_semantic_context(right_engine, right)
    if left_context is None:
        left_context = right_context
    elif right_context is None:
        right_context = left_context
    return can_merge_python_findings(
        left,
        right,
        left_engine_name=left_engine,
        right_engine_name=right_engine,
        left_semantic_context=left_context,
        right_semantic_context=right_context,
        project_root=project_root,
    )


def _candidate_keys(
    engine_name: str,
    finding: Finding,
    project_root: Path,
) -> tuple[tuple[str, str], ...]:
    if not _python_source(finding):
        return ()
    location = finding.primary_location
    if location.end_line is None or location.start_column is None or location.end_column is None:
        return ()
    identity = python_rule_identity(finding, engine_name=engine_name)
    if identity.canonical_rule_id.startswith("ici.python.external."):
        return ()
    try:
        path = canonical_project_path(location.path, project_root)
    except (TypeError, ValueError):
        return ()
    groups = _CONTEXTUAL_GROUPS.get(
        identity.canonical_rule_id,
        (identity.merge_group or identity.canonical_rule_id,),
    )
    return tuple((group, path) for group in groups)


def _component_bounds(component: IssueComponent) -> tuple[int, int]:
    return (
        min(finding.primary_location.start_line for _engine, finding in component.records),
        max(_end_line(finding.primary_location) for _engine, finding in component.records),
    )


def _overlapping_pairs(
    indices: set[int],
    components: list[IssueComponent],
) -> set[tuple[int, int]]:
    ordered = sorted(indices, key=lambda index: _component_bounds(components[index])[0])
    pairs: set[tuple[int, int]] = set()
    for offset, left_index in enumerate(ordered):
        left_end = _component_bounds(components[left_index])[1]
        for right_index in ordered[offset + 1 :]:
            if _component_bounds(components[right_index])[0] > left_end:
                break
            pairs.add((min(left_index, right_index), max(left_index, right_index)))
    return pairs


def _candidate_pairs(
    components: list[IssueComponent],
    project_root: Path,
) -> set[tuple[int, int]]:
    buckets: dict[tuple[str, str], set[int]] = {}
    for index, component in enumerate(components):
        for engine_name, finding in component.records:
            for key in _candidate_keys(engine_name, finding, project_root):
                buckets.setdefault(key, set()).add(index)

    return {
        pair
        for indices in buckets.values()
        for pair in _overlapping_pairs(indices, components)
    }


def _components_can_merge(
    left: IssueComponent,
    right: IssueComponent,
    project_root: Path,
) -> bool:
    if left.group.engine_name == right.group.engine_name:
        return False
    return any(
        _can_merge(left_engine, left_finding, right_engine, right_finding, project_root)
        for left_engine, left_finding in left.records
        for right_engine, right_finding in right.records
    )


def _clusters(
    components: list[IssueComponent],
    project_root: Path,
) -> list[list[IssueComponent]]:
    relations = _DisjointSet(len(components))
    for left_index, right_index in sorted(_candidate_pairs(components, project_root)):
        if _components_can_merge(components[left_index], components[right_index], project_root):
            relations.union(left_index, right_index)
    grouped: dict[int, list[IssueComponent]] = {}
    for index, component in enumerate(components):
        grouped.setdefault(relations.find(index), []).append(component)
    return list(grouped.values())


def _source_location_key(location: SourceLocation) -> tuple[object, ...]:
    return (
        location.path,
        location.start_line,
        location.end_line,
        location.start_column,
        location.end_column,
        location.label,
    )


def _related_locations(
    representative: Finding,
    records: list[tuple[str, Finding]],
) -> tuple[SourceLocation, ...]:
    locations: list[SourceLocation] = []
    seen: set[tuple[object, ...]] = set()
    for _engine_name, finding in records:
        for location in (finding.primary_location, *finding.related_locations):
            key = _source_location_key(location)
            if location is representative.primary_location or key in seen:
                continue
            seen.add(key)
            locations.append(location)
    return tuple(locations)


def _merge_display_locations(records: list[tuple[str, Finding]]) -> tuple[IssueLocation, ...]:
    locations = sorted(
        (
            IssueLocation(
                location.path,
                location.start_line,
                _end_line(location),
                (location.label,) if location.label else (),
            )
            for _engine, finding in records
            for location in (finding.primary_location, *finding.related_locations)
        ),
        key=lambda item: (item.path, item.start_line, item.end_line, item.labels),
    )
    merged: list[IssueLocation] = []
    for location in locations:
        if (
            merged
            and merged[-1].path == location.path
            and location.start_line <= merged[-1].end_line
        ):
            previous = merged[-1]
            merged[-1] = IssueLocation(
                previous.path,
                min(previous.start_line, location.start_line),
                max(previous.end_line, location.end_line),
                tuple(sorted(set(previous.labels + location.labels))),
            )
        else:
            merged.append(location)
    return tuple(merged)


def _representative_record(records: list[tuple[str, Finding]]) -> tuple[str, Finding]:
    return min(
        records,
        key=lambda record: (
            _SEVERITY_RANK[record[1].severity],
            record[1].primary_location.path,
            record[1].primary_location.start_line,
            record[0],
            record[1].fingerprint,
        ),
    )


def _cluster_context(records: list[tuple[str, Finding]]) -> str | None:
    return next(
        (
            context
            for engine_name, finding in records
            if (context := _trusted_semantic_context(engine_name, finding)) is not None
        ),
        None,
    )


def _project_single(component: IssueComponent) -> IssueGroup:
    engine_name, finding = component.records[0]
    identity = python_rule_identity(finding, engine_name=engine_name)
    if not _python_source(finding) or not identity.mergeable:
        return component.group
    return replace(
        component.group,
        rule_id=identity.canonical_rule_id,
        provenance=tuple(
            sorted(
                {
                    _source_label(source_engine, source)
                    for source_engine, source in component.records
                }
            )
        ),
    )


def _project_merged(cluster: list[IssueComponent]) -> IssueGroup:
    records = [record for component in cluster for record in component.records]
    representative_engine, representative = _representative_record(records)
    identity = python_rule_identity(
        representative,
        engine_name=representative_engine,
        semantic_context=_cluster_context(records),
    )
    producer_counts: dict[str, int] = {}
    for engine_name, _finding in records:
        producer_counts[engine_name] = producer_counts.get(engine_name, 0) + 1
    return IssueGroup(
        engine_name=" + ".join(sorted(producer_counts)),
        rule_id=identity.canonical_rule_id,
        category=representative.category.value,
        severity=representative.severity,
        fingerprints=tuple(sorted({finding.fingerprint for _, finding in records})),
        message=representative.message,
        snippet=representative.snippet,
        locations=_merge_display_locations(records),
        original_finding_count=len(records),
        producer_counts=tuple(sorted(producer_counts.items())),
        provenance=tuple(sorted({_source_label(engine, finding) for engine, finding in records})),
        primary_location=representative.primary_location,
        related_locations=_related_locations(representative, records),
    )


def merge_python_components(
    components: list[IssueComponent],
    project_root: Path,
) -> list[IssueGroup]:
    """Merge only precise, semantically equivalent cross-producer records."""

    return [
        _project_single(cluster[0]) if len(cluster) == 1 else _project_merged(cluster)
        for cluster in _clusters(components, project_root)
    ]
