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
from dataclasses import dataclass
from pathlib import Path

from ici.analysis._cpp_dup_tokenization import tokenize_cpp_lines
from ici.analysis._dup_clustering import CloneGroup, cluster_matches
from ici.analysis._dup_matching import (
    DuplicateComparisonLimit,
    DuplicateFileData,
    DuplicateMatchLimits,
    filter_subsumed_matches,
    find_raw_matches,
)
from ici.analysis._dup_regions import cpp_duplicate_regions, python_duplicate_regions
from ici.analysis._python_dup_tokenization import tokenize_python_lines
from ici.analysis._source_inputs import AnalysisSourceError, read_analysis_sources
from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation

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
    clustering = cluster_matches(matches, files_data)
    duplicated = len(clustering.duplicated_positions)
    findings = [
        _finding(request, group, files_data[f_idx].file_path, s_l, e_l)
        for group in clustering.groups
        for f_idx, s_l, e_l in group.locations
    ]
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=(
            Measurement(name="clone_groups", value=len(clustering.groups), unit="groups"),
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


def _finding(
    request: DuplicateRequest,
    group: CloneGroup,
    file_path: str,
    start: int,
    end: int,
) -> Finding:
    # The location is in the hash because two occurrences of one clone can sit
    # in the same file — a fingerprint without it collapsed them into one.
    digest = hashlib.sha256(
        "\x00".join((PROVIDER_NAME, group.fingerprint, file_path, str(start), str(end))).encode()
    ).hexdigest()
    return Finding(
        fingerprint=f"sha256:{digest}",
        rule_id="dup.type2-clone",
        message=(
            f"duplicate block ({group.line_count} lines, group "
            f"#{group.index}) shared across {len(group.locations)} locations"
        ),
        severity="medium",
        confidence="high",
        primary_location=SourceSpan(
            path=file_path,
            start_line=start,
            end_line=end,
        ),
        provider=PROVIDER_NAME,
        component_id=request.component_id or None,
        task_id=request.task_id,
        evidence=EvidenceLevel.MEASURED,
    )
