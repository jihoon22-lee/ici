"""Deterministic, region-bounded seed matching for duplicate analysis."""

from __future__ import annotations

import bisect
import hashlib
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from ici.engines._dup_signal import duplicate_signal_prefix, has_duplicate_signal

LocTuple = tuple[int, int, int]
MatchPair = tuple[int, int, int, int, int, int, int]
_ROLLING_HASH_BASE = 0x100000001B3
_ROLLING_HASH_MODULUS = 1 << 128
_ROLLING_HASH_MASK = _ROLLING_HASH_MODULUS - 1


@dataclass(frozen=True)
class DuplicateFileData:
    file_path: str
    language: str
    raw_lines: list[str]
    indexed: list[tuple[int, str]]
    regions: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class DuplicateMatchLimits:
    window_occurrences: int
    same_file_seed_pairs: int
    cross_file_pairs: int
    cross_file_seed_pairs: int
    extension_comparisons: int
    raw_matches: int

    def __post_init__(self) -> None:
        for name, value in (
            ("window_occurrences", self.window_occurrences),
            ("same_file_seed_pairs", self.same_file_seed_pairs),
            ("cross_file_pairs", self.cross_file_pairs),
            ("cross_file_seed_pairs", self.cross_file_seed_pairs),
            ("extension_comparisons", self.extension_comparisons),
            ("raw_matches", self.raw_matches),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


class DuplicateComparisonLimit(ValueError):
    """Candidate expansion exceeded a fixed project-wide safety bound."""

    def __init__(self, message: str, file_path: str = ".") -> None:
        super().__init__(message)
        self.file_path = file_path
        self.message = message


def _append_bounded(
    matches: list[MatchPair], match: MatchPair, limits: DuplicateMatchLimits
) -> None:
    matches.append(match)
    if len(matches) > limits.raw_matches:
        raise DuplicateComparisonLimit(
            f"Raw duplicate match count exceeds MAX_DUPLICATE_RAW_MATCHES={limits.raw_matches}"
        )


def _interval_contains(intervals: list[tuple[int, int]], position: int) -> bool:
    index = bisect.bisect_right(intervals, (position, 2**63 - 1)) - 1
    return index >= 0 and intervals[index][0] <= position <= intervals[index][1]


def _add_interval(intervals: list[tuple[int, int]], start: int, end: int) -> None:
    index = bisect.bisect_left(intervals, (start, -1))
    if index > 0 and intervals[index - 1][1] + 1 >= start:
        index -= 1
        start = min(start, intervals[index][0])
        end = max(end, intervals[index][1])
        intervals.pop(index)
    while index < len(intervals) and intervals[index][0] <= end + 1:
        start = min(start, intervals[index][0])
        end = max(end, intervals[index][1])
        intervals.pop(index)
    intervals.insert(index, (start, end))


def _record_hash(normalized: str) -> int:
    return int.from_bytes(hashlib.sha256(normalized.encode("utf-8")).digest()[:16], "big")


def _window_hashes(indexed: list[tuple[int, str]], window_size: int) -> Iterator[int]:
    """Yield collision-tolerant rolling hashes in linear source size."""

    if type(window_size) is not int or window_size <= 0:
        raise ValueError("window_size must be a positive integer")
    if len(indexed) < window_size:
        return
    high_factor = pow(_ROLLING_HASH_BASE, window_size - 1, _ROLLING_HASH_MODULUS)
    rolling = 0
    for position in range(window_size):
        normalized = indexed[position][1]
        rolling = (rolling * _ROLLING_HASH_BASE + _record_hash(normalized)) & _ROLLING_HASH_MASK
    yield rolling
    for position in range(len(indexed) - window_size):
        outgoing = _record_hash(indexed[position][1])
        incoming = _record_hash(indexed[position + window_size][1])
        rolling = (
            (rolling - outgoing * high_factor) * _ROLLING_HASH_BASE + incoming
        ) & _ROLLING_HASH_MASK
        yield rolling


def find_raw_matches(
    files_data: list[DuplicateFileData],
    window_size: int,
    limits: DuplicateMatchLimits,
) -> list[MatchPair]:
    """Find maximal exact Type-2 regions from shared normalized window seeds."""

    if type(window_size) is not int or window_size <= 0:
        raise ValueError("window_size must be a positive integer")
    for file_data in files_data:
        if len(file_data.regions) != len(file_data.indexed):
            raise ValueError("duplicate region count must equal indexed record count")

    window_map: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    signal_prefixes = [duplicate_signal_prefix(item.indexed) for item in files_data]
    for file_index, file_data in enumerate(files_data):
        indexed = file_data.indexed
        if len(indexed) < window_size:
            continue
        region_end = -1
        for position, digest in enumerate(_window_hashes(indexed, window_size)):
            if not has_duplicate_signal(signal_prefixes[file_index], position, window_size):
                continue
            region = file_data.regions[position]
            if position > region_end:
                region_end = position
                while region_end + 1 < len(indexed) and file_data.regions[region_end + 1] == region:
                    region_end += 1
            if region_end < position + window_size - 1:
                continue
            occurrences = window_map[(file_data.language, digest)]
            if len(occurrences) >= limits.window_occurrences:
                raise DuplicateComparisonLimit(
                    "Shared duplicate-window occurrence count exceeds "
                    f"MAX_DUPLICATE_WINDOW_OCCURRENCES={limits.window_occurrences}",
                    file_data.file_path,
                )
            occurrences.append((file_index, position))

    raw_matches: list[MatchPair] = []
    covered: dict[tuple[int, int, int], list[tuple[int, int]]] = defaultdict(list)
    cross_file_pairs: set[tuple[int, int]] = set()
    same_seed_pairs = 0
    cross_seed_pairs = 0
    comparisons = 0

    def equal(first_file: int, first: int, second_file: int, second: int) -> bool:
        nonlocal comparisons
        comparisons += 1
        if comparisons > limits.extension_comparisons:
            raise DuplicateComparisonLimit(
                "Duplicate seed extension work exceeds "
                f"MAX_DUPLICATE_EXTENSION_COMPARISONS={limits.extension_comparisons}"
            )
        return (
            files_data[first_file].indexed[first][1] == files_data[second_file].indexed[second][1]
        )

    for key in sorted(window_map):
        occurrences = window_map[key]
        if len(occurrences) < 2:
            continue
        for left_index, (first_file, first_seed) in enumerate(occurrences):
            for second_file, second_seed in occurrences[left_index + 1 :]:
                if first_file == second_file:
                    same_seed_pairs += 1
                    if same_seed_pairs > limits.same_file_seed_pairs:
                        raise DuplicateComparisonLimit(
                            "Duplicate same-file seed pair count exceeds "
                            "MAX_DUPLICATE_SAME_FILE_SEED_PAIRS="
                            f"{limits.same_file_seed_pairs}",
                            files_data[first_file].file_path,
                        )
                    if second_seed - first_seed < window_size:
                        continue
                else:
                    cross_seed_pairs += 1
                    if cross_seed_pairs > limits.cross_file_seed_pairs:
                        raise DuplicateComparisonLimit(
                            "Duplicate cross-file seed pair count exceeds "
                            "MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS="
                            f"{limits.cross_file_seed_pairs}"
                        )
                    cross_file_pairs.add((first_file, second_file))
                    if len(cross_file_pairs) > limits.cross_file_pairs:
                        raise DuplicateComparisonLimit(
                            "Duplicate cross-file candidate count exceeds "
                            f"MAX_DUPLICATE_CROSS_FILE_PAIRS={limits.cross_file_pairs}"
                        )

                alignment = (first_file, second_file, second_seed - first_seed)
                intervals = covered.get(alignment)
                if intervals is not None and _interval_contains(intervals, first_seed):
                    continue

                if not all(
                    equal(first_file, first_seed + offset, second_file, second_seed + offset)
                    for offset in range(window_size)
                ):
                    continue

                first_region = files_data[first_file].regions[first_seed]
                second_region = files_data[second_file].regions[second_seed]
                backward_limit = min(first_seed, second_seed)
                if first_file == second_file:
                    backward_limit = min(
                        backward_limit,
                        second_seed - first_seed - window_size,
                    )
                backward = 0
                while backward < backward_limit:
                    first_position = first_seed - backward - 1
                    second_position = second_seed - backward - 1
                    if (
                        files_data[first_file].regions[first_position] != first_region
                        or files_data[second_file].regions[second_position] != second_region
                        or not equal(
                            first_file,
                            first_position,
                            second_file,
                            second_position,
                        )
                    ):
                        break
                    backward += 1

                first_start = first_seed - backward
                second_start = second_seed - backward
                size = window_size + backward
                first_length = len(files_data[first_file].indexed)
                second_length = len(files_data[second_file].indexed)
                overlap_limit = second_start - first_start if first_file == second_file else None
                while (
                    first_start + size < first_length
                    and second_start + size < second_length
                    and (overlap_limit is None or size < overlap_limit)
                    and files_data[first_file].regions[first_start + size] == first_region
                    and files_data[second_file].regions[second_start + size] == second_region
                    and equal(
                        first_file,
                        first_start + size,
                        second_file,
                        second_start + size,
                    )
                ):
                    size += 1

                intervals = covered.setdefault(alignment, [])
                _add_interval(intervals, first_start, first_start + size - 1)
                first_indexed = files_data[first_file].indexed
                second_indexed = files_data[second_file].indexed
                _append_bounded(
                    raw_matches,
                    (
                        first_file,
                        first_indexed[first_start][0],
                        first_indexed[first_start + size - 1][0],
                        second_file,
                        second_indexed[second_start][0],
                        second_indexed[second_start + size - 1][0],
                        size,
                    ),
                    limits,
                )

    return raw_matches


def filter_subsumed_matches(raw_matches: list[MatchPair]) -> list[MatchPair]:
    """Keep maximal matches and drop regions overlapping a larger pair on both sides."""

    raw_matches.sort(key=lambda item: item[6], reverse=True)
    filtered: list[MatchPair] = []
    for match in raw_matches:
        first_file, first_start, first_end, second_file, second_start, second_end, _ = match
        redundant = False
        for kept in filtered:
            kept_first_file, kept_first_start, kept_first_end = kept[:3]
            kept_second_file, kept_second_start, kept_second_end = kept[3:6]
            if first_file != kept_first_file or second_file != kept_second_file:
                continue
            overlaps_first = not (first_end < kept_first_start or first_start > kept_first_end)
            overlaps_second = not (second_end < kept_second_start or second_start > kept_second_end)
            if overlaps_first and overlaps_second:
                redundant = True
                break
        if not redundant:
            filtered.append(match)
    return filtered
