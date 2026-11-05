"""Development-only frozen `ici next plan --json`/`next verify --json` captures of next-path
C++ sanitizer suites (`cpp.sanitize`, `cpp.tsan`).

Never called by the Rust product. Frozen `application/testing.expand_cpp_sanitizer` plans one
task per declared suite of the component's linked build whose `variant` is `sanitize` /
`thread-sanitize` (`<task>.<build>-ctest`, `<task>.<build>-<SUBDIRS name>`), gated in frozen
order on: a linked build, a variant build, a suite under it, `ctest` and the generated
`CTestTestfile.cmake` (CMake), the executables its `add_test` entries name, and the runtime
markers `workspace/instrumentation.sanitizer_marked` finds in each binary. A runnable suite is
an `adapters/providers/sanitize.py` task: the ctest run (`--test-dir <build> --output-on-failure`)
or the QTest binary, under the variant's `ASAN_OPTIONS`/`UBSAN_OPTIONS`/`TSAN_OPTIONS` overlay,
parsed into sanitizer findings, a suite failure, a normalization failure or a clean count.

The suites run bytes written by the case itself: a `ctest` shell script in the pinned PATH
directory `tools/` inside the project, and test binaries that are tiny static x86-64 ELF files
(`elf_bytes()` below: `write` syscalls of fixed stdout/stderr text, then `exit`) whose data
carries the runtime marker strings frozen scans for. The golden keeps every binary's spec
(text, exit code, markers) and one generated sample as hex that pins the generator, so the
native replay (`crates/ici-cli/tests/next_cpp_sanitizer.rs`) writes and runs the same bytes
with no compiler, CMake, Qt or sanitizer runtime. Only the project and work directories are
normalized (`{root}`, `{work}`); whole JSON documents are kept.
"""

