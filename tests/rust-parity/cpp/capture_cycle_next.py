"""Development-only frozen `ici analyze` C++ include-cycle captures; never called by Rust.

Runs the frozen CLI `ici analyze --json` (no config: one automatic C++ component) over each tree
of parity-fixtures/cpp/cycle-next (`acyclic`: a resolved include chain; `unresolved`: quoted
includes with no project-file match beside a system include) in a fresh `project` directory
with PATH=/usr/bin:/bin, and records the `cpp.cycle` check, its `cycle.*` findings, the
`cycles_found` metric and the run limitations that name includes (frozen
`languages/cycles.py._measure_cpp`).
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
FIXTURES = ROOT / "parity-fixtures/cpp/cycle-next"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-cycle-next"
GOLDEN = ROOT / "tests/rust-parity/cpp/cycle-next-oracle.json"

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(name: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / name, project)
        # The project is its own VCS boundary (frozen and native both stop at `.hg`).
        (project / ".hg").mkdir()
        result = subprocess.run(
            [PYTHON, "-m", "ici", "analyze", "--json"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK),
                "GIT_CEILING_DIRECTORIES": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        document = json.loads(result.stdout)
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "checks": [
            {key: check[key] for key in ("task_id", "check_id", "state", "evidence_complete")}
            for check in document["checks"]
            if check["check_id"] == "cpp.cycle"
        ],
        "findings": [
            finding for finding in document["findings"] if finding["rule_id"].startswith("cycle.")
        ],
        "metrics": [metric for metric in document["metrics"] if metric["name"] == "cycles_found"],
        "limitations": [item for item in document["limitations"] if "include" in item.lower()],
    }


cases = {name: run(name) for name in ("acyclic", "unresolved")}
GOLDEN.write_text(
    json.dumps({"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True, ensure_ascii=False)
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} include-cycle analyze cases")
