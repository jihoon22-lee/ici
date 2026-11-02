"""Development-only frozen compiler-traced include-graph captures; never called by Rust.

Runs the frozen CLI `ici verify --profile deep --no-cache --report` (only the cycle engine
enabled, `project.compile_database` set) in a fresh copy of each parity-fixtures/cpp/
include-context tree with PATH=/usr/bin:/bin, LANG/LC_ALL=C.UTF-8 and PYTHONHASHSEED=0, and
keeps the exit code, suite status and the cycle engine's status, evidence, summary, frozen
`_analyze_cpp_includes` extra counters, targets, findings and tool evidence (name, return code,
error). Every case must take the compiler-traced graph (`cpp_include_resolution ==
"compiler_trace"`): a tree that silently fell back to the heuristic graph is refused.
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
FIXTURES = ROOT / "parity-fixtures/cpp/include-context"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-include-context"
GOLDEN = ROOT / "tests/rust-parity/cpp/include-context-oracle.json"
EXTRA_KEYS = (
    "cpp_include_resolution",
    "resolved_cpp_includes",
    "ambiguous_cpp_includes",
    "unresolved_cpp_includes",
    "cpp_include_scope_counts",
    "cpp_configurations_checked",
    "cpp_include_diagnostics_truncated",
    "cpp_cycles",
)
TARGET_KEYS = ("target_name", "status", "message", "file_path", "start_line")
EVIDENCE_KEYS = ("name", "returncode", "error")

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
    engine = next(item for item in report["results"] if item["engine_name"] == "cycle")
    assert engine["extra"]["cpp_include_resolution"] == "compiler_trace", name
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "extra": {key: engine["extra"][key] for key in EXTRA_KEYS},
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
print(f"{len(cases)} include-context cases")
