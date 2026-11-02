"""Development-only frozen TEM formula boundaries; never called by the Rust product.

Imports the frozen oracle's `ici.application.tem.calculate` and the legacy engine wrapper
`ici.analysis.coverage_support.calculate_tem` and records their result for a grid of inputs:
zero and partial test counts, the 80 % coverage cap (line, branch x 1.25), missing line /
branch / function evidence, the estimated fallback, rounding ties of the score (2 places) and
the pass rate (4 places), and invalid counts. The Rust comparison is
`crates/ici-validation/tests/tem_boundaries.rs`.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.analysis.coverage_support import calculate_tem  # noqa: E402
from ici.application.tem import TemInputs, calculate  # noqa: E402

COUNTS = [(0, 0), (1, 2), (1, 3), (2, 3), (7, 9), (5, 3)]
PERCENTS = [None, 0.0, 50.0, 79.9, 80.0, 80.1, 100.0]


def result(value) -> dict:
    return {
        "score": value.score,
        "formula": value.formula,
        "pass_rate": value.pass_rate,
        "coverage_label": value.coverage_label,
        "coverage_shown": value.coverage_shown,
        "estimated": value.estimated,
        "reason": value.reason,
    }


def main() -> None:
    formula = []
    for passed, total in COUNTS:
        for line in PERCENTS:
            for branch in (None, 0.0, 64.0, 100.0):
                for function in (None, 0.0, 66.66666666666667, 100.0):
                    for estimated in (None, 45.0):
                        inputs = {
                            "passed": passed,
                            "total": total,
                            "line_coverage": line,
                            "branch_coverage": branch,
                            "function_coverage": function,
                            "estimated_coverage": estimated,
                        }
                        formula.append(
                            {"inputs": inputs, "result": result(calculate(TemInputs(**inputs)))}
                        )
    legacy = []
    for passed, total in COUNTS[:-1]:
        for totals in (
            None,
            {"cover": 62.5, "branch_cover": None},
            {"cover": None, "branch_cover": 64.0},
            {"cover": None, "branch_cover": None},
            {"cover": 100.0, "branch_cover": 50.0},
        ):
            for branch_cov, func_cov in ((45.0, 50.0), (85.0, 95.0), (100.0, 100.0)):
                legacy.append(
                    {
                        "inputs": {
                            "branch_cov": branch_cov,
                            "func_cov": func_cov,
                            "passed": passed,
                            "total": total,
                            "coverage_totals": totals,
                        },
                        "result": calculate_tem(branch_cov, func_cov, passed, total, totals),
                    }
                )
    out = ROOT / "tests/rust-parity/validation/tem-boundaries-oracle.json"
    # One compact case per line keeps the golden reviewable and small.
    lines = ['{"oracle": ' + json.dumps(EXPECTED) + ","]
    for key, rows in (("formula", formula), ("legacy", legacy)):
        body = ",\n".join(json.dumps(row, separators=(",", ":")) for row in rows)
        lines.append(f'"{key}": [\n{body}\n]' + ("," if key == "formula" else "}"))
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(len(formula), len(legacy))


if __name__ == "__main__":
    main()
