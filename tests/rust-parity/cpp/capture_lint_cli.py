"""Development-only frozen legacy C++ lint captures; never called by the Rust product.

Heuristic fallback only (no compilation database) with PATH=/usr/bin:/bin, where clang-tidy and
clazy-standalone are absent; g++ wording depends on its version, which is recorded.
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
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-lint-capture"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
# name: (fixture, command, ici.toml tail)
CASES = {
    "lint": ("lint-fixture", "lint", ""),
    "lint-warnings-only": ("lint-warnings", "lint", ""),
    "lint-tidy-off": ("lint-fixture", "lint", "[engines.lint]\nclang_tidy='off'\n"),
    "lint-clazy-required": ("lint-warnings", "lint", "[engines.lint]\nclazy='required'\n"),
    "verify": ("lint-fixture", "verify", ""),
}


def run(fixture: str, command: str, config: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engine = report
    if command == "verify":
        engine = next(item for item in report["results"] if item.get("engine_name") == "lint")
    captured = {
        "status": engine["status"],
        "summary": engine["summary"],
        "evidence": engine["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in engine["targets"]],
    }
    if command != "verify":
        captured["exit_code"] = result.returncode
    return captured


cases = {
    name: {
        "fixture": fixture,
        "command": command,
        "config": config,
        "frozen": run(fixture, command, config),
    }
    for name, (fixture, command, config) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/lint-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "gxx": GXX, "base": BASE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} lint cases with {GXX}")
