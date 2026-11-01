"""Development-only frozen legacy C++ lint execution-branch captures; never called by Rust.

A fake `g++` first on PATH (a shell script, version probe delegated to /usr/bin/g++) drives
the frozen `_cpp_lint` process/output branches per source name: `sig.cpp` is killed by
SIGKILL, `exit3.cpp` exits 3 silently, `quiet1.cpp` exits 1 silently, `err0.cpp` reports a real
GCC error and exits 0, `warn1.cpp` reports only a `#warning` and exits 1, `big.cpp` writes
1,000,001 bytes to stdout, `slow.cpp` sleeps past the 120 s per-unit timeout; other sources run
/usr/bin/g++. `ici lint` uses the c++17 heuristic fallback, `ici verify` the exact database
rows. `ruff` in the tool directory links ICI_TEST_RUFF for the hybrid cases. A configuration
tail that starts with `[project]` replaces the C++ base. Paths are stored as {root} (the project)
and {bin} (the fake tool directory).
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
FIXTURES = ROOT / "parity-fixtures/cpp"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-lint-branches"
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
  *slow.cpp*) sleep 125; exit 0 ;;
esac
exec /usr/bin/g++ "$@"
"""
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
SOURCE = '#include "shared.hpp"\n\nint {name}() {{ return shared_value(); }}\n'
BRANCHES = ["big", "clean", "err0", "exit3", "quiet1", "sig", "warn1"]


def row(source: str, *flags: str, extra: tuple[str, ...] = ()) -> dict:
    return {
        "directory": "{root}",
        "file": source,
        "arguments": ["g++", "-std=c++17", *flags, "-Iinclude", "-c", source, *extra],
    }


EXACT = [row(f"src/{name}.cpp") for name in BRANCHES if name != "clean"]
# Two configurations of one source, one with a warning-level unit diagnostic
# (missing-include-dir), and a row whose replay is rejected (an extra input operand).
EXACT += [
    row("src/clean.cpp"),
    row("src/clean.cpp", "-DVARIANT=2", "-Imissing-dir"),
    row("src/replay.cpp", extra=("other.o",)),
]
# name: (fixture, command, ici.toml tail, extra files)
CASES = {
    "fallback-branches": (
        "lint-branches",
        "lint",
        "",
        {"src/slow.cpp": SOURCE.format(name="slow")},
    ),
    "exact-branches": (
        "lint-branches",
        "verify",
        "",
        {
            "src/replay.cpp": SOURCE.format(name="replay"),
            "build/compile_commands.json": json.dumps(EXACT),
        },
    ),
    # Only a non-production row: every production source is missing and none is replayable.
    "exact-no-replayable": (
        "lint-branches",
        "verify",
        "",
        {
            "tools/gen.cpp": SOURCE.format(name="gen"),
            "build/compile_commands.json": json.dumps([row("tools/gen.cpp")]),
        },
    ),
    # 2049 configurations of one source exceed the 2048 selected-unit budget.
    "exact-budget": (
        "lint-branches",
        "verify",
        "",
        {
            "build/compile_commands.json": json.dumps(
                [row(f"src/{name}.cpp") for name in BRANCHES if name != "clean"]
                + [row("src/clean.cpp", f"-DCONFIGURATION={index}") for index in range(2043)]
            ),
        },
    ),
    # Unit diagnostics frozen records as warnings: a recorded directory that is gone, and a
    # stale Qt generated source (selected by name although it no longer exists).
    "exact-unit-diagnostics": (
        "lint-branches",
        "verify",
        "",
        {
            "build/compile_commands.json": json.dumps(
                [
                    {
                        "directory": "{root}/gone",
                        "file": "{root}/src/clean.cpp",
                        "arguments": ["g++", "-std=c++17", "-c", "{root}/src/clean.cpp"],
                    },
                    row("build/moc_gone.cpp"),
                ]
                + [row(f"src/{name}.cpp") for name in BRANCHES if name != "clean"]
            ),
        },
    ),
    # Frozen LintEngine on a hybrid project without Python or C++ sources: LintScope SKIP.
    "lint-hybrid-empty": ("lint-empty", "lint", "[project]\ntype='hybrid'\n", {}),
    # Mixed Python (Ruff F401) and C++ fallback (exit 3) summaries in one lint engine.
    "lint-hybrid-mixed": (
        "lint-branches",
        "lint",
        "[project]\ntype='hybrid'\nsource_dirs=['src']\n",
        {"src/app.py": "import os\n"},
    ),
}


def run(fixture: str, command: str, config: str, files: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        tools = WORK / "bin"
        tools.mkdir()
        (tools / "g++").write_text(FAKE, encoding="utf-8")
        (tools / "g++").chmod(0o755)
        os.symlink(os.environ["ICI_TEST_RUFF"], tools / "ruff")
        shutil.copytree(FIXTURES / fixture, project)
        base = "" if config.startswith("[project]") else BASE
        (project / "ici.toml").write_text(base + config, encoding="utf-8")
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text.replace("{root}", str(project)), encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, "--report"],
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
    else:
        database = next(
            item for item in report["results"] if item.get("engine_name") == "compile_db"
        )
        captured["compile_db"] = {
            "status": database["status"],
            "summary": database["summary"],
            "targets": [{key: target.get(key) for key in FIELDS} for target in database["targets"]],
        }
    text = json.dumps(captured).replace(str(project), "{root}").replace(str(tools), "{bin}")
    return json.loads(text)


cases = {
    name: {
        "fixture": fixture,
        "command": command,
        "config": config,
        "files": files,
        "frozen": run(fixture, command, config, files),
    }
    for name, (fixture, command, config, files) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/lint-branches-oracle.json").write_text(
    json.dumps(
        {
            "oracle": EXPECTED,
            "gxx": GXX,
            "ruff": subprocess.check_output(
                [os.environ["ICI_TEST_RUFF"], "--version"], text=True
            ).strip(),
            "base": BASE,
            "fake": FAKE,
            "cases": cases,
        },
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} lint branch cases with {GXX}")
