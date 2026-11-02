"""Development-only frozen legacy GNU ELF linker dead-function captures; never Rust.

Runs the frozen CLI `ici dead --report` (cpp_unused='off', cpp_linker='required') in a fresh
copy of each parity-fixtures/cpp/linker-dead tree with PATH=/usr/bin:/bin and LANG/LC_ALL=C.UTF-8:
`discarded` relinks two CMake executables sharing one object, where GNU ld discards one hidden
function in both links (one `ici.dead.gnu-elf-discarded-function`); `clean` relinks one
executable that uses every hidden function; `broken` fails its CMake configure. Keeps the exit
code, status, evidence, required flag, summary, targets, findings, the whole engine extra and the
tool evidence names and return codes, with the project root written as `<root>`.
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
FIXTURES = ROOT / "parity-fixtures/cpp/linker-dead"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-linker-dead"
GOLDEN = ROOT / "tests/rust-parity/cpp/linker-dead-oracle.json"
TARGET_FIELDS = ("target_name", "status", "message", "file_path", "start_line", "metrics")
FINDING_FIELDS = (
    "rule_id",
    "severity",
    "confidence",
    "message",
    "explanation",
    "remediation",
    "tool_rule_id",
    "tool_name",
    "tool_version",
    "primary_location",
    "metrics",
)

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
        (WORK / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-m", "ici", "dead", "--report"],
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
        text = (project / "dead_report.json").read_text(encoding="utf-8")
        report = json.loads(text.replace(str(project), "<root>"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    findings = [{key: finding.get(key) for key in FINDING_FIELDS} for finding in report["findings"]]
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "evidence": report["evidence"],
        "required": report["required"],
        "summary": report["summary"],
        "targets": [
            {key: target.get(key) for key in TARGET_FIELDS} for target in report["targets"]
        ],
        "findings": sorted(findings, key=lambda item: json.dumps(item, sort_keys=True)),
        "extra": report["extra"],
        "tool_evidence": [
            {"name": item["name"], "returncode": item["returncode"]}
            for item in report["tool_evidence"]
        ],
    }


def banner(executable: str) -> str:
    output = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=True
    ).stdout
    return output.splitlines()[0]


cases = {path.name: run(path.name) for path in sorted(FIXTURES.iterdir()) if path.is_dir()}
GOLDEN.write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "gxx": banner("/usr/bin/g++"),
            "cmake": banner("/usr/bin/cmake"),
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} linker dead cases")
