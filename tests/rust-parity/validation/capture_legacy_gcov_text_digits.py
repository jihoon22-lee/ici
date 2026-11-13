"""Development-only frozen captures of legacy gcov text numbers outside ASCII; never called by Rust.

    python -I -B capture_legacy_gcov_text_digits.py [name-prefix ...]

Same driver and document shape as capture_legacy_cpp_test_edges.py (tests/rust-parity/cpp/
capture_validation_full.py `run`, loaded by path, plus each command's stderr), so
`legacy_engine_extras.rs` replays the cases against the native CLI.

Root ruling 2026-11-14 ("follow frozen; a frozen failure fails natively the same way"): frozen
analysis/coverage_support.py reads gcov text numbers with `str.isdigit()` and `int()`
(`_count_gcov_source` line numbers and counts, `_count_gcov_branch` 'taken',
`parse_gcov_functions` 'called'). gcov never writes such numbers, so each case is a gcov wrapper
that hides `--json-format` (the legacy text path) and appends crafted records to the real
`calc.cpp` report:
- a Unicode decimal (Nd) line number on a `#####:` record is a missed statement, and Nd counts,
  'taken' and 'called' values are numbers;
- an `isdigit()` character that is no decimal digit (a superscript, a circled digit) or a line
  number over 4300 digits makes `int()` raise ValueError out of `parse_gcov_dir` (only 'taken' and
  'called' catch it), standalone `ici test` and under `verify`;
- a `#####:` line number past u64 is a miss to frozen (native refuses the reading).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests/rust-parity/validation/legacy-gcov-text-digits-oracle.json"
SPEC = importlib.util.spec_from_file_location(
    "capture_validation_full", ROOT / "tests/rust-parity/cpp/capture_validation_full.py"
)
cvf = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cvf)

# The wrapper is ASCII: CRAFTED is written with ascii(), its \\u escapes.
WRAPPER = """#!/usr/bin/python3
import glob, subprocess, sys
args = sys.argv[1:]
if "--help" in args:
    # A gcov without --json-format (GCC < 9): frozen and native read .gcov text.
    help = subprocess.run(["/usr/bin/gcov", "--help"], capture_output=True, text=True)
    sys.stdout.write("".join(l for l in help.stdout.splitlines(True) if "json" not in l))
    sys.exit(help.returncode)
code = subprocess.call(["/usr/bin/gcov", *args])
CRAFTED = {crafted}
for name in sorted(glob.glob("*.gcov")):
    # Once per report: a CMake session runs gcov once per object directory in one directory.
    if "calc.cpp" in name:
        with open(name, encoding="utf-8") as handle:
            if handle.read().endswith(CRAFTED):
                continue
        with open(name, "a", encoding="utf-8") as handle:
            handle.write(CRAFTED)
