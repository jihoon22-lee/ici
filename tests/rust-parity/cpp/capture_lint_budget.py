"""Development-only frozen legacy C++ lint global time budget captures; never called by Rust.

Frozen `_cpp_lint` shares one 600 s budget (`_GLOBAL_TIMEOUT_SECONDS`) across every selected
unit of `_run_exact` and `_run_fallback`: a unit gets `min(120 s, remaining)` and once the
budget is spent the next unit is a `C++SyntaxBudgetError` target and the rest are not run.
This script runs the frozen CLI with that constant replaced (0 s: spent before the first unit;
20 s: spent while `slow.cpp` sleeps behind a fake `g++`) over parity-fixtures/cpp/lint-branches,
and one with 2049 generated sources (`run_cpp_lint` source-count bound, nothing replayed).
Paths are stored as {root} (the project) and {bin} (the fake tool directory).
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
WORK = Path(tempfile.gettempdir()) / "ici-cpp-lint-budget"
FIELDS = ("target_name", "status", "message", "start_line", "start_column", "file_path")
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
FAKE = """#!/bin/sh
case "$*" in
  *--version*) exec /usr/bin/g++ "$@" ;;
  *sig.cpp*) kill -KILL $$ ;;
  *exit3.cpp*) exit 3 ;;
  *quiet1.cpp*) exit 1 ;;
  *err0.cpp*) /usr/bin/g++ "$@"; exit 0 ;;
  *warn1.cpp*) /usr/bin/g++ "$@"; exit 1 ;;
  *big.cpp*) head -c 1000001 /dev/zero | tr '\\0' x; exit 0 ;;
  *slow.cpp*) sleep 60; exit 0 ;;
esac
exec /usr/bin/g++ "$@"
"""
# The frozen CLI with `_cpp_lint._GLOBAL_TIMEOUT_SECONDS` replaced by the first argument.
LAUNCH = (
    "import sys\n"
    "import ici.analysis._cpp_lint as lint\n"
    "lint._GLOBAL_TIMEOUT_SECONDS = float(sys.argv.pop(1))\n"
    "from ici.__main__ import main\n"
    "sys.argv[0] = 'ici'\n"
    "main()\n"
)
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
SOURCE = '#include "shared.hpp"\n\nint {name}() {{ return shared_value(); }}\n'
BRANCHES = ["big", "clean", "err0", "exit3", "quiet1", "sig", "warn1"]


def row(source: str) -> dict:
    return {
        "directory": "{root}",
        "file": source,
        "arguments": ["g++", "-std=c++17", "-Iinclude", "-c", source],
    }


SLOW = {"src/slow.cpp": SOURCE.format(name="slow")}
DATABASE = [row(f"src/{name}.cpp") for name in [*BRANCHES, "slow"]]
# name: (command, budget seconds, extra files)
CASES = {
    "fallback-budget-spent": ("lint", 0.0, SLOW),
    "fallback-budget-mid": ("lint", 20.0, SLOW),
    "exact-budget-spent": (
        "verify",
        0.0,
        {**SLOW, "build/compile_commands.json": json.dumps(DATABASE)},
    ),
    "exact-budget-mid": (
        "verify",
        20.0,
        {**SLOW, "build/compile_commands.json": json.dumps(DATABASE)},
    ),
    # 2049 selected sources (src/gen/s<index>.cpp, generated from "generated_sources").
    "fallback-source-count": ("lint", 600.0, {}),
}
GENERATED = {"fallback-source-count": 2049 - len(BRANCHES)}


def run(command: str, budget: float, files: dict, generated: int) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        tools = WORK / "bin"
        tools.mkdir()
        (tools / "g++").write_text(FAKE, encoding="utf-8")
        (tools / "g++").chmod(0o755)
        shutil.copytree(FIXTURES / "lint-branches", project)
        (project / "ici.toml").write_text(BASE, encoding="utf-8")
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text.replace("{root}", str(project)), encoding="utf-8")
        if generated:
            (project / "src/gen").mkdir()
        for index in range(generated):
            (project / f"src/gen/s{index}.cpp").write_text(f"int s{index}();\n", encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-c", LAUNCH, str(budget), command, "--report"],
            cwd=project,
            env={
                "PATH": f"{tools}:/usr/bin:/bin",
                "HOME": str(WORK),
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=1800,
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
        "findings": engine["findings"],
    }
    if command != "verify":
        captured["exit_code"] = result.returncode
    text = json.dumps(captured).replace(str(project), "{root}").replace(str(tools), "{bin}")
    return json.loads(text)


cases = {
    name: {
        "command": command,
        "budget_seconds": budget,
        "files": files,
        "generated_sources": GENERATED.get(name, 0),
        "frozen": run(command, budget, files, GENERATED.get(name, 0)),
    }
    for name, (command, budget, files) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/lint-budget-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "gxx": GXX, "base": BASE, "fake": FAKE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} lint budget cases with {GXX}")
