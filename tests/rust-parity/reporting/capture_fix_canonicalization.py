"""Development-only capture: frozen fix canonicalization (core/findings.py `_canonical_fix`).

Runs frozen `canonicalize_finding` (no project root, as for a historical v3 document) over a
native finding whose one fix carries each replacement below, and records the canonical
replacement path or that frozen refused the finding. Never called by the Rust product.

Run from a neutral directory: PYTHONPATH=<oracle>/src <venv python> -s
capture_fix_canonicalization.py <out.json> (not -I, which would ignore PYTHONPATH).
"""

import json
import subprocess
import sys
from pathlib import Path

from ici.core import models
from ici.core.findings import canonicalize_finding

EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
CASES = [
    ["plain", "src/a.py", 1, 1, 1, 4],
    ["collapsed", "./src/../src/a.py", 2, 1, 3, 1],
    ["backslash", "src\\a.py", 1, 1, 1, 1],
    ["trailing-slash", "src/a.py/", 1, 1, 1, 2],
    ["parent", "../outside.py", 1, 1, 1, 2],
    ["absolute", "/abs/outside.py", 1, 1, 1, 2],
    ["drive-absolute", "C:/outside.py", 1, 1, 1, 2],
    ["drive-relative", "C:outside.py", 1, 1, 1, 2],
    ["reversed-lines", "src/a.py", 3, 1, 2, 1],
    ["reversed-columns", "src/a.py", 1, 5, 1, 2],
    ["zero-column", "src/a.py", 1, 0, 1, 2],
    ["zero-line", "src/a.py", 0, 1, 1, 2],
]


def main() -> None:
    oracle = Path(sys.modules["ici"].__file__).resolve().parents[2]
    revision = subprocess.check_output(
        ["git", "-C", str(oracle), "rev-parse", "HEAD"], text=True
    ).strip()
    if revision != EXPECTED:
        raise SystemExit(f"oracle must be {EXPECTED}, found {revision}")
    results = []
    for name, path, start_line, start_column, end_line, end_column in CASES:
        replacement = models.FindingFixReplacement(
            path=path,
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
            replacement="x",
        )
        finding = models.Finding(
            rule_id="ici.lint.rule",
            category=models.FindingCategory.CORRECTNESS,
            severity=models.FindingSeverity.LOW,
            confidence=models.FindingConfidence.HIGH,
            fingerprint="",
            primary_location=models.SourceLocation(path="src/a.py", start_line=1),
            message="m",
            fixes=[models.FindingFix(description="d", replacements=(replacement,))],
        )
        try:
            canonical = canonicalize_finding(finding).fixes[0].replacements[0].path
        except ValueError as error:
            canonical, refused = None, str(error)
        else:
            refused = None
        results.append(
            {
                "name": name,
                "replacement": {
                    "path": path,
                    "start_line": start_line,
                    "start_column": start_column,
                    "end_line": end_line,
                    "end_column": end_column,
                    "replacement": "x",
                },
                "canonical_path": canonical,
                "refused": refused,
            }
        )
    output = {"oracle": EXPECTED, "cases": results}
    Path(sys.argv[1]).write_text(json.dumps(output, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
