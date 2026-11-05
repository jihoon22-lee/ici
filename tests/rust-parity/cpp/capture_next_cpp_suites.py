"""Development-only frozen `ici next verify --json` and `ici next plan --json` captures of
per-suite C++ test planning.

Never called by the Rust product. Frozen `application/testing.expand_cpp_tests` turns each
declared suite of the component's linked build into its own task: `<task>.<build>-ctest` for
a CMake tree with a literal `add_test`, `<task>.<build>-<SUBDIRS name>` for each qmake
testlib/testcase `.pro`. A suite that cannot run is a blocked task with its own reason
("ctest is not available", "test suite not built — …", "test binary not built — …").

The suites run fake tools written by the case itself: a `ctest` shell script in the pinned
PATH directory `tools/` inside the project and QTest binaries that are shell scripts printing
QTest text. The same bytes run under the frozen oracle here and under the native replay
(`crates/ici-cli/tests/next_cpp_suites.rs`), so no compiler or real build is needed. Only the
project and work directories are normalized (`{root}`, `{work}`); the whole JSON document is
kept.
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
GOLDEN = ROOT / "tests/rust-parity/cpp/next-cpp-suites-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-suites-capture"
OFF = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in (
        "compile",
        "diagnostics",
        "tidy",
        "coverage",
        "artifact",
        "binary-compat",
        "cycle",
        "line",
        "complexity",
        "cognitive",
        "dup",
        "exception",
    )
)
HEAD = 'schema_version = 1\n[workspace]\nname = "product"\n'
COMPONENT = (
    '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\nbuild = "native"\n'
    'sources = ["src/**", "unit/**", "other/**"]\n'
)


def build(system: str, definition: str) -> str:
    return (
        f'[builds.native]\nsystem = "{system}"\nproject = "{definition}"\ndirectory = "out"\n'
        'variant = "release"\nprepare = "explicit"\n'
    )


CMAKE = HEAD + build("cmake", "CMakeLists.txt") + COMPONENT + OFF
QMAKE = HEAD + build("qmake", "root.pro") + COMPONENT + OFF
CMAKE_FILES = {
    "CMakeLists.txt": "project(p CXX)\nenable_testing()\nadd_test(NAME unit COMMAND unit)\n"
    "add_test(NAME bad COMMAND bad)\nadd_test(NAME off COMMAND off)\n",
    "src/lib.cpp": "int lib() { return 1; }\n",
    "unit/unit.cpp": "int main() { return 0; }\n",
    "unit/bad.cpp": "int main() { return 1; }\n",
}
QMAKE_FILES = {
    "root.pro": "TEMPLATE = subdirs\nSUBDIRS = unit other\n",
    "unit/unit.pro": "QT += testlib\nTARGET=tst_unit\nSOURCES += tst_unit.cpp\n",
    "unit/tst_unit.cpp": "int main() { return 0; }\n",
    "other/other.pro": "CONFIG += testcase\nSOURCES += tst_other.cpp\n",
    "other/tst_other.cpp": "int main() { return 0; }\n",
    "src/lib.cpp": "int lib() { return 1; }\n",
}


def script(lines: list[str], code: int) -> str:
    body = "".join(f"printf '%s\\n' {json.dumps(line)}\n" for line in lines)
    return f"#!/bin/sh\n{body}exit {code}\n"


CTEST_PASS = script(
    [
        "Test project {root}/out",
        "    Start 1: unit",
        "1/2 Test #1: unit .............................   Passed    0.01 sec",
        "    Start 2: bad",
        "2/2 Test #2: bad ..............................   Passed    0.01 sec",
        "",
        "100% tests passed, 0 tests failed out of 2",
    ],
    0,
)
CTEST_FAIL = script(
    [
        "Test project {root}/out",
        "    Start 1: unit",
        "1/3 Test #1: unit .............................   Passed    0.01 sec",
        "    Start 2: bad",
        "2/3 Test #2: bad ..............................***Failed    0.01 sec",
        "    Start 3: off",
        "3/3 Test #3: off ..............................***Not Run (Disabled)   0.00 sec",
        "",
        "50% tests passed, 1 tests failed out of 2",
    ],
    8,
)
CTEST_NONE = script(["Test project {root}/out", "No tests were found!!!"], 0)
QTEST_PASS = script(
    [
        "********* Start testing of TestUnit *********",
        "PASS   : TestUnit::initTestCase()",
        "PASS   : TestUnit::adds()",
        "PASS   : TestUnit::cleanupTestCase()",
        "Totals: 3 passed, 0 failed, 0 skipped, 0 blacklisted, 1ms",
        "********* Finished testing of TestUnit *********",
    ],
    0,
)
QTEST_FAIL = script(
    [
        "********* Start testing of TestOther *********",
        "PASS   : TestOther::initTestCase()",
        "FAIL!  : TestOther::subtracts() Compared values are not the same",
        "   Actual   (1): 1",
        "   Expected (2): 2",
        "   Loc: [other/tst_other.cpp(7)]",
        "SKIP   : TestOther::later() not yet",
        "PASS   : TestOther::cleanupTestCase()",
        "Totals: 2 passed, 1 failed, 1 skipped, 0 blacklisted, 1ms",
        "********* Finished testing of TestOther *********",
    ],
    1,
)
QTEST_EMPTY = script(["Totals: 0 passed, 0 failed, 0 skipped, 0 blacklisted, 0ms"], 0)
TESTFILE = {"out/CTestTestfile.cmake": "add_test(unit unit)\n"}

# Each case: config, project files, executable project files, fake tools in the PATH dir.
CASES = {
    "ctest-missing": {"config": CMAKE, "files": CMAKE_FILES, "executables": {}, "tools": {}},
    "ctest-testfile-missing": {
        "config": CMAKE,
        "files": CMAKE_FILES,
        "executables": {},
        "tools": {"ctest": CTEST_PASS},
    },
    "ctest-pass": {
        "config": CMAKE,
        "files": {**CMAKE_FILES, **TESTFILE},
        "executables": {},
        "tools": {"ctest": CTEST_PASS},
    },
    "ctest-fail": {
        "config": CMAKE,
        "files": {**CMAKE_FILES, **TESTFILE},
        "executables": {},
        "tools": {"ctest": CTEST_FAIL},
    },
    "ctest-no-tests": {
        "config": CMAKE,
        "files": {**CMAKE_FILES, **TESTFILE},
        "executables": {},
        "tools": {"ctest": CTEST_NONE},
    },
    "qtest-absent": {"config": QMAKE, "files": QMAKE_FILES, "executables": {}, "tools": {}},
    "qtest-mixed": {
        "config": QMAKE,
        "files": QMAKE_FILES,
        "executables": {"out/tst_unit": QTEST_PASS},
        "tools": {},
    },
    "qtest-built": {
        "config": QMAKE,
        "files": QMAKE_FILES,
        "executables": {"out/tst_unit": QTEST_PASS, "out/other": QTEST_FAIL},
        "tools": {},
    },
    "qtest-empty": {
        "config": QMAKE,
        "files": QMAKE_FILES,
        "executables": {"out/tst_unit": QTEST_EMPTY, "out/other": QTEST_PASS},
        "tools": {},
    },
}


def write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def run(case: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        # Inside the workspace, so frozen `process_fingerprint` relocates the tool path to
        # `${workspace}/tools/ctest` and the replay can compare finding fingerprints.
        bin_dir = project / "tools"
        bin_dir.mkdir(parents=True)
        for name, text in case["files"].items():
            write(project / name, text)
        for name, text in case["executables"].items():
            write(project / name, text.replace("{root}", str(project)), executable=True)
        for name, text in case["tools"].items():
            write(bin_dir / name, text.replace("{root}", str(project)), executable=True)
        write(project / "ici.toml", case["config"])
        captured = {}
        for command in ("verify", "plan"):
            result = subprocess.run(
                [PYTHON, "-m", "ici", "next", command, "--json"],
                cwd=project,
                env={
                    "PATH": str(bin_dir),
                    "HOME": str(WORK),
                    "XDG_CACHE_HOME": str(WORK / "cache"),
                    "PYTHONPATH": str(ORACLE / "src"),
                    "PYTHONHASHSEED": "0",
                },
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
            text = result.stdout.replace(str(project), "{root}").replace(str(WORK), "{work}")
            captured[command] = {"exit_code": result.returncode, "document": json.loads(text)}
        return captured
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


oracle = {
    "oracle": EXPECTED,
    "cases": {
        name: {**case, "frozen": captured["verify"], "frozen_plan": captured["plan"]}
        for name, case in CASES.items()
        for captured in (run(case),)
    },
}
GOLDEN.write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(CASES)} next C++ suite cases")
