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

# Uncaptured branches (cppval3): a direct C++ test whose output passes frozen run_process's
# 1,000,000-character bound (named to sort first: frozen keeps unsorted rglob order, which on
# tmpfs lists the newest file first, and native sorts) (`_append_cpp_tool_target` 'Execution output was incomplete'), an
# instrumented test that exits non-zero without a sanitizer report (`SanitizerExecution`), an
# explicit published-artifact selection by id among two artifacts.
FLOOD = (
    "#include <cstdio>\n#include <cstring>\nint main() {\n  char line[1001];\n"
    "  std::memset(line, 'x', 1000);\n  line[1000] = 0;\n"
    "  for (int i = 0; i < 1100; ++i) std::puts(line);\n  return 0;\n}\n"
)
CASES["test:direct-flood"] = (
    "test-direct/mixed",
    "test",
    CPP,
    "tools",
    {"tests/test_aflood.cpp": FLOOD},
)
for _command in ("sanitize", "thread-sanitize"):
    CASES[f"{_command}:direct-exit"] = (
        "sanitize-direct-clean",
        _command,
        CPP,
        "tools",
        {"tests/test_ok.cpp": "int answer();\nint main() { return answer() == 42 ? 3 : 1; }\n"},
    )
CASES["deep:selected-by-id"] = (
    "package-make",
    "verify-deep",
    _BINARY_DEFS["BASE"] + _BINARY_DEFS["ENABLE"] + "artifacts=['release:shadow:tool']\n",
    "tools",
    {"Makefile": _BINARY_DEFS["TWO_MAKE"]},
)

# Uncaptured branch (cppval4): a readelf whose transcript repeats the ELF Class header with
# another value (frozen `_elf.parse_readelf` keeps the last; decisions.md "readelf 출력의 충돌
# header").
CONFLICT_READELF = (
    '#!/bin/sh\n/usr/bin/readelf "$@"\nstatus=$?\n'
    "printf '  Class:                             ELF32\\n'\nexit $status\n"
)
CASES["deep:conflicting-readelf"] = (
    "package-make",
    "verify-deep",
    _BINARY_DEFS["BASE"] + _BINARY_DEFS["ENABLE"],
    "conflict-readelf",
)
SCRIPTS = {"conflict-readelf": {"readelf": CONFLICT_READELF}}