sys.exit(code)
"""
# The passing suite over src/calc.cpp (twice and add, both called): 2/2 tests, every line and
# function covered, so a crafted miss is what changes the verdict.
PASSING = {
    "tests/test_fail.cpp": "int twice(int a);\nint main() { return twice(2) == 4 ? 0 : 1; }\n",
}
CPP = cvf.CPP
REQUIRED = CPP + "[engines.test]\ncoverage_required=true\n"
NUMBERS = (
    # Arabic-Indic 101, a miss.
    "    #####:  \u0661\u0660\u0661:  crafted_miss();\n"
    # Fullwidth 102, a miss; a mathematical bold 1 count (Nd, plane 1) on Devanagari 103, covered.
    "    #####:  \uff11\uff10\uff12:  crafted_miss();\n"
    "    \U0001d7cf*:  \u0967\u0966\u0969:  crafted_hit();\n"
    # An Arabic-Indic zero count is not covered; a '-' count is not executable.
    "    \u0660:  104:  crafted_zero();\n"
    "        -:  \u0661\u0660\u0665:  crafted_comment\n"
    # 'taken': Nd 50% and +Nd are taken, a superscript, 4301 digits and Nd zero are not.
    "branch  0 taken \u0665\u0660%\n"
    "branch  1 taken +\u0667\n"
    "branch  2 taken \u00b2%\n"
    "branch  3 taken " + "1" * 4301 + "%\n"
    "branch  4 taken \u0660%\n"
    # 'called': Nd 2 is called, a superscript and 4301 digits are not.
    "function _Zcrafted_nd called \u0662 returned 100% blocks executed 100%\n"
    "function _Zcrafted_sup called \u00b2 returned 0% blocks executed 0%\n"
    "function _Zcrafted_long called " + "1" * 4301 + " returned 0% blocks executed 0%\n"
)
CRAFTED = {
    "numbers": NUMBERS,
    # A miss only in Nd digits: the line verdict a skipped record would hide.
    "nd-miss": "    #####:  \u0661\u0660\u0661:  crafted_miss();\n",
    # int() raises ValueError out of parse_gcov_dir.
    "superscript-line": "    #####:  \u00b2:  crafted();\n",
    "circled-line": "    =====:  \u2460:  crafted();\n",
    "superscript-count": "    \u00b9\u00b2:  105:  crafted();\n",
    "long-line": "    #####:  " + "1" * 4301 + ":  crafted();\n",
    # A miss whose line number is past u64: frozen's int counts it.
    "line-past-u64": "    #####:  18446744073709551616:  crafted_miss();\n",
}


def wrapper(text: str) -> str:
    return WRAPPER.replace("{crafted}", ascii(text))


for _kind, _text in CRAFTED.items():
    cvf.WRAPPED[f"wrapped-gcov-text-{_kind}"] = {"gcov": wrapper(_text)}

CASES = {}
for _kind in CRAFTED:
    CASES[f"test:direct-{_kind}"] = (
        "test-direct/mixed",
        "test",
        CPP,
        f"wrapped-gcov-text-{_kind}",
        PASSING,
    )
for _kind in ("nd-miss", "superscript-line", "long-line"):
    CASES[f"test:direct-required-{_kind}"] = (
        "test-direct/mixed",
        "test",
        REQUIRED,
        f"wrapped-gcov-text-{_kind}",
        PASSING,
    )
    CASES[f"verify:direct-{_kind}"] = (
        "test-direct/mixed",
        "verify",
        CPP,
        f"wrapped-gcov-text-{_kind}",
        PASSING,
    )
CASES["test:direct-required-line-past-u64"] = (
    "test-direct/mixed",
    "test",
    REQUIRED,
    "wrapped-gcov-text-line-past-u64",
    PASSING,
)
CASES["test:cmake-superscript-line"] = (
    "test-cmake/plain",
    "test",
    CPP,
    "wrapped-gcov-text-superscript-line",
)
CASES["test:cmake-nd-miss"] = ("test-cmake/plain", "test", CPP, "wrapped-gcov-text-nd-miss")

_last: dict[str, object] = {}
_run = cvf.subprocess.run


def _recording_run(argv, *args, **kwargs):
    """Keep the frozen CLI's stderr (cvf.run records only its stdout)."""

    result = _run(argv, *args, **kwargs)
    if list(argv[:3]) == [cvf.PYTHON, "-m", "ici"]:
        _last["stderr"] = result.stderr.replace(str(kwargs["cwd"]), "<root>")
    return result


def main() -> None:
    prefixes = sys.argv[1:]
    cvf.subprocess.run = _recording_run
    first = lambda argv: subprocess.check_output(argv, text=True).splitlines()[0]  # noqa: E731
    existing = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    cases = dict(existing.get("cases", {}))
    for name, spec in CASES.items():
        if prefixes and not any(name.startswith(prefix) for prefix in prefixes):
            continue
        _last.clear()
        case = cvf.run(*spec)
        case["stderr"] = _last["stderr"]
        cases[name] = case
        print(name, case["exit_code"], (case["report"] or {}).get("status"), flush=True)
    GOLDEN.write_text(
        json.dumps(
            {
                "oracle": cvf.EXPECTED,
                "gxx": first(["/usr/bin/g++", "--version"]),
                "cmake": first(["/usr/bin/cmake", "--version"]),
                "qmake": None,
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
