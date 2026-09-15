"""TEM — the weighted test-effectiveness score, as one versioned formula.

The score itself is exactly what the stable ``test`` engine has always
computed (#219 item 1: the formula is extracted, not changed):

    TEM = cov_factor * (func_cov / 100) * pass_rate * 5.0

- ``cov_factor`` is the coverage input capped at 80 %: line coverage when it
  exists, else real branch coverage scaled by the historical ``* 1.25``
  conversion (that factor has no recorded justification — it is preserved,
  not endorsed, and this docstring is the record), else the caller's own
  estimated branch figure.
- ``pass_rate`` is ``passed / total`` — 0 when nothing ran. A suite that did
  not run cannot produce a score by defaulting to full marks.
- ``func_cov`` is function coverage in percent. **There is no default** —
  when the evidence did not measure functions the answer is ``None`` with a
  recorded reason, never a fabricated 100 (#219 acceptance 2).

Aggregation (#219 item 3): component scores may only merge through raw
counts — summed covered/total lines, branches, functions and test cases —
never by averaging the percentages. A component that cannot supply raw
counts is reported separately, not blended into a denominator it did not
earn.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "FORMULA_VERSION",
    "MAX_SCORE",
    "ComponentCounts",
    "TemInputs",
    "TemResult",
    "aggregate",
    "calculate",
    "component_of",
    "counts_from",
    "inputs_from",
]

#: Bumped only when the formula or its inputs change meaning — never for a
#: refactor. ``1`` labels the formula the stable engine has always run.
FORMULA_VERSION = "tem/1"

#: The formula's own scale: a perfect score is 5.0.
MAX_SCORE = 5.0

#: Coverage contributes linearly up to this percent; beyond it adds nothing.
_COVERAGE_CAP = 80.0

#: Historical conversion used when only branch coverage is real: branch
#: coverage is treated as a stricter measure, so it is inflated by this
#: factor before capping. Preserved verbatim from the stable engine — the
#: original justification is not recorded anywhere in the repository.
_BRANCH_TO_LINE = 1.25


@dataclass(frozen=True)
class TemInputs:
    """The evidence one TEM calculation consumes, in percent.

    ``None`` means "not measured" — distinct from 0, which means "measured
    and nothing covered".
    """

    passed: int
    total: int
    line_coverage: float | None = None
    branch_coverage: float | None = None
    #: Function coverage percent — required; absent means the score is N/A.
    function_coverage: float | None = None
    #: The caller's own estimated coverage percent — last resort only,
    #: marked estimated in the result.
    estimated_coverage: float | None = None


@dataclass(frozen=True)
class ComponentCounts:
    """One component's raw evidence — the only mergeable form."""

    name: str
    passed: int
    total: int
    covered_lines: int | None = None
    total_lines: int | None = None
    covered_branches: int | None = None
    total_branches: int | None = None
    covered_functions: int | None = None
    total_functions: int | None = None


@dataclass(frozen=True)
class TemResult:
    """The score plus enough to reproduce it — never a bare number."""

    #: ``None`` when the inputs cannot honestly produce a score.
    score: float | None
    formula: str
    pass_rate: float
    coverage_label: str  # "Line" | "Branch" | ""
    coverage_shown: float | None  # the percent that fed cov_factor
    estimated: bool  # the coverage input was the caller's estimate
    reason: str = ""  # why score is None, when it is


def calculate(inputs: TemInputs) -> TemResult:
    """Apply the versioned formula — or refuse it, with the reason kept."""

    if inputs.passed < 0 or inputs.total < 0 or inputs.passed > inputs.total:
        return TemResult(
            score=None,
            formula=FORMULA_VERSION,
            pass_rate=0.0,
            coverage_label="",
            coverage_shown=None,
            estimated=False,
            reason=f"invalid test counts (passed={inputs.passed}, total={inputs.total})",
        )
    pass_rate = inputs.passed / inputs.total if inputs.total else 0.0

    if inputs.line_coverage is not None:
        cov_factor = min(_COVERAGE_CAP, inputs.line_coverage) / _COVERAGE_CAP
        label, shown, estimated = "Line", inputs.line_coverage, False
    elif inputs.branch_coverage is not None:
        cov_factor = min(_COVERAGE_CAP, inputs.branch_coverage * _BRANCH_TO_LINE) / _COVERAGE_CAP
        label, shown, estimated = "Branch", inputs.branch_coverage, False
    elif inputs.estimated_coverage is not None:
        cov_factor = min(_COVERAGE_CAP, inputs.estimated_coverage) / _COVERAGE_CAP
        label, shown, estimated = "Line", inputs.estimated_coverage, True
    else:
        return TemResult(
            score=None,
            formula=FORMULA_VERSION,
            pass_rate=pass_rate,
            coverage_label="",
            coverage_shown=None,
            estimated=False,
            reason="no coverage evidence — the score is not computed",
        )

    if inputs.function_coverage is None:
        return TemResult(
            score=None,
            formula=FORMULA_VERSION,
            pass_rate=pass_rate,
            coverage_label=label,
            coverage_shown=shown,
            estimated=estimated,
            reason="function coverage was not measured — the score is not computed",
        )

    score = cov_factor * (inputs.function_coverage / 100.0) * pass_rate * MAX_SCORE
    return TemResult(
        score=max(0.0, min(MAX_SCORE, round(score, 2))),
        formula=FORMULA_VERSION,
        pass_rate=round(pass_rate, 4),
        coverage_label=label,
        coverage_shown=shown,
        estimated=estimated,
    )