import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
PYTHON = "/home/jihoon/projects/ici/.venv/bin/python"
GOLDEN = ROOT / "tests/rust-parity/cpp/next-cpp-sanitizer-oracle.json"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
WORK = Path(tempfile.gettempdir()) / "ici-cpp-next-sanitizer-capture"
OFF = "".join(
    f'[checks."cpp.{check}"]\nenabled = false\n'
    for check in (
        "compile",
        "diagnostics",
        "tidy",
        "test",
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
HEAD = 'schema_version = 1\n[workspace]\nname = "product"\nprofile = "deep"\n'
COMPONENT = (
    '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\nbuild = "native"\n'
    'sources = ["src/**", "unit/**", "other/**"]\n'
)
NO_BUILD = (
    HEAD
    + '[[components]]\nid = "app"\nroot = "."\nlanguages = ["cpp"]\nsources = ["src/**"]\n'
    + OFF
)
ASAN = (b"libasan.so.8", b"__asan_init")
UBSAN = (b"libubsan.so.1", b"__ubsan_handle_add_overflow")
TSAN = (b"libtsan.so.2", b"__tsan_init")


def elf(stdout: str = "", stderr: str = "", code: int = 0, markers: tuple = ()) -> dict:
    """The spec of one test binary (see `elf_bytes`); `{root}` is substituted at write time."""

    return {
        "stdout": stdout,
        "stderr": stderr,
        "code": code,
        "markers": [m.decode() for m in markers],
    }


def elf_bytes(spec: dict, root: str) -> bytes:
    """A static x86-64 ELF executable: write `stdout` to fd 1, `stderr` to fd 2, exit `code`.

    One PT_LOAD segment at 0x400000 maps the whole file; the code is `mov eax, 1; mov edi, fd;
    movabs rsi, <data>; mov edx, <len>; syscall` per non-empty stream and `mov eax, 60;
    mov edi, code; syscall`. The NUL-separated marker strings follow the text, where frozen's
    byte scan finds them. An `empty` spec is a zero-byte file.
    """

    if spec.get("empty"):
        return b""
    base, header = 0x400000, 64 + 56
    streams = [
        (fd, spec[key].replace("{root}", root).encode())
        for fd, key in ((1, "stdout"), (2, "stderr"))
    ]
    code_size = sum(27 for _, data in streams if data) + 12
    text = b""
    payload = b""
    for fd, data in streams:
        if not data:
            continue
        address = base + header + code_size + len(payload)
        text += b"\xb8\x01\x00\x00\x00" + b"\xbf" + struct.pack("<I", fd)
        text += b"\x48\xbe" + struct.pack("<Q", address)
        text += b"\xba" + struct.pack("<I", len(data)) + b"\x0f\x05"
        payload += data
    text += b"\xb8\x3c\x00\x00\x00" + b"\xbf" + struct.pack("<I", spec["code"]) + b"\x0f\x05"
    assert len(text) == code_size
    markers = [marker.encode() for marker in spec["markers"]]
    payload += b"\x00" + b"\x00".join(markers) + (b"\x00" if markers else b"")
    size = header + len(text) + len(payload)
    ident = b"\x7fELF\x02\x01\x01" + b"\x00" * 9
    elf_header = ident + struct.pack(
        "<HHIQQQIHHHHHH", 2, 0x3E, 1, base + header, 64, 0, 0, 64, 56, 1, 0, 0, 0
    )
    program = struct.pack("<IIQQQQQQ", 1, 5, 0, base, base, size, size, 0x1000)
    return elf_header + program + text + payload


def build(system: str, definition: str, variant: str) -> str:
    return (
        f'[builds.native]\nsystem = "{system}"\nproject = "{definition}"\ndirectory = "out"\n'
        f'variant = "{variant}"\nprepare = "explicit"\n'
    )


def config(system: str, variant: str) -> str:
    definition = "CMakeLists.txt" if system == "cmake" else "root.pro"
    return HEAD + build(system, definition, variant) + COMPONENT + OFF


SOURCE = "int lib(int *p) {\n  int v = *p;\n  delete p;\n  return v + *p;\n}\n"
CMAKE_FILES = {
    "CMakeLists.txt": "project(p CXX)\nenable_testing()\nadd_test(NAME unit COMMAND unit)\n",
    "src/lib.cpp": SOURCE,
    "unit/unit.cpp": "int main() { return 0; }\n",
}
NO_SUITE_FILES = {
    "CMakeLists.txt": "project(p CXX)\nadd_library(lib src/lib.cpp)\n",
    "src/lib.cpp": SOURCE,
}
# CMake < 3.19 policy spelling; `BRACKET` is the CMP0110 spelling newer policies generate.
TESTFILE = {"out/CTestTestfile.cmake": 'add_test(unit "{root}/out/unit")\n'}
BRACKET = {"out/CTestTestfile.cmake": 'add_test([=[unit]=] "{root}/out/unit")\n'}
QMAKE_FILES = {
    "root.pro": "TEMPLATE = subdirs\nSUBDIRS = unit other\n",
    "unit/unit.pro": "QT += testlib\nTARGET=tst_unit\nSOURCES += tst_unit.cpp\n",
    "unit/tst_unit.cpp": "int main() { return 0; }\n",
    "other/other.pro": "CONFIG += testcase\nSOURCES += tst_other.cpp\n",
    "other/tst_other.cpp": "int main() { return 0; }\n",
    "src/lib.cpp": SOURCE,
}


def script(lines: list[str], code: int, stderr: tuple[str, ...] = ()) -> str:
    body = "".join(f"printf '%s\\n' {json.dumps(line)}\n" for line in lines)
    body += "".join(f"printf '%s\\n' {json.dumps(line)} >&2\n" for line in stderr)
    return f"#!/bin/sh\n{body}exit {code}\n"


CTEST_HEAD = ["Test project {root}/out", "    Start 1: unit"]
CTEST_PASS = [
    *CTEST_HEAD,
    "1/1 Test #1: unit .............................   Passed    0.01 sec",
    "",
    "100% tests passed, 0 tests failed out of 1",
]
CTEST_FAILED = [
    *CTEST_HEAD,
    "1/1 Test #1: unit .............................***Failed    0.01 sec",
]
ASAN_REPORT = [
    "=================================================================",
    "==4242==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010 "
    "at pc 0x55d0 bp 0x7ffd sp 0x7ffc",
    "READ of size 4 at 0x602000000010 thread T0",
    "    #0 0x55d0 in lib(int*) {root}/src/lib.cpp:4:14",
    "    #1 0x55e0 in main {root}/unit/unit.cpp:1:14",
    "    #2 0x7f00 in __libc_start_main /usr/lib/libc.so.6",
    "SUMMARY: AddressSanitizer: heap-use-after-free {root}/src/lib.cpp:4:14 in lib(int*)",
    "==4242==ABORTING",
]
EXTERNAL_REPORT = [
    "==4242==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x602000000020",
    "    #0 0x7f00 in memcpy /usr/include/c++/15/bits/basic_string.h:12:3",
    "SUMMARY: AddressSanitizer: heap-buffer-overflow /usr/include/c++/15/bits/basic_string.h:12:3",
]
UNLOCATED_REPORT = [
    "==4242==ERROR: LeakSanitizer: detected memory leaks",
    "Direct leak of 4 byte(s) in 1 object(s) allocated from:",
    "    #0 0x7f00 in operator new(unsigned long) (/usr/lib/libasan.so.8+0xf0)",
    "SUMMARY: AddressSanitizer: 4 byte(s) leaked in 1 allocation(s).",
]
UBSAN_REPORT = ["{root}/src/lib.cpp:2:11: runtime error: signed integer overflow: 1 + 2147483647"]
TSAN_REPORT = [
    "==================",
    "WARNING: ThreadSanitizer: data race (pid=4242)",
    "  Write of size 4 at 0x7b04 by thread T1:",
    "    #0 lib(int*) {root}/src/lib.cpp:2:7 (unit+0x12)",
    "  Previous read of size 4 at 0x7b04 by main thread:",
    "    #0 main {root}/unit/unit.cpp:1:14 (unit+0x34)",
    "SUMMARY: ThreadSanitizer: data race {root}/src/lib.cpp:2:7 in lib(int*)",
    "==================",
]
DEEP_STACK = [
    "==4242==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd",
    *(f"    #{index} 0x55d0 in frame{index} {{root}}/src/lib.cpp:2:7" for index in range(33)),
]
# A frame in a project file outside the component's sources, a line past the sanitizer bound
# beside a valid frame, and a TSan summary with doubled spaces.
OUTSIDE_REPORT = [
    "==4242==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010",
    "    #0 0x55d0 in helper() {root}/vendor/helper.cpp:2:3",
    "    #1 0x55e0 in lib(int*) {root}/src/lib.cpp:4:14",
]
OVERFLOW_REPORT = [
    "==4242==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010",
    "    #0 0x55d0 in lib(int*) {root}/src/lib.cpp:99999999999:1",
    "    #1 0x55e0 in main {root}/unit/unit.cpp:1:14",
]
SPACED_TSAN = [
    "WARNING: ThreadSanitizer: data  race (pid=4242)",
    "    #0 lib(int*) {root}/src/lib.cpp:2:7 (unit+0x12)",
]
MARKED = elf(markers=ASAN)
TSAN_OFF = '[checks."cpp.tsan"]\nenabled = false\n'
THREAD_MARKED = elf(markers=TSAN)


#: `"$ASAN_OPTIONS|$UBSAN_OPTIONS|$TSAN_OPTIONS"` the variant's run must see (nothing inherited).
OVERLAY = {
    "sanitize": "detect_leaks=1|halt_on_error=1|",
    "thread-sanitize": "||halt_on_error=0",
    "release": "",
}


def ctest(lines: list[str], code: int, stderr: tuple[str, ...] = ()) -> str:
    """A fake ctest that exits 97 (no sanitizer marker, a suite failure) unless the run carries
    exactly the variant's option overlay (`{overlay}` is filled in by `cmake`)."""

    return script(lines, code, stderr).replace(
        "#!/bin/sh\n",
        '#!/bin/sh\n[ "$ASAN_OPTIONS|$UBSAN_OPTIONS|$TSAN_OPTIONS" = "{overlay}" ] || exit 97\n',
        1,
    )


def cmake(variant: str, tool: str | None, binary: str | None, *, testfile: bool = True) -> dict:
    if tool is not None:
        tool = tool.replace("{overlay}", OVERLAY[variant])
    return {
        "config": config("cmake", variant),
        "files": {**CMAKE_FILES, **(TESTFILE if testfile else {})},
        "binaries": {"out/unit": binary} if binary is not None else {},
        "executables": {},
        "tools": {"ctest": tool} if tool is not None else {},
    }


NESTED_REPORT = [line.replace("{root}/", "{root}/app/") for line in ASAN_REPORT]


def nested(tool: str) -> dict:
    """A CMake suite of the component rooted at `app` (build tree `out` at the workspace root)."""

    return {
        "config": HEAD
        + build("cmake", "app/CMakeLists.txt", "sanitize")
        + '[[components]]\nid = "app"\nroot = "app"\nlanguages = ["cpp"]\nbuild = "native"\n'
        'sources = ["src/**", "unit/**"]\n' + OFF + TSAN_OFF,
        "files": {
            **{f"app/{name}": text for name, text in CMAKE_FILES.items()},
            **TESTFILE,
        },
        "binaries": {"out/unit": MARKED},
        "executables": {},
        "tools": {"ctest": tool.replace("{overlay}", OVERLAY["sanitize"])},
    }


def qtest(name: str, stderr: list[str], code: int, markers: tuple = ASAN) -> str:
    return elf(
        stdout=f"********* Start testing of {name} *********\n"
        f"Totals: 1 passed, 0 failed, 0 skipped, 0 blacklisted, 1ms\n",
        stderr="".join(f"{line}\n" for line in stderr),
        code=code,
        markers=markers,
    )


def qmake(variant: str, binaries: dict, executables: dict | None = None) -> dict:
    return {
        "config": config("qmake", variant),
        "files": QMAKE_FILES,
        "binaries": binaries,
        "executables": executables or {},
        "tools": {},
    }


CASES = {
    # Frozen gate order: linked build, variant build, suite under it.
    "no-build": {
        "config": NO_BUILD,
        "files": {"src/lib.cpp": SOURCE},
        "binaries": {},
        "executables": {},
        "tools": {},
    },
    "release-build": cmake("release", ctest(CTEST_PASS, 0), MARKED),
    "no-suite": {
        "config": config("cmake", "sanitize"),
        "files": NO_SUITE_FILES,
        "binaries": {},
        "executables": {},
        "tools": {"ctest": ctest(CTEST_PASS, 0).replace("{overlay}", OVERLAY["sanitize"])},
    },
    # CMake suites: ctest, CTestTestfile, named executables, markers.
    "ctest-missing": cmake("sanitize", None, MARKED),
    "testfile-missing": cmake("sanitize", ctest(CTEST_PASS, 0), MARKED, testfile=False),
    "no-binaries": cmake("sanitize", ctest(CTEST_PASS, 0), None),
    "bracket-testfile": {
        **cmake("sanitize", ctest(CTEST_PASS, 0), MARKED, testfile=False),
        "files": {**CMAKE_FILES, **BRACKET},
    },
    "subdirectory-testfile": {
        **cmake("sanitize", ctest(CTEST_PASS, 0), None, testfile=False),
        "files": {
            **CMAKE_FILES,
            "out/CTestTestfile.cmake": 'subdirs("unit")\n',
            "out/unit/CTestTestfile.cmake": 'add_test(unit "unit")\nadd_test(gone "gone")\n',
        },
        "binaries": {"out/unit/unit": MARKED, "out/other": MARKED},
    },
    "unmarked": cmake("sanitize", ctest(CTEST_PASS, 0), elf()),
    "tsan-marks-for-asan": cmake("sanitize", ctest(CTEST_PASS, 0), THREAD_MARKED),
    "ctest-clean": cmake("sanitize", ctest(CTEST_PASS, 0), MARKED),
    "ctest-ubsan-marked-clean": cmake("sanitize", ctest(CTEST_PASS, 0), elf(markers=UBSAN)),
    "ctest-asan": cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(ASAN_REPORT)), MARKED),
    "ctest-asan-stdout": cmake("sanitize", ctest([*CTEST_FAILED, *ASAN_REPORT], 8), MARKED),
    "ctest-external": cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(EXTERNAL_REPORT)), MARKED),
    "ctest-unlocated": cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(UNLOCATED_REPORT)), MARKED),
    "ctest-ubsan": cmake("sanitize", ctest(CTEST_PASS, 0, tuple(UBSAN_REPORT)), MARKED),
    "ctest-outside-sources": {
        **cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(OUTSIDE_REPORT)), MARKED),
        "files": {
            **CMAKE_FILES,
            **TESTFILE,
            "vendor/helper.cpp": "void helper() {\n  int x = 0;\n}\n",
        },
    },
    "ctest-line-overflow": cmake(
        "sanitize", ctest(CTEST_FAILED, 8, tuple(OVERFLOW_REPORT)), MARKED
    ),
    "tsan-ctest-spaced": cmake(
        "thread-sanitize", ctest(CTEST_PASS, 0, tuple(SPACED_TSAN)), THREAD_MARKED
    ),
    "ctest-deep-stack": cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(DEEP_STACK)), MARKED),
    "ctest-suite-failure": cmake("sanitize", ctest(CTEST_FAILED, 8), MARKED),
    "ctest-signal": cmake(
        "sanitize", ctest(CTEST_HEAD, 0).replace("exit 0\n", "kill -ABRT $$\n"), MARKED
    ),
    # QTest suites: built binary, markers (absent, non-ELF, empty), runs.
    "qtest-absent": qmake("sanitize", {}),
    "qtest-script": qmake(
        "sanitize",
        {"out/other": qtest("TestOther", [], 0)},
        {"out/tst_unit": script(["PASS   : TestUnit::adds()"], 0)},
    ),
    "qtest-empty": qmake(
        "sanitize", {"out/tst_unit": {"empty": True}, "out/other": qtest("TestOther", [], 0)}
    ),
    "qtest-mixed": qmake("sanitize", {"out/tst_unit": qtest("TestUnit", ASAN_REPORT, 1)}),
    "qtest-clean": qmake(
        "sanitize",
        {"out/tst_unit": qtest("TestUnit", [], 0), "out/other": qtest("TestOther", [], 0)},
    ),
    "qtest-findings": qmake(
        "sanitize",
        {
            "out/tst_unit": qtest("TestUnit", ASAN_REPORT, 1),
            "out/other": qtest("TestOther", UBSAN_REPORT, 0),
        },
    ),
    # One sanitizer check selected: the gate reads the suite verdicts alone.
    "asan-only-findings": {
        **cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(ASAN_REPORT)), MARKED),
        "config": config("cmake", "sanitize") + TSAN_OFF,
    },
    "asan-only-clean": {
        **qmake(
            "sanitize",
            {"out/tst_unit": qtest("TestUnit", [], 0), "out/other": qtest("TestOther", [], 0)},
        ),
        "config": config("qmake", "sanitize") + TSAN_OFF,
    },
    "asan-only-mixed": {
        **qmake("sanitize", {"out/tst_unit": qtest("TestUnit", ASAN_REPORT, 1)}),
        "config": config("qmake", "sanitize") + TSAN_OFF,
    },
    "asan-only-suite-failure": {
        **cmake("sanitize", ctest(CTEST_FAILED, 8), MARKED),
        "config": config("cmake", "sanitize") + TSAN_OFF,
    },
    "asan-only-unnormalized": {
        **cmake("sanitize", ctest(CTEST_FAILED, 8, tuple(DEEP_STACK)), MARKED),
        "config": config("cmake", "sanitize") + TSAN_OFF,
    },
    # Bounds and refusals: output past the executor's 1,000,000 characters, a NUL byte in a
    # report, more than 64 diagnostics.
    "ctest-truncated": cmake(
        "sanitize",
        ctest(CTEST_HEAD, 0).replace(
            "exit 0\n",
            "i=0\nwhile [ $i -lt 1100 ]; do printf '%01000d\\n' 0; i=$((i+1)); done\nexit 0\n",
        ),
        MARKED,
    ),
    "ctest-nul": cmake(
        "sanitize",
        ctest(CTEST_FAILED, 8).replace(
            "exit 8\n",
            "printf '==1==ERROR: AddressSanitizer: heap-use-after-free on \\000x\\n' >&2\nexit 8\n",
        ),
        MARKED,
    ),
    "ctest-many-diagnostics": cmake(
        "sanitize", ctest(CTEST_PASS, 0, tuple(UBSAN_REPORT * 65)), MARKED
    ),
    # A component below the workspace root: frozen anchors a location-less finding at the
    # component directory and fingerprints the `${workspace}/app` cwd.
    "nested-component-findings": nested(ctest(CTEST_FAILED, 8, tuple(NESTED_REPORT))),
    "nested-component-suite-failure": nested(ctest(CTEST_FAILED, 8, tuple(UNLOCATED_REPORT))),
    "nested-component-exit": nested(ctest(CTEST_FAILED, 8)),
    # ThreadSanitizer variant.
    "tsan-ctest-race": cmake(
        "thread-sanitize", ctest(CTEST_PASS, 0, tuple(TSAN_REPORT)), THREAD_MARKED
    ),
    "tsan-ctest-clean": cmake("thread-sanitize", ctest(CTEST_PASS, 0), THREAD_MARKED),
    "tsan-unmarked": cmake("thread-sanitize", ctest(CTEST_PASS, 0), MARKED),
    "tsan-qtest": qmake(
        "thread-sanitize",
        {
            "out/tst_unit": qtest("TestUnit", TSAN_REPORT, 66, TSAN),
            "out/other": qtest("TestOther", [], 0, TSAN),
        },
    ),
}


