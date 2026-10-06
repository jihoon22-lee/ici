#!/usr/bin/env python3
"""Assert numeric floors on a stored ``ici.next.run`` result.

The next gate fails on blocking findings; heuristic checks (duplication,
complexity, line size) are advisory in this repository, so the numeric
contract stable enforced through warn/fail bands is carried here as
explicit ratchet floors — measured values may not regress below them.

A missing metric is a failure, not a skip: a floor that cannot see its
measurement is a gate that was never asked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ratchet floors — set just under the values first measured by the next
# providers on this tree (2026-10). They exist to stop backsliding, not to
# reproduce stable's exact instrumentation, which measured these dimensions
# differently (the repo's stable floors were 86/78/94/TEM 4.7; the viewer's
# were branch 80 / function 90 under gcov's own accounting).
FLOOR_SETS: dict[str, dict[str, float]] = {
    # repo: lines 87.9, branches 68.8, functions 85.0, TEM 4.24, dup 10.7%
    "repo": {
        "coverage.lines": 85.0,
        "coverage.branches": 65.0,
        "coverage.functions": 83.0,
        "tem.ici": 4.0,
    },
    # viewer: lines 63.9, branches 41.4, functions 64.9, TEM 2.59, dup 1.7%
    "viewer": {
        "coverage.cpp.lines": 60.0,
        "coverage.cpp.branches": 38.0,
        "coverage.cpp.functions": 62.0,
        "tem.viewer": 2.4,
    },
}
DUPLICATED_LINES_CEILING = 12.0  # percent of measured lines
TEST_CASE_METRICS = {"repo": "pytest.cases", "viewer": "ctest.cases"}

# Ceilings — a measured value may not exceed these. They carry stable's fail
# bands (cyclomatic 25 / cognitive 60) into the next result contract; the
# advisory findings the metrics checks emit repeat the same numbers.
CEILING_SETS: dict[str, dict[str, float]] = {
    # measured 2026-10: complexity 24, cognitive 48
    "repo": {"max_complexity": 25.0, "max_cognitive": 60.0},
    "viewer": {},
}


def _metrics(result: dict) -> dict[str, dict]:
    metrics = result.get("metrics")
    if not isinstance(metrics, list):
        raise SystemExit("result carries no metrics list — nothing to floor")
    by_name: dict[str, dict] = {}
    for entry in metrics:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            by_name[entry["name"]] = entry
    return by_name


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--set=")]
    selected = next((a.partition("=")[2] for a in argv[1:] if a.startswith("--set=")), "repo")
    floors = FLOOR_SETS.get(selected)
    if floors is None or len(args) != 1:
        raise SystemExit(
            f"usage: {argv[0]} [--set={'|'.join(FLOOR_SETS)}] <ici.next.run result.json>"
        )
    try:
        result = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read result {args[0]}: {error}") from error
    metrics = _metrics(result)

    failures: list[str] = []
    for name, floor in floors.items():
        entry = metrics.get(name)
        value = entry.get("value") if entry else None
        if not isinstance(value, (int, float)):
            failures.append(f"{name}: metric absent or non-numeric (floor {floor})")
            continue
        if value < floor:
            failures.append(f"{name}: {value} < floor {floor}")

    for name, ceiling in CEILING_SETS[selected].items():
        entry = metrics.get(name)
        value = entry.get("value") if entry else None
        if not isinstance(value, (int, float)):
            failures.append(f"{name}: metric absent or non-numeric (ceiling {ceiling})")
            continue
        if value > ceiling:
            failures.append(f"{name}: {value} > ceiling {ceiling}")

    dup = metrics.get("duplicated_lines")
    if (
        not dup
        or not isinstance(dup.get("numerator"), int)
        or not isinstance(dup.get("denominator"), int)
    ):
        failures.append("duplicated_lines: ratio not measurable")
    elif dup["denominator"]:
        ratio = 100.0 * dup["numerator"] / dup["denominator"]
        if ratio > DUPLICATED_LINES_CEILING:
            failures.append(f"duplicated_lines: {ratio:.1f}% > ceiling {DUPLICATED_LINES_CEILING}%")

    cases_metric = TEST_CASE_METRICS[selected]
    cases = metrics.get(cases_metric)
    if not cases or not isinstance(cases.get("denominator"), int):
        failures.append(f"{cases_metric}: test evidence absent")
    elif cases["denominator"] == 0:
        failures.append(f"{cases_metric}: zero tests collected")

    for failure in failures:
        print(f"floor-fail: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "floors ok: "
        + ", ".join(f"{name}={metrics[name]['value']}" for name in floors)
        + f", dup={100.0 * dup['numerator'] / dup['denominator']:.1f}%"
        + f", {cases_metric}={cases['numerator']}/{cases['denominator']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
