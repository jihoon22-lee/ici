"""Capture frozen v0.13.0 `ici next report --sarif` documents of stored run results.

    PYTHONPATH=<oracle>/src python capture_run_sarif.py <repo-root> <output.json>

Every `tests/fixtures/ici-next/run-*.json` result, plus derived variants (a comparable and an
incompatible baseline, a suppressed finding, a span without columns, an unknown severity), is
read with the frozen `ici.domain.serialization.loads` and written with the frozen
`ici.reporting.sarif.document`.
"""

import copy
import json
import sys
from pathlib import Path

from ici.domain.serialization import loads
from ici.reporting.sarif import document


def variants(name, value):
    yield name, value
    if not value.get("findings"):
        return
    first = value["findings"][0]["fingerprint"]
    comparable = copy.deepcopy(value)
    comparable["baseline"] = {
        "state": "comparable",
        "origin": "base.json",
        "new": [first],
        "unchanged": [],
        "resolved": ["sha256:" + "1" * 64],
        "carried": ["sha256:" + "2" * 64],
        "reason": "",
    }
    yield f"{name}+baseline-comparable", comparable
    incompatible = copy.deepcopy(value)
    incompatible["baseline"] = {
        "state": "incompatible",
        "origin": "base.json",
        "new": [],
        "unchanged": [],
        "resolved": [],
        "carried": [],
        "reason": "policy changed since the baseline",
    }
    yield f"{name}+baseline-incompatible", incompatible
    marked = copy.deepcopy(value)
    finding = copy.deepcopy(marked["findings"][0])
    finding["fingerprint"] = "sha256:" + "3" * 64
    marked["findings"].append(finding)
    finding["suppression"] = {
        "suppressed": True,
        "kind": "config",
        "reason": "accepted <risk>",
        "origin": "ici.toml",
    }
    span = finding["primary_location"]
    for key in ("start_column", "end_column", "end_line"):
        span.pop(key, None)
    finding["severity"] = "unheard-of"
    yield f"{name}+suppressed-bare-span-unknown-severity", marked


def main():
    repo, output = Path(sys.argv[1]), Path(sys.argv[2])
    cases = {}
    for path in sorted((repo / "tests/fixtures/ici-next").glob("run-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for name, value in variants(path.name, raw):
            try:
                result = loads(json.dumps(value))
            except (ValueError, TypeError) as error:
                cases[name] = {"input": value, "refused": str(error)}
                continue
            cases[name] = {"input": value, "sarif": document(result)}
    with output.open("w", encoding="utf-8") as stream:
        json.dump({"cases": cases}, stream, indent=1, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    print({name: "refused" in case for name, case in cases.items()})


if __name__ == "__main__":
    main()