# Unfinished test compiles (cppval5, frozen TestEngine `_run_cpp_test_case` /
# `_compile_cpp_objects`): a g++ wrapper first in PATH kills itself when it compiles
# tests/test_pass.cpp and floods stderr past run_process's 1,000,000-character bound for
# tests/test_fail.cpp; everything else runs /usr/bin/g++. "wrapped-gxx" is /usr/bin and /bin as
# symlinks (gcov present: the coverage compile path), "wrapped-gxx-compiler" the compiler-only
# directory (no gcov: the plain compile path).
UNFINISHED_GXX = (
    '#!/bin/sh\nfor a; do case "$a" in\n*tests/test_pass.cpp) kill -9 $$;;\n'
    "*tests/test_fail.cpp) i=0; while [ $i -lt 1100 ]; do printf '%01000d\\n' 0 >&2; "
    'i=$((i+1)); done; exit 1;;\nesac; done\nexec /usr/bin/g++ "$@"\n'
)
# Between the bounds (cppval5, decisions.md "next 직접 compile/link 한도"): 1,020,000 bytes of
# stderr then exit 1 on tests/test_pass.cpp, past frozen's 1,000,000 and below the former
# native 1 MiB. Sticky (decisions.md "C++ coverage compile tool error의 순서 의존"): the
# coverage compile of tests/test_fail.cpp is killed, then tests/test_pass.cpp ends with a plain
# exit-1 compiler error (frozen's unsorted rglob order of the fixture copy lists test_fail
# first, as native's sorted order does).
BETWEEN_GXX = (
    '#!/bin/sh\nfor a; do case "$a" in\n'
    "*tests/test_pass.cpp) i=0; while [ $i -lt 1020 ]; do printf '%0999d\\n' 0 >&2; "
    'i=$((i+1)); done; exit 1;;\nesac; done\nexec /usr/bin/g++ "$@"\n'
)
STICKY_GXX = (
    '#!/bin/sh\nfor a; do case "$a" in\n*tests/test_fail.cpp) kill -9 $$;;\n'
    "*tests/test_pass.cpp) echo 'tests/test_pass.cpp:1:1: error: injected' >&2; exit 1;;\n"
    'esac; done\nexec /usr/bin/g++ "$@"\n'
)
# gcov JSON report sets frozen `coverage_support` rejects (cppval5, GcovJsonError codes): the
# wrapper runs the real gcov, then edits the reports it wrote in its working directory —
# none left (missing_data), one with another GCC version (inconsistent_report_set),
# src/calc.cpp named relative to a working directory where src/src/calc.cpp also exists
# (ambiguous_source_path), or five extra reports of 4,096 non-project files (aggregate_limit).
# A format-version mix is not reachable this way (frozen first refuses the format-2 fields of
# a report relabelled format 1, `version_field`); a unit test pins it.
GCOV_WRAPPER = """#!/usr/bin/python3
import glob, gzip, json, os, subprocess, sys
args = sys.argv[1:]
if "--json-format" not in args:
    os.execv("/usr/bin/gcov", ["/usr/bin/gcov", *args])
before = set(glob.glob("*.gcov.json.gz"))
code = subprocess.call(["/usr/bin/gcov", *args])
reports = sorted(set(glob.glob("*.gcov.json.gz")) - before)
def load(path):
    with open(path, "rb") as handle:
        return json.loads(gzip.decompress(handle.read()))
def save(path, data):
    with open(path, "wb") as handle:
        handle.write(gzip.compress(json.dumps(data).encode()))
MODE = "{mode}"
if MODE == "empty":
    for path in reports:
        os.remove(path)
elif MODE == "gcc":
    data = load(reports[-1])
    data["gcc_version"] = "0.0.0"
    save(reports[-1], data)
elif MODE == "ambiguous":
    for path in reports:
        data = load(path)
        for entry in data["files"]:
            if entry["file"].endswith("/src/calc.cpp") and "/src/src/" not in entry["file"]:
                data["current_working_directory"] = entry["file"][: -len("/calc.cpp")]
                entry["file"] = "src/calc.cpp"
        save(path, data)
elif MODE == "files":
    data = load(reports[0])
    for index in range(5):
        extra = dict(data, data_file=data["data_file"] + f".extra{index}")
        extra["files"] = [
            dict(data["files"][0], file=f"/nonexistent/extra{index}/{number}.cpp")
            for number in range(4096)
        ]
        save(f"zz-extra-{index}.gcov.json.gz", extra)
sys.exit(code)
"""
WRAPPED = {
    "wrapped-gxx": {"g++": UNFINISHED_GXX},
    "wrapped-gxx-compiler": {"g++": UNFINISHED_GXX},
    "wrapped-gxx-between": {"g++": BETWEEN_GXX},
    "wrapped-gxx-sticky": {"g++": STICKY_GXX},
    **{
        f"wrapped-gcov-{mode}": {"gcov": GCOV_WRAPPER.replace("{mode}", mode)}
        for mode in ("empty", "gcc", "ambiguous", "files")
    },
}
for _name, _mode, _files in (
    ("test:direct-gcov-no-reports", "empty", {}),
    ("test:direct-gcov-mixed-gcc", "gcc", {}),
    (
        "test:direct-gcov-ambiguous-source",
        "ambiguous",
        {"src/src/calc.cpp": "int twin() { return 1; }\n"},
    ),
    ("test:direct-gcov-file-records", "files", {}),
):
    CASES[_name] = ("test-direct/mixed", "test", CPP, f"wrapped-gcov-{_mode}", _files)
for _name, _kind in (
    ("test:direct-compile-unfinished", "wrapped-gxx"),
    ("test:direct-compile-unfinished-without-gcov", "wrapped-gxx-compiler"),
    ("test:direct-compile-output-between-bounds", "wrapped-gxx-between"),
):
    CASES[_name] = ("test-direct/mixed", "test", CPP, _kind)