def aggregate(
    components: tuple[ComponentCounts, ...],
) -> tuple[TemInputs, tuple[str, ...]]:
    """Merge per-component raw counts into one workspace input.

    Only compatible raw counts are summed. A component that supplies neither
    line nor branch counts is named in the second return value and excluded
    from the merged input — it is reported separately rather than blended
    into a denominator it did not earn. When any merged component lacks
    function counts the merged input records ``None`` and the workspace
    score stays N/A.
    """

    mergeable = tuple(
        item
        for item in components
        if item.covered_lines is not None or item.covered_branches is not None
    )
    excluded = tuple(
        item.name
        for item in components
        if item.covered_lines is None and item.covered_branches is None
    )
    if not mergeable:
        return TemInputs(passed=0, total=0), excluded

    def _pct(pairs: list[tuple[int | None, int | None]]) -> float | None:
        numerator = sum(a for a, _ in pairs if a is not None)
        denominator = sum(b for _, b in pairs if b is not None)
        return numerator / denominator * 100.0 if denominator else None

    merged = TemInputs(
        passed=sum(item.passed for item in mergeable),
        total=sum(item.total for item in mergeable),
        line_coverage=_pct([(item.covered_lines, item.total_lines) for item in mergeable]),
        branch_coverage=_pct([(item.covered_branches, item.total_branches) for item in mergeable]),
        function_coverage=(
            _pct([(item.covered_functions, item.total_functions) for item in mergeable])
            # A component that measured coverage but not functions has
            # functions the merged percent cannot see — blending it in would
            # report a workspace figure that silently skips them.
            if all(
                item.covered_functions is not None and item.total_functions is not None
                for item in mergeable
            )
            else None
        ),
    )
    return merged, excluded


def component_of(task_id: str) -> str:
    """The component a task belongs to, from its ``{component}.{check}`` id."""

    for marker in (".python.", ".cpp."):
        if marker in task_id:
            return task_id.split(marker, 1)[0]
    return ""


def inputs_from(measurements) -> TemInputs:
    """One component's TEM inputs, read off its tasks' measurements.

    Test verdicts come from ``*.cases`` pairs; coverage from
    ``coverage.{lines,branches,functions}`` / ``coverage.cpp.*`` — raw
    numerator/denominator pairs win over bare percents, and a percent with
    no counts still counts as measured for a single component (it only loses
    mergeability, which :func:`aggregate` enforces separately).
    """

    passed = total = 0
    line_cov = branch_cov = func_cov = None
    for item in measurements:
        if item.name.endswith(".cases") and item.denominator:
            passed += item.numerator or 0
            total += item.denominator
        elif item.name in ("coverage.lines", "coverage.cpp.lines"):
            line_cov = (
                item.numerator / item.denominator * 100.0
                if item.numerator is not None and item.denominator
                else item.value
            )
        elif item.name in ("coverage.branches", "coverage.cpp.branches"):
            branch_cov = (
                item.numerator / item.denominator * 100.0
                if item.numerator is not None and item.denominator
                else item.value
            )
        elif item.name in ("coverage.functions", "coverage.cpp.functions"):
            func_cov = (
                item.numerator / item.denominator * 100.0
                if item.numerator is not None and item.denominator
                else item.value
            )
    return TemInputs(
        passed=passed,
        total=total,
        line_coverage=line_cov,
        branch_coverage=branch_cov,
        function_coverage=func_cov,
    )


def counts_from(name: str, measurements) -> ComponentCounts:
    """The mergeable raw form — counts only, no percent-only evidence."""

    passed = total = 0
    covered_lines = total_lines = None
    covered_branches = total_branches = None
    covered_functions = total_functions = None
    for item in measurements:
        if item.name.endswith(".cases") and item.denominator:
            passed += item.numerator or 0
            total += item.denominator
        elif item.name in ("coverage.lines", "coverage.cpp.lines"):
            covered_lines, total_lines = item.numerator, item.denominator
        elif item.name in ("coverage.branches", "coverage.cpp.branches"):
            covered_branches, total_branches = item.numerator, item.denominator
        elif item.name in ("coverage.functions", "coverage.cpp.functions"):
            covered_functions, total_functions = item.numerator, item.denominator
    return ComponentCounts(
        name=name,
        passed=passed,
        total=total,
        covered_lines=covered_lines,
        total_lines=total_lines,
        covered_branches=covered_branches,
        total_branches=total_branches,
        covered_functions=covered_functions,
        total_functions=total_functions,
    )
