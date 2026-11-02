"""Development-only frozen legacy CMake artifact manifest captures; never called by Rust.

Frozen `core/cmake._capture_artifact_manifest` attaches an `ici.artifacts/v1|v2` manifest of
the linked shadow outputs to the engine result of every successful CMake build session that
has an analysis context. This script runs the frozen CLI over parity-fixtures/cpp/test-cmake
in a git checkout and keeps every engine's `artifact_manifests` (digest values replaced by
their shape, since they hash the frozen run identity and compiler output).
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
WORK = Path(tempfile.gettempdir()) / "ici-cpp-cmake-manifests"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
BUILD = "[engines.build]\nenabled=true\n"
# name: (fixture, command, extra arguments, configuration appended to BASE)
CASES = {
    "verify-suite": ("test-cmake/suite", "verify", [], ""),
    "verify-broken-build": ("test-cmake/broken-build", "verify", [], ""),
    "test-suite": ("test-cmake/suite", "test", [], ""),
    # The deep profile adds the thread-sanitize build session.
    "verify-deep-plain": ("test-cmake/plain", "verify", ["--profile", "deep"], ""),
    # The enabled build engine publishes its release session manifest under verify (deep).
    "verify-deep-build": ("test-cmake/plain", "verify", ["--profile", "deep"], BUILD),
    # A failed release build publishes none.
    "verify-deep-build-broken": ("test-cmake/broken-build", "verify", ["--profile", "deep"], BUILD),
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
CMAKE = subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0]
GXX = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]


def shape(value):
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(item) for item in value]
    if isinstance(value, str) and DIGEST.fullmatch(value):
        return "<sha256>"
    return value


def run(fixture: str, command: str, args: list[str], config: str) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        shutil.copytree(ROOT / "parity-fixtures/cpp" / fixture, project)
        (project / "ici.toml").write_text(BASE + config, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "-C", str(project), "add", "-A"], check=True)
        subprocess.run([*git, "-C", str(project), "commit", "-qm", "fixture"], check=True)
        result = subprocess.run(
            [PYTHON, "-m", "ici", command, *args, "--report"],
            cwd=project,
            env={"PATH": "/usr/bin:/bin", "HOME": str(WORK), "PYTHONPATH": str(ORACLE / "src")},
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        report = json.loads((project / f"{command}_report.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    engines = report["results"] if command == "verify" else [report]
    return {
        "exit_code": result.returncode,
        "engines": {
            engine["engine_name"]: shape(engine.get("artifact_manifests"))
            for engine in engines
            if engine["engine_name"] in {"build", "test", "sanitize", "thread_sanitize"}
        },
    }


cases = {
    name: {
        "fixture": fixture,
        "command": command,
        "args": args,
        "config": config,
        "frozen": run(fixture, command, args, config),
    }
    for name, (fixture, command, args, config) in CASES.items()
}
(ROOT / "tests/rust-parity/cpp/cmake-manifests-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "cmake": CMAKE, "gxx": GXX, "base": BASE, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} CMake manifest cases with {CMAKE}")