CASES["test:direct-compile-sticky-coverage-error"] = (
    "test-direct/mixed",
    "test",
    CPP,
    "wrapped-gxx-sticky",
)
# A direct test that outlives frozen run_process's 300 s DEFAULT_TIMEOUT (kept out of the
# default replay; `legacy_cpp_test_timeout_equals_frozen` replays it).
CASES["test:direct-timeout"] = (
    "test-direct/mixed",
    "test",
    CPP,
    "tools",
    {"tests/test_pass.cpp": "#include <unistd.h>\nint main() { sleep(400); return 0; }\n"},
)
# `ici test` on a Make build without coverage_build_argv (frozen core/make.make_plan
# 'build.make.coverage_build_argv is required for coverage').
CASES["test:make-without-coverage-argv"] = (
    "test-make",
    "test",
    CPP + "[build.make]\nenabled=true\nbuild_argv=['make','all']\ntest_argv=['make','check']\n",
    "tools",
)
# A sanitizer report with no project-owned frame (frozen `_sanitizer_diagnostics._locations`
# primary None): the instrumented test prints a fixed report naming only libc frames and
# exits 1, so frozen SanitizeEngine reports `SanitizerDiagnosticLocation` at '[external]'.
_REPORTS = {
    "sanitize": (
        "=================================================================",
        "==4242==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010 at pc "
        "0x7f1234567890 bp 0x7ffd00000000 sp 0x7ffd00000008",
        "READ of size 4 at 0x602000000010 thread T0",
        "    #0 0x7f1234567890 in memcpy (/usr/lib/x86_64-linux-gnu/libc.so.6+0x1234)",
        "    #1 0x7f1234560000 in __libc_start_main (/usr/lib/x86_64-linux-gnu/libc.so.6+0x2a1ca)",
        "",
        "SUMMARY: AddressSanitizer: heap-use-after-free "
        "(/usr/lib/x86_64-linux-gnu/libc.so.6+0x1234) in memcpy",
        "==4242==ABORTING",
    ),
    "thread-sanitize": (
        "==================",
        "WARNING: ThreadSanitizer: data race (pid=4242)",
        "  Write of size 4 at 0x7b0400000000 by thread T1:",
        "    #0 memset /usr/lib/x86_64-linux-gnu/libc.so.6+0x1234 (libc.so.6+0x1234)",
        "",
        "  Previous read of size 4 at 0x7b0400000000 by main thread:",
        "    #0 memcpy /usr/lib/x86_64-linux-gnu/libc.so.6+0x5678 (libc.so.6+0x5678)",
        "",
        "SUMMARY: ThreadSanitizer: data race "
        "(/usr/lib/x86_64-linux-gnu/libc.so.6+0x1234) in memset",
        "==================",
    ),
}
# Past frozen MAX_SANITIZER_FRAMES (32): the same report with 33 libc frames
# ('sanitizer stack exceeds the bounded frame limit').
_REPORTS["sanitize-frames"] = (
    *_REPORTS["sanitize"][:3],
    *(
        f"    #{index} 0x7f12345{index:05x} in memcpy (/usr/lib/x86_64-linux-gnu/libc.so.6+0x1234)"
        for index in range(33)
    ),
    *_REPORTS["sanitize"][5:],
)
for _command, _lines in _REPORTS.items():
    _text = "".join(json.dumps(line + "\n")[1:-1] for line in _lines)
    _case = "sanitize:direct-frame-bound" if _command == "sanitize-frames" else None
    CASES[_case or f"{_command}:direct-unlocated"] = (
        "sanitize-direct-clean",
        "sanitize" if _case else _command,
        CPP,
        "tools",
        {
            "tests/test_ok.cpp": "#include <cstdio>\n#include <unistd.h>\nint answer();\n"
            f'int main() {{\n  std::fputs("{_text}", stderr);\n  std::fflush(stderr);\n'
            "  _exit(answer() == 42 ? 1 : 2);\n}\n"
        },
    )

