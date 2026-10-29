"""Development-only frozen C++ source-scope CLI capture; never called by the Rust product."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURE = ROOT / "tests/rust-parity/cpp/legacy-scope-fixture"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(command: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n", encoding="utf-8"
        )
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": directory, "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }


cases = {command: run(command) for command in ("complexity", "cognitive", "exception")}
output = ROOT / "tests/rust-parity/cpp/legacy-scope-oracle.json"
output.write_text(
    json.dumps({"oracle": EXPECTED, "cases": cases}, indent=1, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} cases -> {output}")