def write(path: Path, data: bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if executable:
        path.chmod(0o755)


def run(case: dict) -> dict:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir()
    try:
        project = WORK / "project"
        bin_dir = project / "tools"
        bin_dir.mkdir(parents=True)
        root = str(project)
        for name, text in case["files"].items():
            write(project / name, text.replace("{root}", root).encode())
        for name, text in case["executables"].items():
            write(project / name, text.replace("{root}", root).encode(), executable=True)
        for name, spec in case["binaries"].items():
            write(project / name, elf_bytes(spec, root), executable=True)
        for name, text in case["tools"].items():
            write(bin_dir / name, text.replace("{root}", root).encode(), executable=True)
        write(project / "ici.toml", case["config"].encode())
        captured = {}
        for command in ("plan", "verify"):
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
            text = result.stdout.replace(root, "{root}").replace(str(WORK), "{work}")
            captured[command] = {"exit_code": result.returncode, "document": json.loads(text)}
        return captured
    finally:
        shutil.rmtree(WORK, ignore_errors=True)


SAMPLE = elf("out\n", "err\n", 3, ASAN)
oracle = {
    "oracle": EXPECTED,
    # The generator's bytes for one root-free spec, so the replay's generator is pinned to it.
    "elf_sample": {"spec": SAMPLE, "hex": elf_bytes(SAMPLE, "").hex()},
    "cases": {name: {**case, "frozen": run(case)} for name, case in CASES.items()},
}
GOLDEN.write_text(
    json.dumps(oracle, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(CASES)} next C++ sanitizer cases")