# A located report on a line with multibyte characters (frozen `_safe_source_location` keeps the
# producer's raw byte column, also one inside a character): frame #0 after "éé" (byte 8,
# character 6) and frame #1 inside the first "é" (byte 5).
_MULTIBYTE = (
    "==4242==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010 at pc 0x1 "
    "bp 0x2 sp 0x3\\nREAD of size 4 at 0x602000000010 thread T0\\n"
    "    #0 0x1 in main %s:2:8\\n    #1 0x2 in main %s:2:5\\n\\n"
    "SUMMARY: AddressSanitizer: heap-use-after-free %s:2:8 in main\\n==4242==ABORTING\\n"
)
CASES["sanitize:direct-multibyte-column"] = (
    "sanitize-direct-clean",
    "sanitize",
    CPP,
    "tools",
    {
        "tests/test_ok.cpp": "#include <cstdio>\n// \u00e9\u00e9 marker\n#include <unistd.h>\n"
        "int answer();\nint main() {\n"
        f'  std::fprintf(stderr, "{_MULTIBYTE}", __FILE__, __FILE__, __FILE__);\n'
        "  std::fflush(stderr);\n  _exit(answer() == 42 ? 1 : 2);\n}\n"
    },
)

# The same unlocated reports from a CMake/ctest adapter suite (cppval5): the defect test of
# test-cmake/sanitize prints the fixed report and exits 1, beside a passing case, so frozen
# `_run_cpp_sanitizer_via_adapter` keeps its per-case verdicts next to the unlocated report.
for _command in ("sanitize", "thread-sanitize"):
    _text = "".join(json.dumps(line + "\n")[1:-1] for line in _REPORTS[_command])
    CASES[f"{_command}:cmake-unlocated"] = (
        "test-cmake/sanitize",
        _command,
        CPP,
        "tools",
        {
            "tests/test_uaf.cpp": "#include <cstdio>\n#include <unistd.h>\nint add(int, int);\n"
            f'int main() {{\n  std::fputs("{_text}", stderr);\n  std::fflush(stderr);\n'
            "  _exit(add(2, 3) == 5 ? 1 : 2);\n}\n"
        },
    )

# Legacy `max_coverage_regression` (cppval5, frozen core/baseline._coverage_regression_records):
# `ici verify --write-baseline base.json`, then BASELINE_EDITS over the tree, then
# `ici verify --report --baseline base.json` (with `--fail-on-new` for the gate case). Only the
# test engine runs and its minimums are 0, so the suite verdict is the baseline gate's. The
# written baseline, both exit codes and the whole compared report are kept.
_QUIET = "".join(
    f"[engines.{_engine}]\nenabled=false\n"
    for _engine in (
        "line",
        "lint",
        "compile_db",
        "type",
        "python_compat",
        "resource",
        "security",
        "cycle",
        "complexity",
        "sanitize",
        "dead",
        "dup",
        "exception",
    )
)
_PASSING = {
    "tests/test_fail.cpp": "int twice(int a);\nint main() { return twice(2) == 4 ? 0 : 1; }\n"
}
# A branch in add() keeps the branch axis measured (a scope without branches is native's n/a
# 'Threshold: Branch coverage' SKIP, validation.json coverage_policy.py:247:12).
_BRANCHY = (
    "int add(int a, int b) {\n  if (a > 0)\n    return a + b;\n  return b + a;\n}\n"
    "int twice(int a) { return a * 2; }\n"
)
BASELINE_EDITS = {"src/calc.cpp": _BRANCHY + "int thrice(int a) { return a * 3; }\n"}
for _name, _command, _tolerance in (
    ("verify-baseline:coverage-breach", "verify-baseline-gate", "1.5"),
    ("verify-baseline:coverage-within", "verify-baseline", "50.0"),
):
    CASES[_name] = (
        "test-direct/mixed",
        _command,
        CPP + "[engines.test]\nmin_tem_score=0.0\nmin_line_cov=0.0\nmin_func_cov=0.0\n"
        f"min_branch_cov=0.0\nmin_file_cov=0.0\nmin_file_statements=1\nmax_coverage_regression={_tolerance}\n"
        + _QUIET,
        "tools",
        {**_PASSING, "src/calc.cpp": _BRANCHY},
    )
