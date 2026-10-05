"""WP21 — the versioned TEM formula and its aggregation rules (#219).

The promises under test:

- the formula is the stable engine's own — pinned by fixed fixtures, not
  re-derived: ``cov_factor * func_cov/100 * pass_rate * 5`` with coverage
  capped at 80 % and the historical branch * 1.25 conversion
- nothing fabricates inputs: no tests, no coverage or no function coverage
  is an N/A with a recorded reason — never a 0 or 100 stand-in
- components merge through raw counts only; a component without them is
  named and excluded, and percentages are never averaged
"""

from __future__ import annotations

import pytest

from ici.application.tem import (
    FORMULA_VERSION,
    ComponentCounts,
    TemInputs,
    aggregate,
    calculate,
)


def test_full_marks_fixture() -> None:
    result = calculate(
        TemInputs(
            passed=10,
            total=10,
            line_coverage=100.0,
            function_coverage=100.0,
        )
    )

    # cov capped at 80: 80/80 * 1.0 * 1.0 * 5.0
    assert result.score == 5.0
    assert result.formula == FORMULA_VERSION
    assert result.coverage_label == "Line"
    assert result.estimated is False


def test_half_marks_fixture() -> None:
    result = calculate(
        TemInputs(
            passed=5,
            total=10,
            line_coverage=40.0,
            function_coverage=50.0,
        )
    )

    # (40/80) * 0.5 * 0.5 * 5.0 = 0.625
    assert result.score == 0.62
    assert result.pass_rate == 0.5
    assert result.coverage_shown == 40.0


def test_branch_coverage_uses_the_historical_conversion() -> None:
    result = calculate(
        TemInputs(
            passed=8,
            total=8,
            branch_coverage=64.0,
            function_coverage=100.0,
        )
    )

    # branch 64 * 1.25 = 80 → factor 1.0; pass_rate 1 → 5.0
    assert result.coverage_label == "Branch"
    assert result.score == 5.0


def test_line_coverage_wins_over_branch() -> None:
    result = calculate(
        TemInputs(
            passed=4,
            total=4,
            line_coverage=20.0,
            branch_coverage=64.0,
            function_coverage=100.0,
        )
    )

    # line 20 → factor 0.25 → 1.25; the real branch number does not feed in
    assert result.coverage_label == "Line"
    assert result.score == 1.25


def test_no_tests_means_zero_pass_rate() -> None:
    result = calculate(TemInputs(passed=0, total=0, line_coverage=80.0, function_coverage=100.0))

    assert result.score == 0.0
    assert result.pass_rate == 0.0


def test_missing_function_coverage_is_not_a_score() -> None:
    result = calculate(TemInputs(passed=10, total=10, line_coverage=90.0))

    assert result.score is None
    assert "function coverage" in result.reason


def test_missing_coverage_is_not_a_score() -> None:
    result = calculate(TemInputs(passed=10, total=10, function_coverage=100.0))

    assert result.score is None
    assert "no coverage" in result.reason


def test_estimated_coverage_is_marked() -> None:
    result = calculate(
        TemInputs(
            passed=10,
            total=10,
            function_coverage=50.0,
            estimated_coverage=80.0,
        )
    )

    assert result.estimated is True
    assert result.score == 2.5


@pytest.mark.parametrize(
    ("passed", "total"),
    [(-1, 10), (5, -10), (11, 10)],
)
def test_invalid_test_counts_are_refused(passed: int, total: int) -> None:
    result = calculate(TemInputs(passed=passed, total=total, line_coverage=50.0))

    assert result.score is None
    assert "invalid test counts" in result.reason


def test_aggregate_sums_counts_not_percentages() -> None:
    big = ComponentCounts(
        name="big",
        passed=90,
        total=100,
        covered_lines=900,
        total_lines=1000,
        covered_functions=45,
        total_functions=50,
    )
    small = ComponentCounts(
        name="small",
        passed=10,
        total=10,
        covered_lines=9,
        total_lines=10,
        covered_functions=5,
        total_functions=5,
    )

    merged, excluded = aggregate((big, small))

    assert excluded == ()
    # 909/1010 = 90.0% — not the average of 90% and 90%
    assert merged.line_coverage == pytest.approx(90.0)
    assert merged.passed == 100
    assert merged.total == 110
    assert merged.function_coverage == pytest.approx(50 / 55 * 100.0)


def test_aggregate_does_not_average_skewed_percentages() -> None:
    big = ComponentCounts(
        name="big",
        passed=100,
        total=100,
        covered_lines=500,
        total_lines=1000,
        covered_functions=50,
        total_functions=100,
    )
    small = ComponentCounts(
        name="small",
        passed=10,
        total=10,
        covered_lines=10,
        total_lines=10,
        covered_functions=10,
        total_functions=10,
    )

    merged, _ = aggregate((big, small))

    # raw merge: 510/1010 ≈ 50.5 — a naive % average would say 75
    assert merged.line_coverage == pytest.approx(510 / 1010 * 100.0)
    assert merged.line_coverage != pytest.approx(75.0)


def test_unmergeable_component_is_named_and_excluded() -> None:
    measured = ComponentCounts(name="core", passed=5, total=5, covered_lines=40, total_lines=50)
    bare = ComponentCounts(name="docs", passed=0, total=0)

    merged, excluded = aggregate((measured, bare))

    assert excluded == ("docs",)
    assert merged.line_coverage == pytest.approx(80.0)
    assert merged.passed == 5


def test_workspace_score_stays_na_when_any_function_counts_missing() -> None:
    full = ComponentCounts(
        name="a",
        passed=5,
        total=5,
        covered_lines=8,
        total_lines=10,
        covered_functions=4,
        total_functions=5,
    )
    no_func = ComponentCounts(name="b", passed=5, total=5, covered_lines=8, total_lines=10)

    merged, _ = aggregate((full, no_func))
    result = calculate(merged)

    assert result.score is None
    assert "function coverage" in result.reason
