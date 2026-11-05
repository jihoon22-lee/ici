"""Development-only frozen `ici next plan --json`/`next verify --json` captures of next-path
C++ coverage over declared suites.

Never called by the Rust product. Frozen `application/gates.gate_cpp` makes `cpp.coverage`
need every declared suite task (`cpp.test.<suite>`), and `application/testing.plan_cpp_coverage`
plans one `gcov --json-format -b -p <notes…>` read in `.ici/cache/gcov/<component>` after them
(`adapters/providers/gcov.py` parses every `*.gcov.json.gz` it wrote). Without notes, gcov or
`cpp.test` the check is blocked with frozen's reason.

The suites are the fake QTest/ctest shell scripts of `capture_next_cpp_suites.py`; `gcov` is a
fake tool too: a shell script that writes a fixed gzip report with the `printf` builtin, so the
same bytes run under the frozen oracle here and under the native replay
(`crates/ici-cli/tests/next_cpp_coverage.rs`). Only the project and work directories are
normalized (`{root}`, `{work}`); whole JSON documents are kept.
"""

import gzip
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/next-cpp-coverage-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-coverage-capture"
OFF = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in (
        "compile",
        "diagnostics",
        "tidy",
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
QMAKE_NO_TEST = QMAKE + '[checks."cpp.test"]\nenabled = false\n'
NOTES = {"out/lib.gcno": "notes\n"}
CMAKE_FILES = {
    "CMakeLists.txt": "project(p CXX)\nenable_testing()\nadd_test(NAME unit COMMAND unit)\n",
    "src/lib.cpp": "int lib(int x) {\n  if (x > 0)\n    return 1;\n  return 0;\n}\n",
    "unit/unit.cpp": "int main() { return 0; }\n",
    "out/CTestTestfile.cmake": "add_test(unit unit)\n",
}
NO_SUITE_FILES = {
    "CMakeLists.txt": "project(p CXX)\nadd_library(lib src/lib.cpp)\n",
    "src/lib.cpp": CMAKE_FILES["src/lib.cpp"],
}
MAKE = HEAD + build("make", "Makefile") + COMPONENT + OFF
QMAKE_FILES = {
    "root.pro": "TEMPLATE = subdirs\nSUBDIRS = unit other\n",
    "unit/unit.pro": "QT += testlib\nTARGET=tst_unit\nSOURCES += tst_unit.cpp\n",
    "unit/tst_unit.cpp": "int main() { return 0; }\n",
    "other/other.pro": "CONFIG += testcase\nSOURCES += tst_other.cpp\n",
    "other/tst_other.cpp": "int main() { return 0; }\n",
    "src/lib.cpp": "int lib(int x) {\n  if (x > 0)\n    return 1;\n  return 0;\n}\n",
}


def script(lines: list[str], code: int, stderr: tuple[str, ...] = ()) -> str:
    body = "".join(f"printf '%s\\n' {json.dumps(line)}\n" for line in lines)
    body += "".join(f"printf '%s\\n' {json.dumps(line)} >&2\n" for line in stderr)
    return f"#!/bin/sh\n{body}exit {code}\n"


def qtest(name: str, failed: bool) -> str:
    verdict = (
        [f"FAIL!  : {name}::adds() Compared values are not the same"]
        if failed
        else [f"PASS   : {name}::adds()"]
    )
    return script(
        [
            f"********* Start testing of {name} *********",
            f"PASS   : {name}::initTestCase()",
            *verdict,
            f"PASS   : {name}::cleanupTestCase()",
            f"Totals: {2 if failed else 3} passed, {1 if failed else 0} failed, 0 skipped, "
            "0 blacklisted, 1ms",
            f"********* Finished testing of {name} *********",
        ],
        1 if failed else 0,
    )


CTEST_PASS = script(
    [
        "Test project {root}/out",
        "    Start 1: unit",
        "1/1 Test #1: unit .............................   Passed    0.01 sec",
        "",
        "100% tests passed, 0 tests failed out of 1",
    ],
    0,
)
# gcov 15 format-2 report of src/lib.cpp: 4 of 5 lines, 1 of 2 branches, 1 of 1 function.
REPORT = {
    "format_version": "2",
    "gcc_version": "15.2.0",
    "current_working_directory": "{root}/out",
    "data_file": "lib.gcno",
    "files": [
        {
            "file": "../src/lib.cpp",
            "functions": [
                {
                    "name": "_Z3libi",
                    "demangled_name": "lib(int)",
                    "start_line": 1,
                    "start_column": 5,
                    "end_line": 5,
                    "end_column": 1,
                    "blocks": 4,
                    "blocks_executed": 3,
                    "execution_count": 1,
                }
            ],
            "lines": [
                {
                    "line_number": number,
                    "function_name": "_Z3libi",
                    "count": count,
                    "unexecuted_block": count == 0,
                    "block_ids": [],
                    "branches": (
                        [
                            {
                                "count": 1,
                                "throw": False,
                                "fallthrough": True,
                                "source_block_id": 2,
                                "destination_block_id": 3,
                            },
                            {
                                "count": 0,
                                "throw": False,
                                "fallthrough": False,
                                "source_block_id": 2,
                                "destination_block_id": 4,
                            },
                        ]
                        if number == 2
                        else []
                    ),
                    "calls": [],
                    "conditions": [],
                }
                for number, count in ((1, 1), (2, 1), (3, 1), (4, 0), (5, 1))
            ],
        }
    ],
}


def gcov(*, report: bool = True, stderr: str = "") -> str:
    """A fake gcov writing the fixed report (gzip, mtime 0) with the printf builtin."""

    body = "#!/bin/sh\n"
    if report:
        data = gzip.compress(json.dumps(REPORT).encode(), mtime=0)
        octal = "".join(f"\\{byte:03o}" for byte in data)
        body += f"printf '{octal}' > lib.gcov.json.gz\n"
    if stderr:
        body += f"printf '%s\\n' {json.dumps(stderr)} >&2\n"
    return body + "exit 0\n"


BUILT = {"out/tst_unit": qtest("TestUnit", False), "out/other": qtest("TestOther", False)}

# Each case: config, project files, executable project files, fake tools in the PATH dir.
CASES = {
    "qmake-pass": {
        "config": QMAKE,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": BUILT,
        "tools": {"gcov": gcov()},
    },
    "qmake-failed-suite": {
        "config": QMAKE,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": {**BUILT, "out/other": qtest("TestOther", True)},
        "tools": {"gcov": gcov()},
    },
    "qmake-blocked-suite": {
        "config": QMAKE,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": {"out/tst_unit": BUILT["out/tst_unit"]},
        "tools": {"gcov": gcov()},
    },
    "qmake-gcov-missing": {
        "config": QMAKE,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": BUILT,
        "tools": {},
    },
    "qmake-no-report": {
        "config": QMAKE,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": BUILT,
        "tools": {"gcov": gcov(report=False)},
    },
    "qmake-stale": {
        "config": QMAKE,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": BUILT,
        "tools": {"gcov": gcov(stderr="lib.gcda:stamp mismatch with notes file")},
    },
    "qmake-no-notes": {
        "config": QMAKE,
        "files": QMAKE_FILES,
        "executables": BUILT,
        "tools": {"gcov": gcov()},
    },
    "qmake-test-disabled": {
        "config": QMAKE_NO_TEST,
        "files": {**QMAKE_FILES, **NOTES},
        "executables": BUILT,
        "tools": {"gcov": gcov()},
    },
    "cmake-pass": {
        "config": CMAKE,
        "files": {**CMAKE_FILES, **NOTES},
        "executables": {},
        "tools": {"gcov": gcov(), "ctest": CTEST_PASS},
    },
    # A linked build that declares no suite (no add_test; a Make build has no definition
    # frozen reads): `cpp.test` is blocked ("no declared test suite") and `cpp.coverage` keeps
    # its plain need on it (gates.gate_cpp rewrites needs only for declared suites).
    "cmake-no-suite": {
        "config": CMAKE,
        "files": {**NO_SUITE_FILES, **NOTES},
        "executables": {},
        "tools": {"gcov": gcov(), "ctest": CTEST_PASS},
    },
    "cmake-no-suite-no-notes": {
        "config": CMAKE,
        "files": NO_SUITE_FILES,
        "executables": {},
        "tools": {"gcov": gcov(), "ctest": CTEST_PASS},
    },
    "cmake-no-suite-gcov-missing": {
        "config": CMAKE,
        "files": {**NO_SUITE_FILES, **NOTES},
        "executables": {},
        "tools": {"ctest": CTEST_PASS},
    },
    "cmake-no-suite-test-disabled": {
        "config": CMAKE + '[checks."cpp.test"]\nenabled = false\n',
        "files": {**NO_SUITE_FILES, **NOTES},
        "executables": {},
        "tools": {"gcov": gcov(), "ctest": CTEST_PASS},
    },
    "make-no-suite": {
        "config": MAKE,
        "files": {
            "Makefile": "all:\n\ttrue\n",
            "src/lib.cpp": CMAKE_FILES["src/lib.cpp"],
            **NOTES,
        },
        "executables": {},
        "tools": {"gcov": gcov()},
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
        bin_dir = project / "tools"
        bin_dir.mkdir(parents=True)
        for name, text in case["files"].items():
            write(project / name, text)
        for name, text in {**case["executables"]}.items():
            write(project / name, text.replace("{root}", str(project)), executable=True)
        for name, text in case["tools"].items():
            write(bin_dir / name, text.replace("{root}", str(project)), executable=True)
        write(project / "ici.toml", case["config"])
        captured = {}
        for command in ("plan", "verify"):
            result = subprocess.run(
                [PYTHON, "-m", "ici", "next", command, "--json"],
                cwd=project,
                env={
                    "PATH": str(bin_dir),
                    "HOME": str(WORK),
                    "XDG_CACHE_HOME": str(WORK / "cache"),
                    # Git discovery stops above the project, as in the native replay.
                    "GIT_CEILING_DIRECTORIES": str(project.parent),
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
    "cases": {name: {**case, "frozen": run(case)} for name, case in CASES.items()},
}
GOLDEN.write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(CASES)} next C++ coverage cases")
