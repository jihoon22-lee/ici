"""Development-only full saved-result captures of frozen C++ compile-database preparation.

Never called by the Rust product. Frozen `prepare_qmake_compilation_context` and
`prepare_cmake_compilation_context` (core/qmake_context.py, core/cmake_context.py) run before
the legacy `ici verify` engines. When the canonical configure, capture build or capture fails,
the run keeps going without a database and the compile_db engine reports the preparation
diagnostic (`qmake-configure-failed`, `qmake-capture-build-failed`, `qmake-capture-unavailable`,
`cmake-configure-failed`); a capture that misses a production unit adds
`qmake-capture-incomplete`. Each case copies a parity-fixtures/cpp tree into a fresh temporary
project (with an empty `.git`), writes its `ici.toml` (every engine but compile_db disabled) and
case files, runs the frozen oracle `ici verify --report` with PATH=/usr/bin:/bin and records the
exit code, stdout and the whole saved `verify_report.json`. Only the project root is normalized
(`<root>`). The Rust replay is `crates/ici-cli/tests/legacy_engine_extras.rs`
`legacy_cpp_preparation_failures_equal_frozen_except_allowed`. The `export:` cases run
`ici export-compilation-context --prepare` (frozen core/compilation_export.load_export_context)
over the same failing preparations and record its exit code, stdout and stderr.

Usage: `capture_preparation_failures.py [case-prefix ...]` recaptures the named cases (all when
none) and keeps the other cases of the existing golden file.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/preparation-failures-oracle.json"
FIXTURES = ROOT / "parity-fixtures/cpp"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
ENGINES = (
    "lint",
    "line",
    "test",
    "type",
    "python_compat",
    "cognitive",
    "resource",
    "security",
    "cycle",
    "complexity",
    "sanitize",
    "thread_sanitize",
    "dead",
    "dup",
    "exception",
    "build",
    "binary_compat",
    "integration",
)
ONLY_COMPILE_DB = "".join(f"[engines.{name}]\nenabled=false\n" for name in ENGINES)
QMAKE = "[project]\ntype='cpp'\nsource_dirs=['tests']\n" + ONLY_COMPILE_DB
CMAKE = "[project]\ntype='cpp'\nsource_dirs=['src']\n" + ONLY_COMPILE_DB
# The context consumers beside compile_db: lint, cycle, dead and complexity also run.
SOURCE_ENGINES = "".join(
    f"[engines.{name}]\nenabled=false\n"
    for name in ENGINES
    if name not in ("lint", "cycle", "dead", "complexity")
)
QMAKE_SOURCES = "[project]\ntype='cpp'\nsource_dirs=['tests']\n" + SOURCE_ENGINES
CMAKE_SOURCES = "[project]\ntype='cpp'\nsource_dirs=['src']\n" + SOURCE_ENGINES
PRO = (FIXTURES / "test-qmake/tests/tests.pro").read_text(encoding="utf-8")
UNIT = (FIXTURES / "test-qmake/tests/test_unit.cpp").read_text(encoding="utf-8")
LISTS = (FIXTURES / "test-cmake/plain/CMakeLists.txt").read_text(encoding="utf-8")
# A PATH `qmake6` that refuses only the recorder-wrapped capture configure (frozen's
# `compiler-wrapper` under `ici-qmake-capture-*`, native's `__qmake-record` mode); every other
# call runs the real qmake6.
REFUSING_QMAKE = (
    "#!/bin/sh\n"
    'for argument in "$@"; do\n'
    '  case "$argument" in\n'
    "    *compiler-wrapper*|*__qmake-record*) echo 'capture configure refused' >&2; exit 3;;\n"
    "  esac\n"
    "done\n"
    'exec /usr/bin/qmake6 "$@"\n'
)
WRAPPED = {"wrapped-qmake": {"qmake6": REFUSING_QMAKE}}
# name: (fixture, ici.toml, files written over the fixture[, PATH kind])
CASES = {
    # Positive: the capture covers every production unit (no diagnostic).
    "verify:qmake-captured": ("test-qmake", QMAKE, {}),
    # A production unit the qmake build never compiles: frozen `_with_capture_coverage`.
    "verify:qmake-capture-incomplete": (
        "test-qmake",
        QMAKE,
        {"tests/extra.cpp": "int extra() { return 1; }\n"},
    ),
    # The canonical probe configure fails (qmake `error()`).
    "verify:qmake-configure-failed": (
        "test-qmake",
        QMAKE,
        {"tests/tests.pro": PRO + "error(boom)\n"},
    ),
    # The capture build fails on a production unit that does not compile.
    "verify:qmake-capture-build-failed": (
        "test-qmake",
        QMAKE,
        {"tests/test_unit.cpp": UNIT + "int broken( {\n"},
    ),
    # QMAKE_CXX is not one resolvable compiler program: the capture is unavailable (warning).
    "verify:qmake-capture-unavailable": (
        "test-qmake",
        QMAKE,
        {"tests/tests.pro": PRO + "QMAKE_CXX = /usr/bin/env g++\n"},
    ),
    # The probe configure succeeds, the recorder-wrapped capture configure fails.
    "verify:qmake-capture-configure-failed": ("test-qmake", QMAKE, {}, "wrapped-qmake"),
    # The same failures with the other context consumers (lint, cycle, dead, complexity).
    "verify-sources:qmake-configure-failed": (
        "test-qmake",
        QMAKE_SOURCES,
        {"tests/tests.pro": PRO + "error(boom)\n"},
    ),
    "verify-sources:qmake-capture-unavailable": (
        "test-qmake",
        QMAKE_SOURCES,
        {"tests/tests.pro": PRO + "QMAKE_CXX = /usr/bin/env g++\n"},
    ),
    "verify-sources:qmake-capture-incomplete": (
        "test-qmake",
        QMAKE_SOURCES,
        {"tests/extra.cpp": "int extra() { return 1; }\n"},
    ),
    "verify-sources:cmake-configure-failed": (
        "test-cmake/plain",
        CMAKE_SOURCES,
        {"CMakeLists.txt": LISTS + 'message(FATAL_ERROR "boom")\n'},
    ),
    # Positive: the canonical CMake configure exports the database.
    "verify:cmake-configured": ("test-cmake/plain", CMAKE, {}),
    # The canonical CMake configure fails.
    "verify:cmake-configure-failed": (
        "test-cmake/plain",
        CMAKE,
        {"CMakeLists.txt": LISTS + 'message(FATAL_ERROR "boom")\n'},
    ),
}


# Export cases: the verify case whose project and files they reuse.
EXPORTS = (
    "verify:qmake-captured",
    "verify:cmake-configured",
    "verify:qmake-capture-incomplete",
    "verify:qmake-configure-failed",
    "verify:qmake-capture-build-failed",
    "verify:qmake-capture-unavailable",
    "verify:qmake-capture-configure-failed",
    "verify:cmake-configure-failed",
)


def wrapped_path(directory: Path, path_kind: str) -> str:
    """The case's scripts first, then every /usr/bin and /bin entry as a symlink."""

    directory.mkdir()
    for name, script in WRAPPED[path_kind].items():
        (directory / name).write_text(script, encoding="utf-8")
        (directory / name).chmod(0o755)
    for parent in ("/usr/bin", "/bin"):
        for entry in sorted(os.listdir(parent)):
            if not (directory / entry).exists() and not (directory / entry).is_symlink():
                (directory / entry).symlink_to(Path(parent) / entry)
    return str(directory)


