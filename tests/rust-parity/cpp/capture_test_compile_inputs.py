"""Development-only frozen captures of the legacy C++ compile inputs; never called by Rust.

Frozen `TestEngine._run_cpp_tests` (engines/test.py:524-528) compiles each direct g++ test with
`project_cpp_include_flags()` (include directories, then the ordered `pkg-config --cflags` flags
of `project.cpp_pkg_config`) against `project_compilable_cpp_sources()` (every C/C++ source minus
those under `project.cpp_external_build_dirs`). `ComplexityEngine`, `CognitiveEngine` and
`DeadCodeEngine` read the full C/C++ source list and pass the compilable list only to the
compiler-backed boundary probe (an analysis context with a database) and to dead's candidate
union (engines/dead.py:88-92, a subset of the full list).

Cases (`<command>:<name>`): `ici test` over parity-fixtures/cpp/sanitize-direct-external with and
without `cpp_external_build_dirs=['src/external']` (its src/external/broken.cpp does not
compile) and over parity-fixtures/cpp/sanitize-direct-pkg with a known (`icipkg`, whose Cflags
define the macro src/lib.cpp needs) and an unknown package; `ici complexity|cognitive|dead`
over the external fixture with and without the key. Each case runs the frozen oracle
`ici <command> --report` in a fresh `<work>/project` (with `.git`) under PATH=/usr/bin:/bin and
PKG_CONFIG_PATH=<project>/pc, and keeps the exit code, status, summary, evidence and every
target (name, status, message, file and lines; the project root written `<root>`, the work
directory's parent `<tmp>`), plus the
test engine's coverage file list and line/branch/function coverage.

`TMPDIR` chooses the work directory's parent. Usage: `capture_test_compile_inputs.py`.
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
GOLDEN = ROOT / "tests/rust-parity/cpp/test-compile-inputs-oracle.json"
BASE = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
EXTERNAL = "cpp_external_build_dirs=['src/external']\n"
FIELDS = ("target_name", "status", "message", "file_path", "start_line", "end_line")
COVERAGE = ("line_coverage", "branch_coverage", "function_coverage")
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
# name: (fixture, command, ici.toml)
CASES = {
    "test:direct-external": ("sanitize-direct-external", "test", BASE + EXTERNAL),
    "test:direct-external-unset": ("sanitize-direct-external", "test", BASE),
    "test:direct-pkg-config": ("sanitize-direct-pkg", "test", BASE + "cpp_pkg_config=['icipkg']\n"),
    "test:direct-pkg-config-missing": (
        "sanitize-direct-pkg",
        "test",
        BASE + "cpp_pkg_config=['absentpkg']\n",
    ),
}
for _command in ("complexity", "cognitive", "dead"):
    CASES[f"{_command}:direct-external"] = ("sanitize-direct-external", _command, BASE + EXTERNAL)
    CASES[f"{_command}:direct-external-unset"] = ("sanitize-direct-external", _command, BASE)


def run(fixture: str, command: str, config: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cpp-compile-inputs-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / ".git").mkdir()
        (project / "ici.toml").write_text(config, encoding="utf-8")
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", command, "--report"],
            cwd=project,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(work),
                "PYTHONPATH": str(ORACLE / "src"),
                "PKG_CONFIG_PATH": str(project / "pc"),
            },
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        text = (project / f"{command}_report.json").read_text(encoding="utf-8")
        # A compiler excerpt cut at frozen's 200 characters can end inside a second spelling of
        # the project path: its work directory's parent is written `<tmp>`.
        text = text.replace(str(project), "<root>").replace(str(work.parent), "<tmp>")
        report = json.loads(text)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    view = {
        "exit_code": result.returncode,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "targets": [{key: target.get(key) for key in FIELDS} for target in report["targets"]],
    }
    if command == "test":
        extra = report.get("extra") or {}
        view["coverage_files"] = sorted(
            row.get("file") for row in extra.get("coverage_files") or []
        )
        view["coverage"] = {key: extra.get(key) for key in COVERAGE}
    return view


def main() -> None:
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    pkg = subprocess.check_output(["/usr/bin/pkg-config", "--version"], text=True).strip()
    cases = {}
    for name, (fixture, command, config) in CASES.items():
        cases[name] = {
            "fixture": fixture,
            "command": command,
            "config": config,
            "frozen": run(fixture, command, config),
        }
        print(name, cases[name]["frozen"]["exit_code"], cases[name]["frozen"]["status"], flush=True)
    GOLDEN.write_text(
        json.dumps(
            {"oracle": EXPECTED, "gxx": gxx, "pkg_config": pkg, "cases": cases},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
