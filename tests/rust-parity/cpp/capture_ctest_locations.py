"""Development-only frozen next `_parse_ctest` locations for a case without a matching source.

Reuses the real ctest 4.2.3 transcript of parity-fixtures/cpp/test-cmake/suite stored in
cpptest-parser-oracle.json and parses it with the frozen `_parse_ctest` over a copy of the
fixture whose tests/test_crash.cpp is absent, so `_test_source` finds no `test_crash*.cpp` or
`*test_crash*.cpp` and falls back to the bare stem (root ruling 2026-11-14 (b4)). Scratch
directory: argv[1]. Never called by the Rust product.
"""

import json
import shutil
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

from ici.adapters.providers.cpptest import _parse_ctest  # noqa: E402
from ici.execution.process import ProcessSpec  # noqa: E402

PARITY = ROOT / "tests/rust-parity/cpp"
REMOVED = "tests/test_crash.cpp"


def main() -> None:
    project = Path(sys.argv[1]).resolve()
    shutil.rmtree(project, ignore_errors=True)
    source = json.loads((PARITY / "cpptest-parser-oracle.json").read_text(encoding="utf-8"))
    transcript = source["cases"]["ctest-suite"]["transcript"]
    try:
        shutil.copytree(ROOT / "parity-fixtures/cpp/test-cmake/suite", project)
        (project / REMOVED).unlink()
        text = transcript.replace("<ROOT>", str(project))
        argv = ("ctest", "--test-dir", str(project / "build"), "--output-on-failure")
        spec = ProcessSpec(argv=argv, name="app.cpp.test", cwd=project)
        output = _parse_ctest(text, spec, project_root=project)
    finally:
        shutil.rmtree(project, ignore_errors=True)
    case = {
        "fixture": "test-cmake/suite",
        "removed": REMOVED,
        "transcript_case": "cpptest-parser-oracle.json#ctest-suite",
        "frozen": {
            "failed_to_parse": output.failed_to_parse,
            "findings": [
                {
                    "rule_id": item.rule_id,
                    "message": item.message,
                    "path": item.primary_location.path,
                    "start_line": item.primary_location.start_line,
                    "severity": item.severity,
                    "confidence": item.confidence,
                }
                for item in output.findings
            ],
            "limitations": list(output.limitations),
        },
    }
    (PARITY / "ctest-locations-oracle.json").write_text(
        json.dumps({"oracle": EXPECTED, "cases": {"ctest-unmatched-source": case}}, indent=1)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(case["frozen"]["findings"]))


if __name__ == "__main__":
    main()
