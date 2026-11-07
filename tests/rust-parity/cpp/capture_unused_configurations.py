"""Development-only frozen C++ unused-function configuration-consensus captures; never Rust.

Runs the frozen CLI `ici verify --profile deep --no-cache --report` (only the dead engine,
`project.compile_database` with two configurations of src/a.cpp) in a fresh copy of each
parity-fixtures/cpp/unused-configurations tree with PATH=/usr/bin:/bin, LANG/LC_ALL=C.UTF-8 and
PYTHONHASHSEED=0. `varying`: `-DUSE_HELPER` uses the static helper in one configuration only,
so frozen `_merge_observations` reports a disagreement and discards every source's result;
`agreeing`: both configurations report the same unused helper and another source is clean.
Keeps the exit code, suite status and the dead engine's status, evidence, summary, targets,
findings and tool evidence (name, return code).
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
FIXTURES = ROOT / "parity-fixtures/cpp/unused-configurations"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-unused-configurations"
GOLDEN = ROOT / "tests/rust-parity/cpp/unused-configurations-oracle.json"
TARGET_KEYS = ("target_name", "status", "message", "file_path", "start_line")

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def run(name: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / name, project)
        (WORK / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", "deep", "--no-cache", "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(WORK / "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<root>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engine = next(item for item in report["results"] if item["engine_name"] == "dead")
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "targets": [{key: target[key] for key in TARGET_KEYS} for target in engine["targets"]],
        "findings": sorted(
            (
                {
                    "rule_id": finding["rule_id"],
                    "severity": finding["severity"],
                    "message": finding["message"],
                    "path": finding["primary_location"]["path"],
                }
                for finding in engine["findings"]
            ),
            key=lambda item: (item["path"], item["message"]),
        ),
        "tool_evidence": [
            {key: item[key] for key in ("name", "returncode")} for item in engine["tool_evidence"]
        ],
    }


cases = {path.name: run(path.name) for path in sorted(FIXTURES.iterdir()) if path.is_dir()}
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "gxx": GXX, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} unused-configuration cases")
