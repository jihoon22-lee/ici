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
DATABASE = (
    '[{"directory":"{root}","file":"src/clean.cpp",'
    '"arguments":["g++","-std=c++17","-Iinclude","-c","src/clean.cpp"]}]'
)
MAKE = "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
# name: (fixture, command, ici.toml tail, extra files with {root} = the project directory)
CASES = {
    "lint": ("lint-fixture", "lint", "", {}),
    "lint-warnings-only": ("lint-warnings", "lint", "", {}),
    "lint-tidy-off": ("lint-fixture", "lint", "[engines.lint]\nclang_tidy='off'\n", {}),
    "lint-clazy-required": ("lint-warnings", "lint", "[engines.lint]\nclazy='required'\n", {}),
    "verify": ("lint-fixture", "verify", "", {}),
    # Standalone `ici lint` has no analysis context: a compilation database or a declared Make
    # build changes nothing and nothing is built (the Makefile would fail on src/broken.cpp).
    "lint-with-database": (
        "lint-fixture",
        "lint",
        "",
        {"build/compile_commands.json": DATABASE},
    ),
    "lint-explicit-database": (
        "lint-fixture",
        "lint",
        "compile_database='build/compile_commands.json'\n",
        {"build/compile_commands.json": DATABASE},
    ),
    "lint-make": (
        "lint-fixture",
        "lint",
        MAKE,
        {"Makefile": "all:\n\tmkdir -p build/ici-make\n\tg++ src/*.cpp -o build/ici-make/app\n"},
    ),
}


def run(fixture: str, command: str, config: str, files: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        for name, text in files.items():
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_text(text.replace("{root}", str(project)), encoding="utf-8")
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
        built = (project / "build/ici-make").exists()
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
    if files:
        captured["built"] = built
    return captured


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
