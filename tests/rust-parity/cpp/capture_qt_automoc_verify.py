"""Development-only frozen legacy `ici verify` over a Qt AUTOMOC CMake project; never called by Rust.

    ICI_TEST_CLANG_TIDY=/path/to/clang-tidy python -I -B capture_qt_automoc_verify.py

parity-fixtures/cpp/qt-automoc is a legacy CMake project (`source_dirs = ['src']`) whose library
and test targets set `CMAKE_AUTOMOC`, so every build tree's compilation database also records the
generated `<target>_autogen/mocs_compilation.cpp` units. Frozen `_cpp_include_graph._selected_units`
and `_cpp_tooling.selected_units` trace / replay only the units of selected production sources,
so the cycle engine traces `src/counter.cpp` alone and clang-tidy and the compiler-backed
complexity boundaries replay it alone (frozen `_cpp_lint` syntax checks still compile the Qt
generated units). Only the lint, test, cycle and complexity engines run. Two cases: `plain` with
PATH=/usr/bin:/bin (no clang-tidy: lexical complexity) and `clang-tidy` with the directory of
`$ICI_TEST_CLANG_TIDY` first on PATH (frozen boundary mode). Records the exit code, the suite
status and per engine the status, evidence, summary, targets and the cycle/boundary counters.
The project path is stored as {root}; the frozen checkout is `$ICI_FROZEN_ORACLE` (6fe3e405).
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path(os.environ.get("ICI_FROZEN_ORACLE", "/tmp/ici-rust-replacement-20261028/oracle"))
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
FIXTURE = ROOT / "parity-fixtures/cpp/qt-automoc"
GOLDEN = ROOT / "tests/rust-parity/cpp/qt-automoc-verify-oracle.json"
FIELDS = ("target_name", "status", "message", "start_line", "file_path")
ENGINES = ("lint", "test", "cycle", "complexity")
OFF = [
    "line",
    "compile_db",
    "type",
    "python_compat",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "cognitive",
    "security",
    "resource",
    "binary_compat",
    "integration",
]
CONFIG = "[project]\ntype='cpp'\nsource_dirs=['src']\n" + "".join(
    f"[engines.{name}]\nenabled=false\n" for name in OFF
)
EXTRA = {
    "lint": ("clang_tidy_configurations_checked", "clang_tidy_sources_checked", "violations_count"),
    "cycle": ("cpp_configurations_checked", "cpp_include_resolution", "cpp_cycles"),
    "complexity": (
        "cpp_boundary_mode",
        "cpp_boundary_configurations_checked",
        "cpp_boundary_sources_checked",
        "cpp_boundary_errors",
        "cpp_exact_boundaries",
        "cpp_estimated_boundaries",
    ),
}
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
for tool in ("clang-tidy", "clazy-standalone"):
    assert shutil.which(tool, path="/usr/bin:/bin") is None, f"{tool} must be absent"
TIDY = Path(os.environ["ICI_TEST_CLANG_TIDY"]).resolve()
CMAKE = subprocess.check_output(["/usr/bin/cmake", "--version"], text=True).splitlines()[0]
TIDY_VERSION = next(
    line.strip()
    for line in subprocess.check_output([str(TIDY), "--version"], text=True).splitlines()
    if "version" in line
)
CASES = {"plain": "/usr/bin:/bin", "clang-tidy": f"{TIDY.parent}:/usr/bin:/bin"}


def run(path):
    work = Path(tempfile.mkdtemp(prefix="ici-qt-automoc-")).resolve()
    try:
        project = work / "project"
        shutil.copytree(FIXTURE, project)
        (project / "ici.toml").write_text(CONFIG, encoding="utf-8")
        git = ["git", "-c", "user.email=ici@example.invalid", "-c", "user.name=ici"]
        for args in (["init", "-q", "."], ["add", "-A"], ["commit", "-qm", "fixture"]):
            subprocess.run(git + args, cwd=project, check=True)
        (work / "home").mkdir()
        result = subprocess.run(
            [PYTHON, "-B", "-m", "ici", "verify", "--report"],
            cwd=project,
            env={
                "PATH": path,
                "HOME": str(work / "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "QT_QPA_PLATFORM": "offscreen",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        report = json.loads(
            (project / "verify_report.json")
            .read_text(encoding="utf-8")
            .replace(str(project), "{root}")
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    engines = {}
    for engine in report["results"]:
        name = engine["engine_name"]
        if name not in ENGINES:
            continue
        engines[name] = {
            "status": engine["status"],
            "evidence": engine["evidence"],
            "summary": engine["summary"],
            "targets": [{key: target[key] for key in FIELDS} for target in engine["targets"]],
            "extra": {key: engine["extra"].get(key) for key in EXTRA.get(name, ())},
        }
    return {
        "exit_code": result.returncode,
        "suite_status": report["suite_status"],
        "engines": engines,
    }


def main():
    cases = {name: {"path": path, "frozen": run(path)} for name, path in CASES.items()}
    for name, case in cases.items():
        print(name, json.dumps(case["frozen"], ensure_ascii=False)[:600])
    for case in cases.values():
        case["path"] = case["path"].replace(str(TIDY.parent), "{clang-tidy-dir}")
    GOLDEN.write_text(
        json.dumps(
            {
                "oracle": EXPECTED,
                "config": CONFIG,
                "cmake": CMAKE,
                "clang_tidy": TIDY_VERSION,
                "cases": cases,
            },
            indent=1,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
