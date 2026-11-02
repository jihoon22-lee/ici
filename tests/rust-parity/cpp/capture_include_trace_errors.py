"""Development-only frozen include-trace process-error captures; never called by Rust.

Runs the frozen CLI `ici verify --profile deep --no-cache --report` (only the cycle engine
enabled, `project.compile_database` set) over parity-fixtures/cpp/include-trace-errors with a
fake `g++` driver first on PATH (then /usr/bin:/bin), LANG/LC_ALL=C.UTF-8 and PYTHONHASHSEED=0.
The driver gives each translation unit one frozen `_trace_process_error` /
`_consume_successful_trace` branch of `analysis/_cpp_include_graph.py`: unexpected stdout
(stdout.cpp), a signal (sig.cpp), stderr over the 1,000,000-character `run_process` capture
(big.cpp), a trace that jumps over a parent depth (unparse.cpp), a trace followed by a
non-trace diagnostic (diag.cpp); ok.cpp runs the real g++. The timed-out branch needs the
300 s `run_process` default to expire and is not captured. Keeps the exit code, suite status
and the cycle engine's status, evidence, summary, frozen extra (every key), targets, findings
and tool evidence (name, return code, error), with the project and driver paths replaced.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURE = ROOT / "parity-fixtures/cpp/include-trace-errors"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-include-trace-errors"
GOLDEN = ROOT / "tests/rust-parity/cpp/include-trace-errors-oracle.json"
TARGET_KEYS = ("target_name", "status", "message", "file_path", "start_line")
EVIDENCE_KEYS = ("name", "returncode", "error")
FAKE = """#!/bin/sh
case "$*" in
  *--version*) exec /usr/bin/g++ "$@" ;;
  *stdout.cpp*) echo unexpected; exit 0 ;;
  *sig.cpp*) kill -KILL $$ ;;
  *big.cpp*) head -c 1000001 /dev/zero | tr '\\0' x >&2; exit 0 ;;
  *unparse.cpp*) printf '.. /usr/include/stdio.h\\n' >&2; exit 0 ;;
  *diag.cpp*) /usr/bin/g++ "$@"; printf 'src/diag.cpp:1:1: warning: injected\\n' >&2; exit 0 ;;
esac
exec /usr/bin/g++ "$@"
"""

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def run() -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        (WORK / "home").mkdir()
        bin_dir = WORK / "bin"
        bin_dir.mkdir()
        (bin_dir / "g++").write_text(FAKE, encoding="utf-8")
        os.chmod(bin_dir / "g++", 0o755)
        result = subprocess.run(
            [PYTHON, "-m", "ici", "verify", "--profile", "deep", "--no-cache", "--report"],
            cwd=project,
            env={
                "PATH": f"{bin_dir}:/usr/bin:/bin",
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
        report = json.loads(text.replace(str(project), "<root>").replace(str(bin_dir), "<bin>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engine = next(item for item in report["results"] if item["engine_name"] == "cycle")
    assert engine["extra"]["cpp_include_resolution"] == "compiler_trace"
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "extra": engine["extra"],
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
            {key: item[key] for key in EVIDENCE_KEYS} for item in engine["tool_evidence"]
        ],
    }


GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "gxx": GXX, "fake": FAKE, "case": run()},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print("include-trace-errors captured")
