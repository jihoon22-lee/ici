"""Development-only frozen capture: coverage.py JSON strictness of `parse_coverage_json`.

    python -I -B capture_coverage_json_strictness.py --oracle <oracle-root> --out <json>

Never called by Rust. Feeds crafted coverage.py JSON documents to the frozen module function
`ici.analysis.coverage_support.parse_coverage_json(json_path, project_root, expected_files)`
(oracle 6fe3e40, coverage_support.py:79-228) the way the legacy TestEngine calls it
(engines/test_coverage.py: `expected_files` = the discovered Python sources), and records
whether frozen accepted each document and, when it did, the line and branch counts it measured.
Root ruling 2026-11-14 (validation.json src/ici/analysis/coverage_support.py item 5): follow
frozen with captures; native-only refusals cite decisions.md "coverage.py JSON 입력의 추가 검증".

A case is raw JSON text (so non-standard tokens and duplicate keys stay as written) with `<ROOT>`
for the project root, or a generator (`{"files": N}`: N one-statement files f<i>.py, every one
expected), plus the expected project-relative files. The replay is
crates/ici-validation/tests/contracts.rs `coverage_json_strictness_follows_frozen`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
SUMMARY = {
    "covered_lines": 2,
    "num_statements": 3,
    "missing_lines": 1,
    "covered_branches": 1,
    "num_branches": 2,
}


def document(changes: dict[str, object] | None = None) -> dict:
    """A valid one-file report (main.py: lines 1, 2 run, 3 missed; one of two branches)."""

    files = {
        "main.py": {
            "executed_lines": [1, 2],
            "missing_lines": [3],
            "summary": dict(SUMMARY),
            "executed_branches": [[1, 2]],
            "missing_branches": [[1, 3]],
        }
    }
    value = {"meta": {"branch_coverage": True}, "files": files, "totals": dict(SUMMARY)}
    for path, item in (changes or {}).items():
        cursor = value
        keys = path.split("/")
        for key in keys[:-1]:
            cursor = cursor[key]
        if item is _DELETE:
            del cursor[keys[-1]]
        else:
            cursor[keys[-1]] = item
    return value


_DELETE = object()
MAIN = ["main.py"]
ZERO = {"covered_lines": 0, "num_statements": 0, "missing_lines": 0}


def text(value: dict) -> str:
    return json.dumps(value)


CASES: dict[str, dict] = {
    "valid": {"text": text(document()), "expected": MAIN},
    "absolute-name": {
        "text": text(document()).replace('"main.py"', '"<ROOT>/main.py"'),
        "expected": MAIN,
    },
    "no-branch-arcs": {
        "text": text(
            document(
                {
                    "files/main.py/executed_branches": _DELETE,
                    "files/main.py/missing_branches": _DELETE,
                }
            )
        ),
        "expected": MAIN,
    },
    "extra-key": {"text": text(document({"files/main.py/extra": {"x": 1}})), "expected": MAIN},
    # Frozen refusals (None): every one must be refused natively too.
    "not-json": {"text": "{", "expected": MAIN},
    "top-level-list": {"text": "[]", "expected": MAIN},
    "totals-missing": {"text": text(document({"totals": _DELETE})), "expected": MAIN},
    "totals-negative": {"text": text(document({"totals/num_branches": -1})), "expected": MAIN},
    "totals-float": {"text": text(document({"totals/num_statements": 3.0})), "expected": MAIN},
    "totals-bool": {"text": text(document({"totals/covered_branches": True})), "expected": MAIN},
    "covered-over-statements": {
        "text": text(document({"totals/covered_lines": 4, "totals/missing_lines": -1})),
        "expected": MAIN,
    },
    "parts-not-statements": {"text": text(document({"totals/missing_lines": 2})), "expected": MAIN},
    "branches-over-total": {
        "text": text(document({"totals/covered_branches": 3})),
        "expected": MAIN,
    },
    "files-empty": {"text": text(document({"files": {}})), "expected": MAIN},
    "file-summary-missing": {
        "text": text(document({"files/main.py/summary": _DELETE})),
        "expected": MAIN,
    },
    "executed-count-mismatch": {
        "text": text(document({"files/main.py/executed_lines": [1]})),
        "expected": MAIN,
    },
    "duplicate-line": {
        "text": text(document({"files/main.py/executed_lines": [1, 1]})),
        "expected": MAIN,
    },
    "zero-line": {
        "text": text(document({"files/main.py/executed_lines": [0, 2]})),
        "expected": MAIN,
    },
    "bool-line": {
        "text": text(document({"files/main.py/executed_lines": [True, 2]})),
        "expected": MAIN,
    },
    "lines-overlap": {
        "text": text(document({"files/main.py/missing_lines": [2]})),
        "expected": MAIN,
    },
    "totals-differ-from-files": {
        "text": text(document({"totals/covered_branches": 0})),
        "expected": MAIN,
    },
    "no-statements": {
        "text": text(
            document(
                {
                    "files/main.py/executed_lines": [],
                    "files/main.py/missing_lines": [],
                    **{f"files/main.py/summary/{key}": value for key, value in ZERO.items()},
                    **{f"totals/{key}": value for key, value in ZERO.items()},
                }
            )
        ),
        "expected": MAIN,
    },
    "extra-file": {
        "text": text(
            document(
                {
                    "files/other.py": {
                        "executed_lines": [],
                        "missing_lines": [],
                        "summary": {**ZERO, "num_branches": 0, "covered_branches": 0},
                    }
                }
            )
        ),
        "expected": MAIN,
    },
    "declared-file-missing": {"text": text(document()), "expected": ["main.py", "other.py"]},
    "duplicate-alias": {
        "text": text(document()).replace(
            '"main.py": {', '"<ROOT>/main.py": {"x": 0}, "main.py": {', 1
        ),
        "expected": MAIN,
    },
    "dot-relative-name": {
        "text": text(document()).replace('"main.py"', '"./main.py"'),
        "expected": MAIN,
    },
    "bom": {"text": "\ufeff" + text(document()), "expected": MAIN},
    # Malformed in ways frozen does not check (decisions.md "coverage.py JSON 입력의 추가 검증").
    "meta-branch-false": {
        "text": text(document({"meta/branch_coverage": False})),
        "expected": MAIN,
    },
    "meta-missing": {"text": text(document({"meta": _DELETE})), "expected": MAIN},
    "branch-arcs-not-list": {
        "text": text(document({"files/main.py/executed_branches": "arcs"})),
        "expected": MAIN,
    },
    "branch-arc-count-mismatch": {
        "text": text(document({"files/main.py/executed_branches": []})),
        "expected": MAIN,
    },
    "branch-arc-shape": {
        "text": text(document({"files/main.py/executed_branches": [[1]]})),
        "expected": MAIN,
    },
    "branch-arc-duplicate": {
        "text": text(
            document(
                {
                    "files/main.py/missing_branches": [[1, 3], [1, 3]],
                    "files/main.py/summary/num_branches": 3,
                    "totals/num_branches": 3,
                }
            )
        ),
        "expected": MAIN,
    },
    "line-past-u32": {
        "text": text(document({"files/main.py/executed_lines": [2**32, 2]})),
        "expected": MAIN,
    },
    "files-past-16384": {"generate": {"files": 16385}},
    # Malformed in ways neither frozen nor the approved row checks: recorded for a ruling.
    "nan-in-meta": {
        "text": text(document()).replace(
            '"branch_coverage": true', '"branch_coverage": true, "timestamp": NaN'
        ),
        "expected": MAIN,
    },
    "branches-past-u64": {
        "text": text(
            document(
                {
                    "files/main.py/summary/num_branches": 2**64,
                    "totals/num_branches": 2**64,
                    "files/main.py/executed_branches": _DELETE,
                    "files/main.py/missing_branches": _DELETE,
                }
            )
        ),
        "expected": MAIN,
    },
    "duplicate-file-key": {
        "text": text(document()).replace(
            '"main.py": {', '"main.py": {"summary": null}, "main.py": {', 1
        ),
        "expected": MAIN,
    },
}


def generated(spec: dict) -> tuple[str, list[str]]:
    count = spec["files"]
    one = {"covered_lines": 1, "num_statements": 1, "missing_lines": 0}
    files = {
        f"f{index}.py": {
            "executed_lines": [1],
            "missing_lines": [],
            "summary": {**one, "num_branches": 0, "covered_branches": 0},
        }
        for index in range(count)
    }
    totals = {
        "covered_lines": count,
        "num_statements": count,
        "missing_lines": 0,
        "num_branches": 0,
        "covered_branches": 0,
    }
    value = {"meta": {"branch_coverage": True}, "files": files, "totals": totals}
    return json.dumps(value), sorted(files)


PROBE = """
import json, sys
from pathlib import Path
from ici.analysis.coverage_support import parse_coverage_json
root = Path(sys.argv[1]).resolve()
names = json.loads((root.parent / "expected.json").read_text("utf-8"))
expected = {str(root / name) for name in names}
result = parse_coverage_json(root / "coverage.json", root, expected)
if result is None:
    print(json.dumps({"accepted": False}))
