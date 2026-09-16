"""Type-2 duplicate detection as ici's own observation.

``*.dup`` is an internal check: the tokenizers, window matcher and cluster
logic are the same ones the stable ``dup`` engine runs, reused rather than
re-implemented (#218's dedup requirement — one implementation, two callers).
The ownership policy is the shared one too: generated and vendor files are
excluded by default, and an exclusion is reported, not silently skipped.

The AST-shape semantic clustering the stable engine layers on top for Python
is not carried yet — its absence is a stated limitation, not silence.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines._cpp_dup_tokenization import tokenize_cpp_lines
from ici.engines._dup_matching import (
    DuplicateComparisonLimit,
    DuplicateFileData,
    DuplicateMatchLimits,
    filter_subsumed_matches,
    find_raw_matches,
)
from ici.engines._dup_regions import cpp_duplicate_regions, python_duplicate_regions
from ici.engines._python_dup_tokenization import tokenize_python_lines
from ici.engines._source_inputs import AnalysisSourceError, read_analysis_sources

__all__ = ["PROVIDER_NAME", "DuplicateRequest", "measure_duplicates"]

PROVIDER_NAME = "ici.dup"

#: The stable engine's shipped policy — warn at 5% duplicated lines, the
#: match window and the comparison budgets are the same constants it reads.
WARN_PCT = 5.0
FAIL_PCT = 15.0
WINDOW_SIZE = 6
_LIMITS = DuplicateMatchLimits(
    window_occurrences=2_048,
    same_file_seed_pairs=100_000,
    cross_file_pairs=20_000,
    cross_file_seed_pairs=250_000,
    extension_comparisons=5_000_000,
    raw_matches=10_000,
)
_MAX_NORMALIZED_CHARS = 128 * 1024 * 1024
_MAX_INDEXED_RECORDS = 500_000
_FINGERPRINT_ALGORITHM = "sha256/type2-region-v1"


@dataclass(frozen=True)
class DuplicateRequest:
    """The component files one language's duplicate scan reads."""

    language: str  # "python" or "cpp"
    project_root: Path
    files: tuple[Path, ...]
    task_id: str
    component_id: str = ""


def measure_duplicates(request: DuplicateRequest) -> Observation:
    """Tokenize, match, cluster — and state the duplicated share honestly."""

    try:
        inventory = read_analysis_sources(request.project_root, request.files)
    except AnalysisSourceError as error:
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.FAILED,
            limitations=(f"{error.code}: {error.message}",),
        )

    limitations = [f"excluded {item.file_path}: {item.reason}" for item in inventory.excluded]
    if request.language == "python":
        limitations.append(
            "AST-shape semantic clustering is not carried yet — matches are "
            "lexical Type-2 clones only"
        )
    if not inventory.sources:
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.SUCCEEDED,
            measurements=(
                Measurement(name="clone_groups", value=0, unit="groups"),
                Measurement(name="duplicated_lines", value=0, unit="lines"),
            ),
            limitations=tuple(limitations) or ("no owned source files",),
        )

    try:
        files_data, total_code_lines = _index_sources(inventory.sources)
    except ValueError as error:
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.FAILED,
            limitations=(str(error),),
        )

    try:
        matches = filter_subsumed_matches(find_raw_matches(files_data, WINDOW_SIZE, _LIMITS))
    except DuplicateComparisonLimit as error:
        # The stable engine answers ERROR with the same bound; here the task
        # fails and its limitation says why, so the run reports INCOMPLETE
        # rather than crashing mid-pipeline.
        return Observation(
            task_id=request.task_id,
            provider=PROVIDER_NAME,
            state=TaskState.FAILED,
            limitations=(f"duplicate comparison limit exceeded: {error}",),
        )
    groups, _ = _cluster(matches, files_data)
    duplicated = _duplicated_lines(matches, files_data)
    findings = [_finding(request, group, occ) for group in groups for occ in group["occurrences"]]
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(
            Measurement(name="clone_groups", value=len(groups), unit="groups"),
            Measurement(
                name="duplicated_lines",
                value=duplicated,
                unit="lines",
                numerator=duplicated,
                denominator=total_code_lines or None,
            ),
        ),
        limitations=tuple(limitations),
    )


def _index_sources(
    sources: tuple,
) -> tuple[list[DuplicateFileData], int]:
    """Normalize every owned source into indexed token lines and regions."""

    files_data: list[DuplicateFileData] = []
    total_code_lines = 0
    normalized_chars = 0
    indexed_records = 0
    for source in sorted(sources, key=lambda item: item.file_path):
        try:
            if source.language == "cpp":
                indexed = list(tokenize_cpp_lines(source.text))
                regions = cpp_duplicate_regions(source.text, (line for line, _tokens in indexed))
            else:
                indexed = list(tokenize_python_lines(source.text))
                regions = python_duplicate_regions(source.text, (line for line, _tokens in indexed))
        except ValueError as error:
            raise ValueError(
                f"{source.file_path}: lexical normalization failed: {error}"
            ) from error
        normalized_chars += sum(len(tokens) for _line, tokens in indexed)
        if normalized_chars > _MAX_NORMALIZED_CHARS:
            raise ValueError("normalized duplicate input exceeds the bounded limit")
        indexed_records += len(indexed)
        if indexed_records > _MAX_INDEXED_RECORDS:
            raise ValueError("indexed duplicate records exceed the bounded limit")
        total_code_lines += len(indexed)
        files_data.append(
            DuplicateFileData(source.file_path, source.language, source.lines, indexed, regions)
        )
    return files_data, total_code_lines


