"""Development-only frozen C++ legacy CLI captures; never called by the Rust product."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(fixture: Path, command: str, config: str = "") -> dict:
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory) / "project"
        shutil.copytree(fixture, project)
        (project / "ici.toml").write_text(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n" + config, encoding="utf-8"
        )
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": directory,
                "PYTHONPATH": str(ORACLE / "src"),
                # Frozen Tarjan iterates Python sets; pin hashing so captures are reproducible.
                "PYTHONHASHSEED": "0",
            },
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


PARITY = ROOT / "tests/rust-parity/cpp"
scope = {
    command: run(PARITY / "legacy-scope-fixture", command)
    for command in ("complexity", "cognitive", "exception")
}
(PARITY / "legacy-scope-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "cases": scope}, indent=1, sort_keys=True) + "\n",
    encoding="utf-8",
)
CYCLE_CONFIGS = {"default": "", "limit-one": "[engines.cycle]\nmax_reported=1\n"}
cycle = {
    name: run(PARITY / "cycle-fixture", "cycle", config) for name, config in CYCLE_CONFIGS.items()
}
(PARITY / "cycle-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "configs": CYCLE_CONFIGS, "cases": cycle}, indent=1, sort_keys=True
    )
    + "\n",
    encoding="utf-8",
)
exception = {"exception": run(PARITY / "exception-fixture", "exception")}
(PARITY / "exception-oracle.json").write_text(
    json.dumps({"oracle": EXPECTED, "cases": exception}, indent=1, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"{len(scope)} scope cases, {len(cycle)} cycle cases, {len(exception)} exception cases")
