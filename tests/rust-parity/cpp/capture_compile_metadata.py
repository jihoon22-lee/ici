"""Development-only frozen compile-database metadata warning captures; never called by Rust.

Runs the frozen CLI `ici verify --profile deep --no-cache --report` (compile_db, lint and cycle
enabled, `project.compile_database = "db/compile_commands.json"`) over a fresh copy of
parity-fixtures/cpp/compile-metadata with PATH=/usr/bin:/bin, LANG/LC_ALL=C.UTF-8 and
PYTHONHASHSEED=0. Each database row carries one frozen `_compile_db_metadata` warning: an
unknown `-x` language, an invalid `-D` name, `-o` without a value, an `output` field outside the
project, an `output` field that is not a string, one naming a directory, one disagreeing with
`-o`, one that agrees with `-o` only when read from the database directory, and a clean row.
Keeps the exit code, suite status and, for each of the three engines, status, evidence,
summary, score, every target, every finding, the full `extra` and the full tool evidence, with
the project path stored as {root}.
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
FIXTURE = ROOT / "parity-fixtures/cpp/compile-metadata"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-compile-metadata"
GOLDEN = ROOT / "tests/rust-parity/cpp/compile-metadata-oracle.json"
ENGINES = ("compile_db", "lint", "cycle")
TARGET_KEYS = ("target_name", "status", "message", "file_path", "start_line", "end_line")
FINDING_KEYS = ("rule_id", "severity", "confidence", "category", "message")

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def engine_view(engine: dict) -> dict:
    return {
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "score": engine.get("score"),
        "targets": [{key: target.get(key) for key in TARGET_KEYS} for target in engine["targets"]],
        "findings": [
            {
                **{key: finding[key] for key in FINDING_KEYS},
                "path": finding["primary_location"]["path"],
                "start_line": finding["primary_location"]["start_line"],
            }
            for finding in engine["findings"]
        ],
        "extra": engine["extra"],
        "tool_evidence": engine["tool_evidence"],
    }


def run() -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
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
        report = json.loads(text.replace(str(project), "{root}"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engines = {item["engine_name"]: item for item in report["results"]}
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "engines": {name: engine_view(engines[name]) for name in ENGINES},
    }


GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "gxx": GXX, "case": run()},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print("compile-metadata captured")
