"""Development-only full saved-result captures of the frozen C++ validation commands.

Never called by the Rust product. Each case copies a parity-fixtures/cpp tree into a fresh
temporary project (with an empty `.git`), writes its `ici.toml`, runs the frozen oracle
`ici <command> --report` with an explicit PATH and records the exit code, stdout and the whole
saved `<command>_report.json`. Only the project root is normalized (`<root>`); every other key,
value, type and list is kept so the Rust replay (`crates/ici-cli/tests/legacy_engine_extras.rs`
`legacy_cpp_validation_results_equal_frozen_except_allowed`) compares the full result.

Usage: `capture_validation_full.py [case-prefix ...]` recaptures the named cases (all when none)
and keeps the other cases of the existing golden file.
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
GOLDEN = ROOT / "tests/rust-parity/cpp/validation-full-oracle.json"
FIXTURES = ROOT / "parity-fixtures/cpp"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
CPP = "[project]\ntype='cpp'\nsource_dirs=['src']\n"
MAKE = (
    CPP + "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
    "coverage_build_argv=['make','all','VARIANT=coverage']\n"
    "sanitize_build_argv=['make','all','VARIANT=sanitize']\n"
)
# name: (fixture, command, ici.toml, PATH kind). PATH kinds: "tools" = /usr/bin:/bin (g++,
# gcov, cmake, ctest, make); "compiler" = a directory with only g++/as/ld (no gcov).
CASES = {
    # Direct g++ test runs (frozen TestEngine._run_cpp_tests without a build adapter).
    "test:direct-mixed": ("test-direct/mixed", "test", CPP, "tools"),
    "test:direct-broken": ("test-direct/broken", "test", CPP, "tools"),
    "test:direct-all-broken": ("test-direct/all-broken", "test", CPP, "tools"),
    "test:direct-abort": ("test-direct/abort", "test", CPP, "tools"),
    "test:direct-lib-broken": ("test-direct/lib-broken", "test", CPP, "tools"),
    "test:direct-mixed-without-gcov": ("test-direct/mixed", "test", CPP, "compiler"),
    "test:direct-broken-without-gcov": ("test-direct/broken", "test", CPP, "compiler"),
    "test:direct-coverage-required-without-gcov": (
        "test-direct/mixed",
        "test",
        CPP + "[engines.test]\ncoverage_required=true\n",
        "compiler",
    ),
    "test:direct-quality-enabled": (
        "test-direct/mixed",
        "test",
        CPP + "[engines.test.quality]\nenabled=true\nrepeat_runs=3\n",
        "tools",
    ),
    "test:direct-thresholds": (
        "test-direct/mixed",
        "test",
        CPP + "[engines.test]\nmin_tem_score=4.5\nmin_line_cov=100.0\nmin_branch_cov=10.0\n",
        "tools",
    ),
    # CMake/ctest adapter runs (frozen _run_cpp_tests_via_adapter).
    "test:cmake-plain": ("test-cmake/plain", "test", CPP, "tools"),
    "test:cmake-suite": ("test-cmake/suite", "test", CPP, "tools"),
    "test:cmake-broken-build": ("test-cmake/broken-build", "test", CPP, "tools"),
    # Make adapter runs (frozen core/cmake.run_tests with the Make backend).
    "test:make-pass": ("test-make", "test", MAKE + "test_argv=['make','check']\n", "tools"),
    "test:make-fail": ("test-make", "test", MAKE + "test_argv=['make','check-all']\n", "tools"),
    # Coverage policy keys on a C++ suite: changed lines with their minimum, the regression
    # tolerance recorded in the policy snapshot, and an unusable changed-line selection.
    "test:direct-policy": (
        "test-direct/mixed",
        "test",
        CPP + "[engines.test]\nchanged_lines=['src/calc.cpp:1-2']\nmin_changed_line_cov=90.0\n"
        "max_coverage_regression=1.5\n",
        "tools",
    ),
    "test:direct-changed-invalid": (
        "test-direct/mixed",
        "test",
        CPP + "[engines.test]\nchanged_lines=['src/calc.cpp:2-1']\n",
        "tools",
    ),
}
SANITIZE_FIXTURES = {
    "direct-clean": ("sanitize-direct-clean", CPP),
    "direct-defect": ("sanitize-direct", CPP),
    "direct-ubsan": ("sanitize-direct-ubsan", CPP),
    "direct-leak": ("sanitize-direct-leak", CPP),
    "direct-external": (
        "sanitize-direct-external",
        CPP + "cpp_external_build_dirs=['src/external']\n",
    ),
    "noscope": ("sanitize-noscope", CPP),
    "testless-direct": ("sanitize-testless", CPP),
    "cmake-plain": ("test-cmake/plain", CPP),
    "cmake-defect": ("test-cmake/sanitize", CPP),
    "make-pass": ("test-make", MAKE + "test_argv=['make','check']\n"),
}
for _name, (_fixture, _config) in SANITIZE_FIXTURES.items():
    for _command in ("sanitize", "thread-sanitize"):
        CASES[f"{_command}:{_name}"] = (_fixture, _command, _config, "tools")
TSAN_MAKE = (
    CPP + "[build.make]\nenabled=true\nbuild_argv=['make','all']\n"
    "thread_sanitize_build_argv=['make','all','VARIANT=thread']\n"
)
CASES["thread-sanitize:make-race"] = (
    "tsan-make",
    "thread-sanitize",
    TSAN_MAKE + "test_argv=['make','check-race']\n",
    "tools",
)
CASES["thread-sanitize:make-clean"] = (
    "tsan-make",
    "thread-sanitize",
    TSAN_MAKE + "test_argv=['make','check']\n",
    "tools",
)
# Legacy `ici build` (frozen BuildEngine): the package fixtures carry their own ici.toml;
# frozen `ici build` takes no --report and saves no report, so stdout and exit are compared.
for _name, _fixture in (
    ("make", "package-make"),
    ("direct", "package-direct"),
    ("cmake", "package-cmake"),
):
    CASES[f"build:{_name}"] = (_fixture, "build", "", "tools")

# Legacy deep `ici verify` (frozen BuildEngine + BinaryCompatibilityEngine): the cases of the
# development-only capture_binary_compat.py (its definitions, not its capture), with the saved
# `build` and `binary_compat` engine results kept in full. Frozen runs every deep engine; the
# other engines and the suite exit belong to their own rows and are not kept.
_BINARY = (ROOT / "tests/rust-parity/cpp/capture_binary_compat.py").read_text(encoding="utf-8")
_BINARY_DEFS: dict = {"__file__": str(ROOT / "tests/rust-parity/cpp/capture_binary_compat.py")}
exec(_BINARY[: _BINARY.index("\ncases = {")], _BINARY_DEFS)
DEEP_ENGINES = ("build", "binary_compat")
READELF_PATHS = {"missing-readelf": "no-readelf", "truncated-readelf": "short-readelf"}
SHORT_READELF = _BINARY_DEFS["REPLACED"]["truncated-readelf"]["readelf"]
for _name, _changes in _BINARY_DEFS["CASES"].items():
    _files = {key: value for key, value in _changes.items() if key != "ici.toml"}
    CASES[f"deep:{_name}"] = (
        "package-make",
        "verify-deep",
        _changes.get("ici.toml", _BINARY_DEFS["BASE"] + _BINARY_DEFS["ENABLE"]),
        READELF_PATHS.get(_name, "tools"),
        _files,
    )


def farm_path(directory: Path, path_kind: str) -> str:
    """/usr/bin and /bin as symlinks without readelf, or with a readelf cut to 4 lines."""

    directory.mkdir()
    if path_kind == "short-readelf":
        (directory / "readelf").write_text(SHORT_READELF, encoding="utf-8")
        (directory / "readelf").chmod(0o755)
    for parent in ("/usr/bin", "/bin"):
        for entry in sorted(os.listdir(parent)):
            if entry != "readelf" and not (directory / entry).exists():
                (directory / entry).symlink_to(Path(parent) / entry)
    return str(directory)


def compiler_path(directory: Path) -> str:
    directory.mkdir()
    for tool in ("g++", "as", "ld", "x86_64-linux-gnu-as", "x86_64-linux-gnu-ld"):
        if Path("/usr/bin", tool).exists():
            os.symlink(Path("/usr/bin", tool), directory / tool)
    return str(directory)


def run(fixture: str, command: str, config: str, path_kind: str, files: dict | None = None) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ici-cppval-capture-"))
    try:
        project = work / "project"
        shutil.copytree(FIXTURES / fixture, project)
        (project / ".git").mkdir()
        if config:
            (project / "ici.toml").write_text(config, encoding="utf-8")
        for relative, text in (files or {}).items():
            (project / relative).write_text(text, encoding="utf-8")
        if path_kind == "tools":
            path = "/usr/bin:/bin"
        elif path_kind == "compiler":
            path = compiler_path(work / "bin")
        else:
            path = farm_path(work / "bin", path_kind)
        if command == "verify-deep":
            argv = ["verify", "--profile", "deep", "--report"]
        else:
            argv = [command, *([] if command == "build" else ["--report"])]
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
        name = "verify" if command == "verify-deep" else command.replace("-", "_")
        report = project / (name + "_report.json")
        text = report.read_text(encoding="utf-8") if report.exists() else "null"
        stdout = result.stdout
        for raw, mark in ((str(project), "<root>"), (str(work / "bin"), "<compiler-bin>")):
            text = text.replace(raw, mark)
            stdout = stdout.replace(raw, mark)
        saved = json.loads(text)
        case = {
            "fixture": fixture,
            "command": command,
            "config": config,
            "path": path_kind,
            "exit_code": result.returncode,
            "stdout": stdout,
            "report": saved,
        }
        if command == "verify-deep":
            # Only the build and binary_compat engines; the suite exit and stdout cover every
            # deep engine and are not this capture's subject.
            case |= {"files": files or {}, "exit_code": None, "stdout": ""}
            case["report"] = {
                engine["engine_name"]: engine
                for engine in saved["results"]
                if engine.get("engine_name") in DEEP_ENGINES
            }
        return case
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    prefixes = sys.argv[1:]
    gxx = subprocess.check_output(["/usr/bin/g++", "--version"], text=True).splitlines()[0]
    cmake = subprocess.check_output(["/usr/bin/cmake", "--version"], text=True).splitlines()[0]
    existing = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    cases = dict(existing.get("cases", {}))
    for name, spec in CASES.items():
        if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
            continue
        cases[name] = run(*spec)
        print(
            name, cases[name]["exit_code"], (cases[name]["report"] or {}).get("status"), flush=True
        )
    GOLDEN.write_text(
        json.dumps(
            {"oracle": EXPECTED, "gxx": gxx, "cmake": cmake, "cases": dict(sorted(cases.items()))},
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
