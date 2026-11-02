"""Development-only frozen legacy C++ metric captures over unusable sources; never Rust.

Runs the frozen CLI `ici cognitive --report` and `ici complexity --report` (type='cpp',
source_dirs=['src'], no compilation database) in a fresh copy of each
parity-fixtures/cpp/metric-sources tree with PATH=/usr/bin:/bin and LANG/LC_ALL=C.UTF-8:
`unreadable` has a source that is not UTF-8 beside a measurable one; `unterminated` a function
whose body never closes. The `empty` tree (a type='cpp' project with no source) runs
`ici exception --report` and `ici type --report` instead, the not-applicable C++ scope;
`unreadable` also runs `ici dup --report` (frozen source intake stops at the bad source). Keeps
the exit code, status, evidence, summary and targets.
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
FIXTURES = ROOT / "parity-fixtures/cpp/metric-sources"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-metric-sources"
GOLDEN = ROOT / "tests/rust-parity/cpp/metric-sources-oracle.json"
CONFIG = '[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
FIELDS = ("target_name", "status", "message", "file_path", "start_line", "end_line")

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(name: str, command: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / name, project)
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        (WORK / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
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
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "evidence": report["evidence"],
        "summary": report["summary"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }


COMMANDS = {"empty": ("exception", "type"), "unreadable": ("cognitive", "complexity", "dup")}
cases = {
    f"{command}:{path.name}": run(path.name, command)
    for path in sorted(FIXTURES.iterdir())
    if path.is_dir()
    for command in COMMANDS.get(path.name, ("cognitive", "complexity"))
}
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "config": CONFIG, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} metric source cases")
