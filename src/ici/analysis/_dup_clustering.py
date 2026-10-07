"""Connected-component clustering over pairwise duplicate matches.

The last stage both duplicate implementations share: pairwise ``MatchPair``
records become multi-occurrence clone groups, ordered deterministically, and
the same pass counts the duplicated code lines. Keeping it here is what makes
the stable engine and the next ``*.dup`` check equal — the stable engine used
to carry this logic itself, and the next path's port already drifted twice
(built the adjacency twice; labelled the fingerprint ``v1`` while the shared
input has been the ``v2`` shape since #135).

Two invariants worth the words:

- Ordering is fully deterministic — locations sort by
  ``(file_path, start, end)``, groups by ``(-longest match, -size, locations)``,
  so the same inputs always produce the same report.
- The numerator counts only lines the denominator counted. ``indexed`` holds
  normalized code lines; a clone span's blanks and comments would let a rate
  read above 100%, so duplicated positions are intersected with them.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ici.analysis._dup_matching import DuplicateFileData, LocTuple, MatchPair

__all__ = [
    "FINGERPRINT_ALGORITHM",
    "CloneGroup",
    "DuplicateClustering",
    "cluster_matches",
]

#: sha256 over ``ALG\0language\0normalized-region`` — the label recorded on
#: every finding. ``v2`` marks the region-bounded normalization #135 shipped;
#: anything still saying ``v1`` is naming a hash it does not compute.
FINGERPRINT_ALGORITHM = "sha256/type2-region-v2"


@dataclass(frozen=True)
class CloneGroup:
    """One ordered clone cluster, ready for either result model to render."""

    index: int  # 1-based position in the deterministic group order
    line_count: int  # longest match length inside the group
    fingerprint: str
    language: str
    #: Raw representative lines with trailing whitespace trimmed. Callers
    #: bound their own fields — the stable engine clips at 300 chars.
    snippet: str
    #: ``(file_idx, start_line, end_line)`` triples sorted by path and span.
    locations: tuple[LocTuple, ...]


@dataclass(frozen=True)
class DuplicateClustering:
    """Groups plus the duplicated-line positions both sides must agree on."""

    groups: tuple[CloneGroup, ...]
    #: ``(file_idx, line_no)`` — every counted code line in a non-first
    #: occurrence. The first location in a group is the original.
    duplicated_positions: frozenset[tuple[int, int]]


def cluster_matches(
    matches: Iterable[MatchPair], files_data: Sequence[DuplicateFileData]
) -> DuplicateClustering:
    """Union pairwise matches into ordered groups and count duplicated lines."""

    adjacency: dict[LocTuple, set[LocTuple]] = defaultdict(set)
    match_len: dict[LocTuple, int] = {}
    for f1, s1, e1, f2, s2, e2, k in matches:
        loc1, loc2 = (f1, s1, e1), (f2, s2, e2)
        adjacency[loc1].add(loc2)
        adjacency[loc2].add(loc1)
        match_len[loc1] = max(match_len.get(loc1, 0), k)
        match_len[loc2] = max(match_len.get(loc2, 0), k)

    visited: set[LocTuple] = set()
    clusters: list[list[LocTuple]] = []
    for node in sorted(adjacency):
        if node in visited:
            continue
        component: list[LocTuple] = []
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

    code_lines = [{line_no for line_no, _tokens in item.indexed} for item in files_data]
    duplicated: set[tuple[int, int]] = set()
    groups: list[CloneGroup] = []
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
        normalized_region = "\n".join(
            normalized
            for line_no, normalized in representative.indexed
            if rep_s <= line_no <= rep_e
        )
        fingerprint = hashlib.sha256(
            f"{FINGERPRINT_ALGORITHM}\0{representative.language}\0{normalized_region}".encode()
        ).hexdigest()
        # Preserve exact raw indentation (never strip line one's leading space).
        snippet = "".join(representative.raw_lines[rep_s - 1 : rep_e]).rstrip()

        for position, (f_idx, s_l, e_l) in enumerate(component):
            if position == 0:
                continue
            counted = code_lines[f_idx]
            for line_no in range(s_l, e_l + 1):
                if line_no in counted:
                    duplicated.add((f_idx, line_no))

        groups.append(
            CloneGroup(
                index=index,
                line_count=max(match_len.get(loc, 0) for loc in component),
                fingerprint=fingerprint,
                language=representative.language,
                snippet=snippet,
                locations=tuple(component),
            )
        )
    return DuplicateClustering(groups=tuple(groups), duplicated_positions=frozenset(duplicated))