else:
    summaries = [info["summary"] for info in result["files"].values()]
    print(json.dumps({
        "accepted": True,
        "file_count": len(result["files"]),
        "files": sorted(result["files"]) if len(result["files"]) <= 16 else None,
        "covered_lines": sum(item["covered_lines"] for item in summaries),
        "num_statements": sum(item["num_statements"] for item in summaries),
        "covered_branches": sum(item["covered_branches"] for item in summaries),
        "num_branches": sum(item["num_branches"] for item in summaries),
        "line_cov": result["line_cov"],
        "branch_cov": result["branch_cov"],
    }))
"""


def capture(oracle: Path, python: str, case: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="ici-coverage-json-") as temporary:
        root = Path(temporary).resolve() / "project"
        root.mkdir()
        if "generate" in case:
            payload, expected = generated(case["generate"])
        else:
            payload, expected = case["text"], case["expected"]
        (root / "coverage.json").write_text(payload.replace("<ROOT>", str(root)), "utf-8")
        (root.parent / "expected.json").write_text(json.dumps(expected), "utf-8")
        for name in expected:
            (root / name).write_text("x = 1\n", "utf-8")
        output = subprocess.run(
            [python, "-I", "-B", "-c", PROBE, str(root)],
            env={"PYTHONPATH": str(oracle / "src"), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
            timeout=600,
        )
        return json.loads(output.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    head = subprocess.check_output(["git", "-C", str(args.oracle), "rev-parse", "HEAD"], text=True)
    assert head.strip() == SOURCE
    document_out = {"source": SOURCE, "python": sys.version.split()[0], "cases": {}}
    for name, case in CASES.items():
        recorded = {key: value for key, value in case.items()}
        recorded["frozen"] = capture(args.oracle, sys.executable, case)
        document_out["cases"][name] = recorded
        print(name, recorded["frozen"]["accepted"], flush=True)
    args.out.write_text(json.dumps(document_out, indent=1, sort_keys=True) + "\n", "utf-8")


if __name__ == "__main__":
    main()
