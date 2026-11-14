"""Development-only capture: frozen next-run Measurements rows (reporting/view_model.py:130).

Loads tests/rust-parity/domain/run-v2.json through the frozen reader with a synthetic metric
list, then records frozen `view_model(...).metrics` (name, `_number` value, unit, "n of d" raw)
and the offline HTML `_metrics` table. Never called by the Rust product.

Run from a neutral directory: PYTHONPATH=<oracle>/src <venv python> -s
capture_run_measurements.py <out.json> (not -I, which would ignore PYTHONPATH).
"""

import json
import subprocess
import sys
from pathlib import Path

from ici.domain.serialization import run_result_from_dict
from ici.reporting.offline_html import _metrics
from ici.reporting.view_model import view_model

EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
ROOT = Path(__file__).resolve().parents[3]
METRICS = [
    {"name": "lines_total", "value": 4.0, "unit": "lines", "evidence": "MEASURED"},
    {"name": "coverage.lines", "value": 70.0, "unit": "%", "numerator": 7, "denominator": 10},
    {"name": "ratio", "value": 0.1234567, "evidence": "ESTIMATED"},
    {"name": "big", "value": 1234567.5, "unit": "ms"},
    {"name": "tiny", "value": 0.00001234},
    {"name": "huge", "value": 1e21},
    {"name": "negative", "value": -2.5, "numerator": 0, "denominator": 4},
    {"name": "empty.cases", "value": 0.0, "denominator": 0},
    {"name": "third", "value": 1 / 3, "numerator": 1, "denominator": 3},
]


def main() -> None:
    oracle = Path(sys.modules["ici"].__file__).resolve().parents[2]
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != EXPECTED:
        raise SystemExit(f"oracle must be {EXPECTED}, found {revision}")
    document = json.loads((ROOT / "tests/rust-parity/domain/run-v2.json").read_text("utf-8"))
    document["metrics"] = [{"evidence": "MEASURED", **item} for item in METRICS]
    view = view_model(run_result_from_dict(document))
    output = {
        "oracle": EXPECTED,
        "document": document,
        "rows": [[row.name, row.value, row.unit, row.raw] for row in view.metrics],
        "html": _metrics(view),
    }
    Path(sys.argv[1]).write_text(json.dumps(output, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
