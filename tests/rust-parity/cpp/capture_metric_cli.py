"""Development-only frozen `ici complexity`/`ici cognitive` C++ capture; never called by Rust."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
SOURCE = (ROOT / "tests/rust-parity/cpp/metric-cli-fixture.cpp").read_text(encoding="utf-8")
CONFIGS = {
    "defaults": "",
    "low": "[engines.complexity]\nwarn_cc=3\nfail_cc=8\n[engines.cognitive]\nwarn=3\nfail=8\n",
    "equal": (
        "[engines.complexity]\nwarn_cc=11\nfail_cc=11\nwarn_nesting=9\n"
        "[engines.cognitive]\nwarn=4\nfail=4\nwarn_nesting=9\n"
    ),
    "nesting": "[engines.complexity]\nwarn_nesting=2\n[engines.cognitive]\nwarn_nesting=2\n",
}
FIELDS = ("target_name", "status", "message", "start_line", "end_line", "file_path")


def run(config: str, command: str) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        (project / "src").mkdir()
        (project / "src/main.cpp").write_text(SOURCE, encoding="utf-8")
        (project / "ici.toml").write_text(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n" + config, encoding="utf-8"
        )
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": directory,
            "PYTHONPATH": str(ORACLE / "src"),
        }
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env=environment,
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


cases = {
    f"{command}:{name}": run(config, command)
    for name, config in CONFIGS.items()
    for command in ("complexity", "cognitive")
}
output = ROOT / "tests/rust-parity/cpp/metric-cli-oracle.json"
output.write_text(
    json.dumps({"oracle": EXPECTED, "configs": CONFIGS, "cases": cases}, indent=1, sort_keys=True)
    + "\n",
    encoding="utf-8",
)
print(
    f"{len(cases)} cases -> {output}",
    os.linesep.join(f"{k}: {v['status']}" for k, v in cases.items()),
)