# A production source without statements leaves no gcov record (frozen
# coverage_support._require_complete_source_coverage, `incomplete_source_coverage`).
CASES["test:direct-empty-source"] = (
    "test-direct/mixed",
    "test",
    CPP,
    "tools",
    {**_PASSING, "src/empty.cpp": "// no statements\n"},
)
# gcov JSON rows (frozen coverage_support.parse_gcov_json_dir): function rows sorted by (file,
# start_line, start_column, name) where the mangled names sort the other way (twice before add).
CASES["test:direct-rows"] = (
    "test-direct/mixed",
    "test",
    CPP,
    "tools",
    {
        **_PASSING,
        "src/calc.cpp": "int twice(int a) { return a * 2; }\n"
        "int add(int a, int b) {\n  if (a > 0)\n    return a + b;\n  return b + a;\n}\n",
    },
)


def farm_path(directory: Path, path_kind: str) -> str:
    """/usr/bin and /bin as symlinks without readelf, or with a readelf cut to 4 lines or one
    that appends a conflicting Class header."""

    directory.mkdir()
    scripts = {"short-readelf": {"readelf": SHORT_READELF}, **SCRIPTS}.get(path_kind, {})
    for name, script in scripts.items():
        (directory / name).write_text(script, encoding="utf-8")
        (directory / name).chmod(0o755)
    for parent in ("/usr/bin", "/bin"):
        for entry in sorted(os.listdir(parent)):
            if entry != "readelf" and not (directory / entry).exists():
                (directory / entry).symlink_to(Path(parent) / entry)
    return str(directory)


def wrapped_path(directory: Path, path_kind: str) -> str:
    """The case's wrapper scripts first, then the compiler-only tools or every /usr/bin and
    /bin entry as a symlink (readelf included)."""

    directory.mkdir()
    for name, script in WRAPPED[path_kind].items():
        (directory / name).write_text(script, encoding="utf-8")
        (directory / name).chmod(0o755)
    if path_kind == "wrapped-gxx-compiler":
        for tool in ("as", "ld", "x86_64-linux-gnu-as", "x86_64-linux-gnu-ld"):
            if Path("/usr/bin", tool).exists():
                os.symlink(Path("/usr/bin", tool), directory / tool)
        return str(directory)
    for parent in ("/usr/bin", "/bin"):
        for entry in sorted(os.listdir(parent)):
            if not (directory / entry).exists() and not (directory / entry).is_symlink():
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
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        if path_kind == "tools":
            path = "/usr/bin:/bin"
        elif path_kind == "compiler":
            path = compiler_path(work / "bin")
        elif path_kind in WRAPPED:
            path = wrapped_path(work / "bin", path_kind)
        else:
            path = farm_path(work / "bin", path_kind)
        environment = {
            "PATH": path,
            "HOME": str(work),
            "LC_CTYPE": "C.UTF-8",
            "PYTHONPATH": str(ORACLE / "src"),
        }

        def ici(argv: list) -> subprocess.CompletedProcess:
            return subprocess.run(
                [PYTHON, "-m", "ici", *argv],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )

        baseline = None
        if command == "verify-deep":
            argv = ["verify", "--profile", "deep", "--report"]
        elif command.startswith("verify-baseline"):
            written = ici(["verify", "--write-baseline", "base.json"])
            baseline = {
                "exit_code": written.returncode,
                "report": json.loads(
                    (project / "base.json")
                    .read_text(encoding="utf-8")
                    .replace(str(project), "<root>")
                ),
            }
            for relative, text in BASELINE_EDITS.items():
                (project / relative).write_text(text, encoding="utf-8")
            argv = ["verify", "--report", "--baseline", "base.json"]
            argv += ["--fail-on-new"] if command == "verify-baseline-gate" else []
        else:
            argv = [command, *([] if command == "build" else ["--report"])]
        result = ici(argv)
        if command == "verify-deep" or command.startswith("verify-baseline"):
            name = "verify"
        else:
            name = command.replace("-", "_")
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
        if files:
            # The replay writes the same case files over the fixture.
            case["files"] = files
        if path_kind in SCRIPTS:
            # The replay writes the same PATH scripts.
            case["scripts"] = SCRIPTS[path_kind]
        if path_kind in WRAPPED:
            case["scripts"] = WRAPPED[path_kind]
        if baseline is not None:
            # The replay writes the baseline, applies the same edit, then compares.
            case["baseline"] = baseline
            case["edit"] = BASELINE_EDITS
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