def run(
    fixture: str, config: str, files: dict, path_kind: str = "tools", export: bool = False
) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cppval-capture-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / ".git").mkdir()
        (project / "ici.toml").write_text(config, encoding="utf-8")
        for relative, text in files.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        path = "/usr/bin:/bin" if path_kind == "tools" else wrapped_path(work / "bin", path_kind)
        argv = ["export-compilation-context", "--prepare"] if export else ["verify", "--report"]
        result = subprocess.run(
            [PYTHON, "-m", "ici", *argv],
            cwd=project,
            env={
                "PATH": path,
                "HOME": str(work),
                "LC_CTYPE": "C.UTF-8",
                "PYTHONPATH": str(ORACLE / "src"),
            },
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        if export:
            return {
                "fixture": fixture,
                "command": "export-compilation-context",
                "config": config,
                "path": path_kind,
                "exit_code": result.returncode,
                "stdout": result.stdout.replace(str(project), "<root>"),
                "stderr": result.stderr.replace(str(project), "<root>"),
                "report": None,
                **({"files": files} if files else {}),
                **({"scripts": WRAPPED[path_kind]} if path_kind in WRAPPED else {}),
            }
        report = project / "verify_report.json"
        text = report.read_text(encoding="utf-8") if report.exists() else "null"
        case = {
            "fixture": fixture,
            "command": "verify",
            "config": config,
            "path": path_kind,
            "exit_code": result.returncode,
            "stdout": result.stdout.replace(str(project), "<root>"),
            "report": json.loads(text.replace(str(project), "<root>")),
        }
        if files:
            case["files"] = files
        if path_kind in WRAPPED:
            # The replay writes the same PATH scripts.
            case["scripts"] = WRAPPED[path_kind]
        return case
    finally:
        shutil.rmtree(work, ignore_errors=True)


def first_line(argv: list) -> str:
    return subprocess.check_output(argv, text=True).splitlines()[0]


def main() -> None:
    prefixes = sys.argv[1:]
    existing = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    cases = dict(existing.get("cases", {}))
    for name, spec in CASES.items():
        if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
            continue
        cases[name] = run(*spec)
        print(
            name, cases[name]["exit_code"], (cases[name]["report"] or {}).get("status"), flush=True
        )
    for name in EXPORTS:
        export = name.replace("verify:", "export:")
        if prefixes and not any(export.startswith(prefix) for prefix in prefixes):
            continue
        cases[export] = run(*CASES[name], export=True)
        print(export, cases[export]["exit_code"], cases[export]["stderr"].strip(), flush=True)
    GOLDEN.write_text(
        json.dumps(
            {
                "oracle": EXPECTED,
                "gxx": first_line(["/usr/bin/g++", "--version"]),
                "cmake": first_line(["/usr/bin/cmake", "--version"]),
                "qmake": first_line(["/usr/bin/qmake6", "--version"]),
                "cases": dict(sorted(cases.items())),
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