_Loc = tuple[int, int, int]


def _cluster(matches: list, files_data: list[DuplicateFileData]) -> tuple[list[dict], None]:
    """Union pairwise matches into multi-occurrence clone groups.

    The grouping is the stable engine's: adjacency over match endpoints,
    connected components, then deterministic ordering. Only the non-first
    occurrences count as duplicated lines — the first is the original.
    """

    adjacency: dict[_Loc, set[_Loc]] = defaultdict(set)
    match_len: dict[_Loc, int] = {}
    for f1, s1, e1, f2, s2, e2, k in matches:
        loc1, loc2 = (f1, s1, e1), (f2, s2, e2)
        adjacency[loc1].add(loc2)
        adjacency[loc2].add(loc1)
        match_len[loc1] = max(match_len.get(loc1, 0), k)
        match_len[loc2] = max(match_len.get(loc2, 0), k)

    visited: set[_Loc] = set()
    clusters: list[list[_Loc]] = []
    for node in sorted(adjacency):
        if node in visited:
            continue
        component: list[_Loc] = []
        queue = [node]
        visited.add(node)
        while queue:
            current = queue.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        clusters.append(component)

    for component in clusters:
        component.sort(key=lambda loc: (files_data[loc[0]].file_path, loc[1], loc[2]))
    clusters.sort(
        key=lambda component: (
            -max(match_len.get(loc, 0) for loc in component),
            -len(component),
            tuple((files_data[loc[0]].file_path, loc[1], loc[2]) for loc in component),
        )
    )

    groups: list[dict] = []
    for index, component in enumerate(clusters, 1):
        rep_f, rep_s, rep_e = min(
            component,
            key=lambda loc: (
                -match_len.get(loc, 0),
                files_data[loc[0]].file_path,
                loc[1],
                loc[2],
            ),
        )
        representative = files_data[rep_f]
        lines_k = max(match_len.get(loc, 0) for loc in component)
        normalized_region = "\n".join(
            normalized
            for line_no, normalized in representative.indexed
            if rep_s <= line_no <= rep_e
        )
        fingerprint = hashlib.sha256(
            f"{_FINGERPRINT_ALGORITHM}\0{representative.language}\0{normalized_region}".encode()
        ).hexdigest()
        snippet = "".join(representative.raw_lines[rep_s - 1 : rep_e]).rstrip()
        groups.append(
            {
                "id": index,
                "fingerprint": fingerprint,
                "language": representative.language,
                "lines": lines_k,
                "occurrences": [
                    {
                        "file": files_data[f_idx].file_path,
                        "start": s_l,
                        "end": e_l,
                    }
                    for f_idx, s_l, e_l in component
                ],
                "snippet": snippet,
            }
        )
    return groups, None


def _duplicated_lines(matches: list, files_data: list[DuplicateFileData]) -> int:
    """Code lines appearing in a clone but as its non-first occurrence.

    The denominator counted only normalized code lines, so the numerator must
    too — a raw span's blanks and comments would let the rate read above 100%.
    """

    adjacency: dict[_Loc, set[_Loc]] = defaultdict(set)
    for f1, s1, e1, f2, s2, e2, _k in matches:
        adjacency[(f1, s1, e1)].add((f2, s2, e2))
        adjacency[(f2, s2, e2)].add((f1, s1, e1))
    visited: set[_Loc] = set()
    code_lines = [{line_no for line_no, _t in file.indexed} for file in files_data]
    duplicated: set[tuple[int, int]] = set()
    for node in sorted(adjacency):
        if node in visited:
            continue
        component = []
        queue = [node]
        visited.add(node)
        while queue:
            current = queue.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
        component.sort(key=lambda loc: (files_data[loc[0]].file_path, loc[1], loc[2]))
        for position, (f_idx, s_l, e_l) in enumerate(component):
            if position == 0:
                continue
            for line_no in range(s_l, e_l + 1):
                if line_no in code_lines[f_idx]:
                    duplicated.add((f_idx, line_no))
    return len(duplicated)


def _finding(request: DuplicateRequest, group: dict, occurrence: dict) -> Finding:
    return Finding(
        fingerprint=f"dup-{group['fingerprint'][:16]}-{occurrence['file']}",
        rule_id="dup.type2-clone",
        message=(
            f"duplicate block ({group['lines']} lines, group "
            f"#{group['id']}) shared across {len(group['occurrences'])} locations"
        ),
        severity="medium",
        confidence="high",
        primary_location=SourceSpan(
            path=occurrence["file"],
            start_line=occurrence["start"],
            end_line=occurrence["end"],
        ),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        task_id=request.task_id,
        evidence=EvidenceLevel.MEASURED,
    )
