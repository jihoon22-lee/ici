"""Contracts for bounded shared-seed duplicate matching.

The current internal matching API under test is
``ici.engines._dup_matching.find_raw_matches(files_data, window_size, limits)``
with ``DuplicateFileData`` and ``DuplicateMatchLimits`` records.  The engine
integration is ``DuplicateEngine.run``; when an internal comparison bound is
exceeded it must return ``EngineStatus.ERROR`` with
``EvidenceState.NOT_RUN`` and one ``InspectionTarget`` whose target name is
``DuplicateComparisonLimit``.

The expected internal module constants are
``MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS``,
``MAX_DUPLICATE_EXTENSION_COMPARISONS``, and
``MAX_DUPLICATE_INDEXED_RECORDS`` (alongside the existing window, pair, and
raw-match limits).  Cross-file matching must use shared actionable normalized
window seeds and bounded left/right extension, never
``difflib.SequenceMatcher``.  Physical blank/comment gaps are allowed, while
the region key supplied by ``python_duplicate_regions`` or
``cpp_duplicate_regions`` is a hard function/preprocessor/import boundary.
The rolling helpers under test are ``_window_hashes(indexed, window_size)`` and
its monkeypatchable per-record ``_record_hash(normalized)`` hook.

Fixtures are intentionally small and deterministic; no wall-clock assertion
or large source corpus is part of this contract.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines import _dup_matching as matching_module
from ici.engines import dup as dup_module
from ici.engines._dup_matching import (
    DuplicateFileData,
    DuplicateMatchLimits,
    find_raw_matches,
)
from ici.engines.dup import DuplicateEngine

_SAFE_LIMIT = 1_000_000
_COMPARISON_LIMIT_NAMES = (
    "MAX_DUPLICATE_WINDOW_OCCURRENCES",
    "MAX_DUPLICATE_SAME_FILE_SEED_PAIRS",
    "MAX_DUPLICATE_CROSS_FILE_PAIRS",
    "MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS",
    "MAX_DUPLICATE_EXTENSION_COMPARISONS",
    "MAX_DUPLICATE_RAW_MATCHES",
    "MAX_DUPLICATE_INDEXED_RECORDS",
)


def _synthetic_file(
    file_path: str,
    indexed: list[tuple[int, str]],
    regions: tuple[tuple[int, int], ...],
) -> DuplicateFileData:
    """Build a small indexed file with physical lines between records."""

    last_line = indexed[-1][0]
    raw_lines = [f"# physical line {line}\n" for line in range(1, last_line + 1)]
    return DuplicateFileData(file_path, "python", raw_lines, indexed, regions)


def _match_limits(**overrides: int) -> DuplicateMatchLimits:
    return DuplicateMatchLimits(
        window_occurrences=overrides.get("window_occurrences", _SAFE_LIMIT),
        same_file_seed_pairs=overrides.get("same_file_seed_pairs", _SAFE_LIMIT),
        cross_file_pairs=overrides.get("cross_file_pairs", _SAFE_LIMIT),
        cross_file_seed_pairs=overrides.get("cross_file_seed_pairs", _SAFE_LIMIT),
        extension_comparisons=overrides.get("extension_comparisons", _SAFE_LIMIT),
        raw_matches=overrides.get("raw_matches", _SAFE_LIMIT),
    )


def test_shared_actionable_seed_extends_over_gaps_and_deduplicates_maximal_match(
    monkeypatch: pytest.MonkeyPatch,
):
    """Overlapping seeds produce one maximal region without SequenceMatcher."""

    def forbidden_sequence_matcher(*_args, **_kwargs):
        raise AssertionError("cross-file duplicate matching must not use SequenceMatcher")

    monkeypatch.setattr(difflib, "SequenceMatcher", forbidden_sequence_matcher)

    # The six shared actionable records have physical blank/comment gaps.  The
    # equal first/last tokens deliberately make only the region transition,
    # rather than token inequality, stop left/right extension.
    shared = [f"NAME(shared_{index})" for index in range(6)]
    left_indexed = [
        (1, shared[0]),
        (3, shared[0]),
        (4, shared[1]),
        (6, shared[2]),
        (8, shared[3]),
        (9, shared[4]),
        (11, shared[5]),
        (13, shared[5]),
    ]
    right_indexed = [
        (1, shared[0]),
        (4, shared[0]),
        (5, shared[1]),
        (7, shared[2]),
        (9, shared[3]),
        (10, shared[4]),
        (12, shared[5]),
        (14, shared[5]),
    ]
    regions = ((0, 0), *((1, 0),) * len(shared), (2, 0))

    matches = find_raw_matches(
        [
            _synthetic_file("src/left.py", left_indexed, regions),
            _synthetic_file("src/right.py", right_indexed, regions),
        ],
        window_size=4,
        # There are several overlapping shared seeds, but only one maximal
        # match.  A raw-match budget of one makes pre-deduplication observable.
        limits=_match_limits(raw_matches=1),
    )

    assert matches == [(0, 3, 11, 1, 4, 12, 6)]


def test_rolling_hash_collision_still_requires_normalized_window_equality(
    monkeypatch: pytest.MonkeyPatch,
):
    """A forced rolling-hash collision cannot turn distinct windows into clones."""

    monkeypatch.setattr(matching_module, "_record_hash", lambda _normalized: 0)
    left_records = [(line, f"NAME(left_{line})") for line in range(1, 5)]
    right_records = [(line, f"NAME(right_{line})") for line in range(1, 5)]
    regions = ((1, 0),) * 4

    matches = find_raw_matches(
        [
            _synthetic_file("src/left.py", left_records, regions),
            _synthetic_file("src/right.py", right_records, regions),
        ],
        window_size=3,
        limits=_match_limits(),
    )

    assert matches == []


def test_huge_window_size_returns_no_hashes_without_record_hash_work(
    monkeypatch: pytest.MonkeyPatch,
):
    """A huge ``min_window`` has an empty result without iterating its size."""

    hashed_records: list[str] = []

    def record_hash(normalized: str) -> int:
        hashed_records.append(normalized)
        return 0

    monkeypatch.setattr(matching_module, "_record_hash", record_hash)
    indexed = [(line, f"NAME(record_{line})") for line in range(1, 5)]

    hashes = list(matching_module._window_hashes(indexed, 10**9))

    assert hashes == []
    assert hashed_records == []


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_duplicate_engine(root: Path, min_window: int = 3):
    return DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {
                "dup": {
                    "min_window": min_window,
                    "warn_pct": 0.0,
                    "fail_pct": 100.0,
                    "mode": "pass_warn",
                }
            },
        },
    ).run()


def _repeated_python_function(name: str, parameter: str, local: str) -> str:
    statements = "".join(f"    {local} += {index}\n" for index in range(1, 8))
    return f"def {name}({parameter}):\n{statements}    return {local}\n"


def _set_comparison_limits(monkeypatch: pytest.MonkeyPatch, **overrides: int) -> None:
    for name in _COMPARISON_LIMIT_NAMES:
        monkeypatch.setattr(dup_module, name, overrides.get(name, _SAFE_LIMIT))


def _assert_deterministic_limit_error(first, second, marker: str) -> None:
    for result in (first, second):
        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN
        errors = [
            target for target in result.targets if target.target_name == "DuplicateComparisonLimit"
        ]
        assert len(errors) == 1
        assert errors[0].status == EngineStatus.ERROR
        assert marker in errors[0].message
        assert all(target.status == EngineStatus.ERROR for target in result.targets)

    first_error = next(
        target for target in first.targets if target.target_name == "DuplicateComparisonLimit"
    )
    second_error = next(
        target for target in second.targets if target.target_name == "DuplicateComparisonLimit"
    )
    assert first_error.file_path == second_error.file_path
    assert first_error.message == second_error.message


@pytest.mark.parametrize(
    ("limit_name", "limit_value"),
    [
        ("MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS", 1),
        ("MAX_DUPLICATE_EXTENSION_COMPARISONS", 2),
    ],
    ids=["cross-file-seed-pairs", "seed-extension-comparisons"],
)
def test_repeated_normalized_records_hit_comparison_limits_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit_value: int,
):
    """Repeated normalized records fail closed instead of growing comparisons."""

    _write(
        tmp_path,
        "src/a.py",
        _repeated_python_function("alpha", "value", "total"),
    )
    _write(
        tmp_path,
        "src/b.py",
        _repeated_python_function("beta", "amount", "result"),
    )
    _set_comparison_limits(monkeypatch, **{limit_name: limit_value})

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    _assert_deterministic_limit_error(first, second, limit_name)


def test_aggregate_indexed_record_limit_fails_closed_across_small_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Several individually small files still obey one aggregate record bound."""

    _write(tmp_path, "src/a.py", "alpha = 1\n")
    _write(tmp_path, "src/b.py", "beta = 2\n")
    _write(tmp_path, "src/c.py", "gamma = 3\n")
    _set_comparison_limits(monkeypatch, MAX_DUPLICATE_INDEXED_RECORDS=2)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    for result in (first, second):
        assert result.status == EngineStatus.ERROR
        assert result.evidence == EvidenceState.NOT_RUN
        errors = [
            target for target in result.targets if target.target_name == "SourceTokenizationError"
        ]
        assert len(errors) == 1
        assert errors[0].file_path == "src/c.py"
        assert errors[0].status == EngineStatus.ERROR
        assert "MAX_DUPLICATE_INDEXED_RECORDS" in errors[0].message
        assert all(target.status == EngineStatus.ERROR for target in result.targets)

    first_error = next(
        target for target in first.targets if target.target_name == "SourceTokenizationError"
    )
    second_error = next(
        target for target in second.targets if target.target_name == "SourceTokenizationError"
    )
    assert first_error.file_path == second_error.file_path == "src/c.py"
    assert first_error.message == second_error.message
