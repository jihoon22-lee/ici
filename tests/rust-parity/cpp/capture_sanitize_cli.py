"""Development-only frozen C++ sanitizer CLI captures; never called by the Rust product."""

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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-sanitize-capture"
MAKE = "[build.make]\nenabled=true\nbuild_argv=['/usr/bin/make','all']\n"
REQUIRED = "[engines.sanitize]\nrequired=true\n[engines.thread_sanitize]\nrequired=true\n"
# fixture, ici.toml tail: only the direct-* cases compile and run instrumented tests.
CONFIGS = {
    "testless-direct": ("sanitize-testless", ""),
    "testless-make": ("sanitize-testless", MAKE),
    "testless-make-required": ("sanitize-testless", MAKE + REQUIRED),
    "tested-make-without-sanitizer-argv": ("sanitize-tested", MAKE),
    # Direct g++ runs: one clean test, and one clean plus one heap use-after-free test.
    "direct-clean": ("sanitize-direct-clean", ""),
    "direct-defect": ("sanitize-direct", ""),
}


def run(fixture: Path, command: str, config: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(fixture, project)
        (project / "ici.toml").write_text(
            "[project]\ntype='cpp'\nsource_dirs=['src']\n" + config, encoding="utf-8"
        )
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        report = json.loads(
            (project / f"{command.replace('-', '_')}_report.json").read_text(encoding="utf-8")
        )
        built = (project / "build").exists()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    return {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "built": built,
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }


FIXTURES = ROOT / "parity-fixtures/cpp"
cases = {
    f"{command}:{name}": run(FIXTURES / fixture, command, config)
    for name, (fixture, config) in CONFIGS.items()
    for command in ("sanitize", "thread-sanitize")
}
(ROOT / "tests/rust-parity/cpp/sanitize-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "configs": {k: v[1] for k, v in CONFIGS.items()}, "cases": cases},
        indent=1,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} sanitizer cases")
