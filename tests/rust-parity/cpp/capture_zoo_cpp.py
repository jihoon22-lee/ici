"""Development-only frozen `ici verify` captures of quality-zoo C++ scenarios; never called by Rust.

Runs the frozen CLI with each scenario's own command plus `--report` in a fresh copy of
quality-zoo/scenarios/cpp/<name> (no git checkout, like the zoo runner) with PATH=/usr/bin:/bin
(plus clang-tidy/clazy wrappers for the clang scenarios) and LANG/LC_ALL=C.UTF-8, and keeps the
suite status, the exit code, every engine's status, evidence, summary, the `extra` fields the zoo
compares, targets and tool evidence, and every engine finding's rule, message, severity,
confidence, category, tool and primary location. The temporary project path is replaced by
`<root>`. The zoo expectations themselves are not read or changed.
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
ZOO = ROOT / "quality-zoo/scenarios/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-zoo-capture"
GOLDEN = ROOT / "tests/rust-parity/cpp/zoo-cpp-oracle.json"
SCENARIOS = tuple(sorted(path.name for path in ZOO.iterdir() if path.is_dir()))
# The engine `extra` fields the zoo expectations compare (plus compile_db's database_path).
EXTRA_KEYS = (
    "sanitize_issues",
    "thread_sanitize_issues",
    "configurations",
    "coverage_percent",
    "covered_units",
    "database_path",
    "issues_count",
    "production_units",
    "clazy_mode",
    "clazy_profile",
    "cpp_diagnostic_categories",
    "cpp_diagnostic_category_policy",
    "cpp_diagnostic_families",
    "branch_coverage",
    "coverage_source",
    "function_coverage",
    "max_complexity",
    "total_functions",
    "cpp_unused_functions_count",
    "cpp_unused_mode",
    "clone_groups_count",
)
# Scenarios that need the clang tools: the captured LLVM 21.1.8 clang-tidy/clazy, run through
# wrappers that set their library path (the zoo runner puts the same tools on PATH).
CLANG_TOOLS = {"qt-missing-parent-constructor"}
TARGET_KEYS = ("target_name", "status", "message", "file_path", "start_line")
EVIDENCE_KEYS = ("name", "path", "argv", "returncode", "timed_out", "truncated", "error")
FINDING_KEYS = (
    "rule_id",
    "message",
    "severity",
    "confidence",
    "category",
    "tool_name",
    "tool_rule_id",
)
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def wrappers(directory: Path) -> Path:
    directory.mkdir()
    library = os.environ["ICI_TEST_CLANG_LIBRARY_PATH"]
    for name, variable in (
        ("clang-tidy", "ICI_TEST_CLANG_TIDY"),
        ("clazy-standalone", "ICI_TEST_CLAZY"),
    ):
        tool = directory / name
        tool.write_text(
            f'#!/bin/sh\nLD_LIBRARY_PATH={library} exec {os.environ[variable]} "$@"\n',
            encoding="utf-8",
        )
        tool.chmod(0o755)
    return directory


def run(name: str) -> dict:
    scenario = json.loads((ZOO / name / "scenario.json").read_text(encoding="utf-8"))
    expectation = next(iter(sorted((ZOO / name / "expectations").glob("candidate-v0.13.0.json"))))
    command = json.loads(expectation.read_text(encoding="utf-8"))["command"]
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(ZOO / name, project, ignore=shutil.ignore_patterns("expectations"))
        (WORK / "home").mkdir()
        path = "/usr/bin:/bin"
        if name in CLANG_TOOLS:
            path = f"{wrappers(WORK / 'bin')}:{path}"
        result = subprocess.run(
            [PYTHON, "-m", "ici", *command, "--report"],
            cwd=project,
            env={
                "PATH": path,
                "HOME": str(WORK / "home"),
                "QT_QPA_PLATFORM": "offscreen",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONHASHSEED": "0",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        text = (project / "verify_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<root>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "scenario": scenario["scenario_id"],
        "command": command,
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "engines": {
            engine["engine_name"]: {
                "status": engine["status"],
                "evidence": engine["evidence"],
                "summary": engine["summary"],
                "extra": {
                    key: engine["extra"][key] for key in EXTRA_KEYS if key in engine["extra"]
                },
                "targets": [
                    {key: target.get(key) for key in TARGET_KEYS} for target in engine["targets"]
                ],
                "tool_evidence": [
                    {key: record.get(key) for key in EVIDENCE_KEYS}
                    for record in engine["tool_evidence"]
                ],
                "findings": [
                    {key: finding[key] for key in FINDING_KEYS}
                    | {
                        "path": finding["primary_location"]["path"],
                        "start_line": finding["primary_location"]["start_line"],
                    }
                    for finding in engine["findings"]
                ],
            }
            for engine in report["results"]
        },
    }


cases = {name: run(name) for name in SCENARIOS}
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
print(f"{len(cases)} quality-zoo C++ scenarios with {GXX}")
