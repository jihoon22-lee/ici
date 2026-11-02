"""Development-only frozen legacy C++ lint fallback replay-rejection captures; never Rust.

A `g++` wrapper inside the project (`bin/g++`, delegating to /usr/bin/g++) is first on PATH, so
the frozen c++17 heuristic fallback (`_cpp_lint._run_fallback`) resolves a project-contained
compiler and `build_replay_command` rejects every source (`project-compiler-rejected`): each is
a `C++SyntaxReplayError` and an engine error, nothing is compiled. Captured for `ici lint` and
for `ici verify` with only the lint engine (clang-tidy/clazy off) over
parity-fixtures/cpp/lint-fallback-replay: exit code, status, evidence, summary and targets.
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
FIXTURE = ROOT / "parity-fixtures/cpp/lint-fallback-replay"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-lint-fallback-replay"
GOLDEN = ROOT / "tests/rust-parity/cpp/lint-fallback-replay-oracle.json"
WRAPPER = '#!/bin/sh\nexec /usr/bin/g++ "$@"\n'
DISABLED = [
    "line",
    "compile_db",
    "test",
    "type",
    "python_compat",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cognitive",
    "security",
    "resource",
    "build",
    "binary_compat",
    "integration",
    "cycle",
]
CONFIG = (
    '[project]\ntype = "cpp"\nsource_dirs = ["src"]\n'
    + "".join(f"[engines.{name}]\nenabled = false\n" for name in DISABLED)
    + '[engines.lint]\nenabled = true\nclang_tidy = "off"\nclazy = "off"\n'
)
FIELDS = ("target_name", "status", "message", "file_path", "start_line")

assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)


def run(command: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURE, project)
        (project / "bin").mkdir()
        (project / "bin/g++").write_text(WRAPPER, encoding="utf-8")
        (project / "bin/g++").chmod(0o755)
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        (WORK / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": f"{project / 'bin'}:/usr/bin:/bin",
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
    engine = report
    if command == "verify":
        engine = next(item for item in report["results"] if item["engine_name"] == "lint")
    return {
        "exit_code": result.returncode,
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "targets": [{key: target[key] for key in FIELDS} for target in engine["targets"]],
    }


cases = {command: run(command) for command in ("lint", "verify")}
GOLDEN.write_text(
    json.dumps(
        {"oracle": EXPECTED, "config": CONFIG, "wrapper": WRAPPER, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} lint fallback replay cases")
