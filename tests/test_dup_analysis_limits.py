"""Fail-closed comparison budgets for duplicate analysis.

Each case uses a small number of actionable function-shaped records.  The
limits are internal invariants, so tests inject them by monkeypatching the
engine module rather than by adding user-facing configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ici.core.models import EngineStatus, EvidenceState
from ici.engines import dup as dup_module
from ici.engines.dup import DuplicateEngine

_COMPARISON_LIMITS = (
    "MAX_DUPLICATE_WINDOW_OCCURRENCES",
    "MAX_DUPLICATE_SAME_FILE_SEED_PAIRS",
    "MAX_DUPLICATE_CROSS_FILE_PAIRS",
    "MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS",
    "MAX_DUPLICATE_EXTENSION_COMPARISONS",
    "MAX_DUPLICATE_RAW_MATCHES",
)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_duplicate_engine(root: Path):
    return DuplicateEngine(
        root,
        {
            "project": {"source_dirs": ["src"]},
            "engines": {
                "dup": {
                    "min_window": 6,
                    "warn_pct": 0.0,
                    "fail_pct": 100.0,
                    "mode": "pass_warn",
                }
            },
        },
    ).run()


def _set_comparison_limits(monkeypatch: pytest.MonkeyPatch, target: str, value: int) -> None:
    for name in _COMPARISON_LIMITS:
        monkeypatch.setattr(dup_module, name, value if name == target else 1_000_000)


def _assert_deterministic_comparison_limit_error(first, second, limit_name: str) -> None:
    assert first.status == EngineStatus.ERROR
    assert first.evidence == EvidenceState.NOT_RUN
    assert second.status == EngineStatus.ERROR
    assert second.evidence == EvidenceState.NOT_RUN

    first_errors = [
        target for target in first.targets if target.target_name == "DuplicateComparisonLimit"
    ]
    second_errors = [
        target for target in second.targets if target.target_name == "DuplicateComparisonLimit"
    ]
    assert len(first_errors) == 1
    assert len(second_errors) == 1
    assert first_errors[0].status == EngineStatus.ERROR
    assert second_errors[0].status == EngineStatus.ERROR
    assert first_errors[0].message
    assert limit_name in first_errors[0].message
    assert first_errors[0].message == second_errors[0].message
    assert all(target.status == EngineStatus.ERROR for target in first.targets)
    assert all(target.status == EngineStatus.ERROR for target in second.targets)


def _python_function(name: str, parameter: str, local: str, literal: int) -> str:
    return f"""def {name}({parameter}):
    if {parameter} > 0:
        {local} = {parameter} + {literal}
        {local} += 2
        {local} += 3
        return {local}
    return {parameter}
"""


def test_window_occurrence_budget_fails_closed_before_occurrence_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for index in range(3):
        _write(
            tmp_path,
            f"src/clone_{index}.py",
            _python_function(f"classify_{index}", f"value_{index}", f"total_{index}", 10 + index),
        )
    _set_comparison_limits(monkeypatch, "MAX_DUPLICATE_WINDOW_OCCURRENCES", 2)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    _assert_deterministic_comparison_limit_error(first, second, "MAX_DUPLICATE_WINDOW_OCCURRENCES")


def test_same_file_seed_pair_budget_fails_closed_for_repeated_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repeated = (
        "\n".join(
            _python_function(
                f"classify_{index}", f"value_{index}", f"total_{index}", 10 + index
            ).rstrip()
            for index in range(3)
        )
        + "\n"
    )
    _write(tmp_path, "src/repeated.py", repeated)
    _write(
        tmp_path,
        "src/unrelated.py",
        _python_function("different", "item", "result", 100).replace(
            "if item > 0:", "if item < 0:"
        ),
    )
    _set_comparison_limits(monkeypatch, "MAX_DUPLICATE_SAME_FILE_SEED_PAIRS", 1)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    _assert_deterministic_comparison_limit_error(
        first, second, "MAX_DUPLICATE_SAME_FILE_SEED_PAIRS"
    )


def test_cross_file_pair_budget_fails_closed_for_three_matching_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for index in range(3):
        _write(
            tmp_path,
            f"src/clone_{index}.py",
            _python_function(f"classify_{index}", f"value_{index}", f"total_{index}", 10 + index),
        )
    _set_comparison_limits(monkeypatch, "MAX_DUPLICATE_CROSS_FILE_PAIRS", 2)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    _assert_deterministic_comparison_limit_error(first, second, "MAX_DUPLICATE_CROSS_FILE_PAIRS")


@pytest.mark.parametrize(
    ("limit_name", "limit_value"),
    [
        ("MAX_DUPLICATE_CROSS_FILE_SEED_PAIRS", 1),
        ("MAX_DUPLICATE_EXTENSION_COMPARISONS", 1),
    ],
    ids=["cross-file-seed-pairs", "seed-extension-comparisons"],
)
def test_cross_file_comparison_budgets_fail_closed_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit_value: int,
):
    for index in range(3):
        _write(
            tmp_path,
            f"src/clone_{index}.py",
            _python_function(f"classify_{index}", f"value_{index}", f"total_{index}", 10 + index),
        )
    _set_comparison_limits(monkeypatch, limit_name, limit_value)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    _assert_deterministic_comparison_limit_error(first, second, limit_name)


def test_raw_match_budget_fails_closed_before_report_inventory_growth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    for prefix, comparison in (("positive", ">"), ("negative", "<")):
        for index in range(2):
            source = _python_function(
                f"{prefix}_{index}",
                f"value_{index}",
                f"total_{index}",
                10 + index,
            ).replace("> 0", f"{comparison} 0")
            _write(tmp_path, f"src/{prefix}_{index}.py", source)
    _set_comparison_limits(monkeypatch, "MAX_DUPLICATE_RAW_MATCHES", 1)

    first = _run_duplicate_engine(tmp_path)
    second = _run_duplicate_engine(tmp_path)

    _assert_deterministic_comparison_limit_error(first, second, "MAX_DUPLICATE_RAW_MATCHES")
