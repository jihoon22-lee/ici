"""Development-only capture: frozen run TEM metrics and limitations (application/report.py:92).

Feeds synthetic observations to frozen `ici.application.report.tem_metrics` and records the
metrics and limitations it appends to every next run. Never called by the Rust product.

Run from a neutral directory: PYTHONPATH=<oracle>/src <venv python> -s capture_tem_metrics.py
<out.json> (not -I, which would ignore PYTHONPATH and import the checkout instead).
"""

import json
import subprocess
import sys
from pathlib import Path

from ici.application.report import tem_metrics
from ici.domain.enums import TaskState
from ici.domain.observation import Measurement, Observation

EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"


def pair(name, numerator, denominator):
    if not denominator:
        # Frozen refuses a numerator over a zero denominator: an empty run is a bare zero.
        return {"name": name, "value": 0.0, "denominator": 0}
    value = numerator / denominator * 100.0
    return {"name": name, "value": value, "numerator": numerator, "denominator": denominator}


def percent(name, value):
    return {"name": name, "value": value}


CASES = {
    "python-lines-and-functions": [
        ["app.python.test", [pair("pytest.cases", 9, 10)]],
        [
            "app.python.coverage",
            [pair("coverage.lines", 70, 100), pair("coverage.functions", 8, 10)],
        ],
        ["app.python.line", [percent("lines_total", 120.0)]],
    ],
    "python-no-functions": [
        ["app.python.test", [pair("pytest.cases", 3, 3)]],
        ["app.python.coverage", [pair("coverage.lines", 50, 100)]],
    ],
    "no-coverage": [
        ["project.python.lint", []],
        ["project.python.line", [percent("lines_total", 4.0)]],
    ],
    "two-components-merge": [
        ["a.python.test", [pair("pytest.cases", 4, 5)]],
        [
            "a.python.coverage",
            [
                pair("coverage.lines", 30, 40),
                pair("coverage.branches", 5, 10),
                pair("coverage.functions", 3, 4),
            ],
        ],
        ["b.cpp.test.suite", [pair("ctest.cases", 2, 2)]],
        [
            "b.cpp.coverage",
            [pair("coverage.cpp.branches", 1, 4), pair("coverage.cpp.functions", 1, 2)],
        ],
    ],
    "percent-only-excluded": [
        ["a.python.test", [pair("pytest.cases", 1, 1)]],
        [
            "a.python.coverage",
            [pair("coverage.lines", 9, 10), pair("coverage.functions", 1, 1)],
        ],
        ["b.python.test", [pair("pytest.cases", 2, 4)]],
        [
            "b.python.coverage",
            [percent("coverage.lines", 55.5), percent("coverage.functions", 75.0)],
        ],
    ],
    "workspace-not-scored": [
        ["a.python.test", [pair("pytest.cases", 1, 1)]],
        [
            "a.python.coverage",
            [pair("coverage.lines", 9, 10), pair("coverage.functions", 1, 1)],
        ],
        ["b.cpp.test", [pair("ctest.cases", 1, 2)]],
        ["b.cpp.coverage", [pair("coverage.cpp.lines", 3, 4)]],
        [
            "c.cpp.coverage",
            [pair("coverage.cpp.lines", 1, 4), percent("coverage.cpp.functions", 50.0)],
        ],
    ],
    "unmarked-and-zero-cases": [
        ["vcs.status", []],
        ["build.release", [pair("make.cases", 0, 0)]],
        ["app.python.test", [pair("pytest.cases", 0, 0)]],
        [
            "app.python.coverage",
            [pair("coverage.lines", 1, 2), pair("coverage.functions", 1, 2)],
        ],
    ],
    "invalid-counts": [
        ["app.python.test", [pair("pytest.cases", 5, 3)]],
        [
            "app.python.coverage",
            [pair("coverage.lines", 1, 2), pair("coverage.functions", 1, 2)],
        ],
    ],
    "branch-and-last-wins": [
        ["app.cpp.coverage", [pair("coverage.cpp.branches", 30, 100)]],
        ["app.cpp.test", [pair("ctest.cases", 7, 8)]],
        [
            "app.cpp.coverage.extra",
            [pair("coverage.cpp.branches", 90, 100), pair("coverage.cpp.functions", 2, 3)],
        ],
    ],
}


def main() -> None:
    oracle = Path(sys.modules["ici"].__file__).resolve().parents[2]
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != EXPECTED:
        raise SystemExit(f"oracle must be {EXPECTED}, found {revision}")
    cases = {}
    for name, observations in CASES.items():
        built = tuple(
            Observation(
                task_id=task_id,
                provider="capture",
                state=TaskState.SUCCEEDED,
                measurements=tuple(Measurement(**item) for item in measurements),
            )
            for task_id, measurements in observations
        )
        metrics, limitations = tem_metrics(built)
        cases[name] = {
            "observations": observations,
            "metrics": [
                {"name": item.name, "value": item.value, "unit": item.unit} for item in metrics
            ],
            "limitations": list(limitations),
        }
    document = {"oracle": EXPECTED, "cases": cases}
    Path(sys.argv[1]).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
