"""Development-only frozen `ici analyze` C++ exception-scope captures; never called by Rust.

Runs the frozen CLI `ici analyze --json` (no config: one automatic C++ component) over
parity-fixtures/cpp/exception-next-scope in a fresh `project` directory with PATH=/usr/bin:/bin,
and records the `cpp.exception` check, its `exception.*` findings and the run limitations that
name an excluded source (frozen `languages/hygiene.py.measure_hygiene` over
`read_analysis_sources`: generated and vendor sources are excluded and reported).
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURE = ROOT / "parity-fixtures/cpp/exception-next-scope"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-exception-next-scope"
GOLDEN = ROOT / "tests/rust-parity/cpp/exception-next-scope-oracle.json"

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run() -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        result = subprocess.run(
            [PYTHON, "-I", "-m", "ici", "analyze", "--json"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        document = json.loads(result.stdout)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "checks": [
            {key: check[key] for key in ("task_id", "check_id", "state", "evidence_complete")}
            for check in document["checks"]
            if check["check_id"] == "cpp.exception"
        ],
        "findings": [
            {
                key: finding[key]
                for key in ("rule_id", "message", "severity", "confidence", "category")
            }
            | {
                "path": finding["primary_location"]["path"],
                "line": finding["primary_location"]["start_line"],
            }
            for finding in document["findings"]
            if finding["rule_id"].startswith("exception.")
        ],
        "excluded": sorted(
            item for item in document["limitations"] if item.startswith("excluded ")
        ),
    }


case = run()
GOLDEN.write_text(
    json.dumps({"oracle": EXPECTED, "case": case}, indent=1, sort_keys=True, ensure_ascii=False)
    + "\n",
    encoding="utf-8",
)
print(f"{len(case['findings'])} exception findings, {len(case['excluded'])} exclusions")
