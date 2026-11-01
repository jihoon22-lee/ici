"""Development-only frozen `make check` transcript parsing captures; never called by Rust.

Builds parity-fixtures/cpp/test-qmake out of source with qmake6, runs the real
`make check TESTARGS=-xunitxml`, and feeds that transcript plus bounded synthetic transcripts
(wrapper-prefixed and plain invocations, a second binary that never ran, a non-zero exit
without an Error line, DOCTYPE, malformed and concatenated xunit documents, ctest-style
statuses, error/skipped/text-only details, sanitizer reports, an over-long message) to the
frozen `parse_make_check_stdout`, `parse_qtest_xunit`, `_qmake_results` and
`_attach_sanitizer_output`. The project root is replaced by <ROOT>.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORACLE = Path("/tmp/ici-rust-replacement-20261028/oracle")
EXPECTED = "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
assert (
    subprocess.check_output(["git", "-C", str(ORACLE), "rev-parse", "HEAD"], text=True).strip()
    == EXPECTED
)
sys.path.insert(0, str(ORACLE / "src"))

from ici.core._cmake_test_results import (  # noqa: E402
    _attach_sanitizer_output,
    _qmake_results,
    parse_make_check_stdout,
    parse_qtest_xunit,
)

QMAKE = shutil.which("qmake6")
assert QMAKE is not None


def suite(name: str, cases: str) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8" ?>\n<testsuite name="{name}">\n{cases}</testsuite>\n'
    )


PASSING = suite("A", '  <testcase name="one" classname="A"/>\n')
FAILING = suite(
    "B",
    '  <testcase name="bad" classname="B">\n'
    '    <failure type="fail" message="&apos;x&apos; &amp; &lt;y&gt; returned FALSE. ()"/>\n'
    "  </testcase>\n"
    '  <testcase name="err" classname="B"><error message="">trace\n text </error></testcase>\n'
    '  <testcase name="two" classname="B"><failure message="m1"/><error message="m2"/></testcase>\n',
)
STATES = suite(
    "S",
    '  <testcase name="xf" result="xfail"/>\n'
    '  <testcase name="xp" result="xpass"/>\n'
    '  <testcase name="odd" result="weird"/>\n'
    '  <testcase name="bl" result="BLACKLISTED"/>\n'
    '  <testcase name="notrun" status="notrun"/>\n'
    '  <testcase name="unk" status="flaky&apos;s"/>\n'
    '  <testcase name="sk"><skipped/></testcase>\n'
    '  <testcase name="sk2"><skipped>  reason text  </skipped></testcase>\n'
    '  <testcase name="  spaced  " result=" Pass "/>\n',
)
SANITIZED = suite(
    "Z",
    '  <testcase name="leak"><failure message="boom"/>'
    "<system-out>==1==ERROR: LeakSanitizer: detected memory leaks</system-out></testcase>\n"
    '  <testcase name="quiet"><system-err>SUMMARY: UndefinedBehaviorSanitizer: x</system-err>'
    "</testcase>\n",
)
LONG = suite("L", f'  <testcase name="long"><failure message="{"y" * 600}"/></testcase>\n')
SYNTHETIC = {
    "plain-pass": ("./test_a -xunitxml\n" + PASSING, 0),
    "wrapper-fail": (
        "cd /w/.. && /w/target_wrapper.sh  ./test_b -xunitxml\n"
        + FAILING
        + "make[1]: *** [Makefile:322: check] Error 2\nmake: *** [Makefile:207: sub-tests-check] Error 2\n",
        2,
    ),
    "second-never-ran": (
        "./test_a -xunitxml\n"
        + PASSING
        + "./test_b -xunitxml\n"
        + FAILING
        + "make: *** [check] Error 1\n./test_c\n",
        2,
    ),
    "nonzero-no-error-line": ("./test_a\n./test_b\nsome output\n", 1),
    "repeated-name": ("./test_a\n./test_a -x\n(cd sub && ./test_x)\n", 0),
    "doctype": ("./test_a\n<!DOCTYPE x>\n" + FAILING, 2),
    "xunit-only": (PASSING + FAILING + STATES, 1),
    "malformed-block": ("<testsuite name='M'><testcase name='a'></testsuite>\n" + FAILING, 1),
    "unnamed-suite": (
        '<testsuite><testcase name="bare"><failure message="f"/></testcase></testsuite>',
        1,
    ),
    "sanitized-xunit": (SANITIZED, 1),
    "sanitizer-transcript-pass": (
        "./test_a\n" + PASSING + "==9==ERROR: AddressSanitizer: heap-use-after-free on x\n",
        0,
    ),
    "sanitizer-no-case": ("==9==WARNING: ThreadSanitizer: data race\n", 66),
    "long-message": ("./test_l\n" + LONG + "make: *** [check] Error 1\n", 2),
    "crlf-and-formfeed": ("./test_a\r\nmake: *** [x] Error 1\x0c./test_b\r", 2),
    "empty": ("", 0),
}


def view(cases) -> list[dict]:
    return [asdict(case) for case in cases]


def frozen(text: str, returncode: int) -> dict:
    merged = _qmake_results(text, returncode)
    return {
        "make": view(parse_make_check_stdout(text, returncode)),
        "xunit": view(parse_qtest_xunit(text)),
        "qmake": view(merged),
        "attached": view(_attach_sanitizer_output(merged, text)),
    }


with tempfile.TemporaryDirectory(prefix="ici-make-check-") as temporary:
    work = Path(temporary)
    shutil.copytree(ROOT / "parity-fixtures/cpp/test-qmake", work / "src")
    build = work / "build"
    build.mkdir()
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(work), "LC_ALL": "C.UTF-8"}
    subprocess.run([QMAKE, str(work / "src/project.pro")], cwd=build, env=environment, check=True)
    subprocess.run(
        ["make", "--jobs=2"], cwd=build, env=environment, check=True, capture_output=True
    )
    result = subprocess.run(
        ["make", "check", "TESTARGS=-xunitxml"],
        cwd=build,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    # Timestamps and the host name vary per run; the replay feeds this exact transcript.
    real = (result.stdout + result.stderr).replace(str(work), "<ROOT>")
    real_code = result.returncode

transcripts = {"test-qmake": (real, real_code), **SYNTHETIC}
cases = {
    name: {"transcript": text, "returncode": code, "frozen": frozen(text, code)}
    for name, (text, code) in transcripts.items()
}
(ROOT / "tests/rust-parity/cpp/make-check-oracle.json").write_text(
    json.dumps(
        {"oracle": EXPECTED, "qmake": Path(QMAKE).name, "cases": cases},
        indent=1,
        sort_keys=True,
        ensure_ascii=False,
    )
    + "\n",
    encoding="utf-8",
)
print(f"{len(cases)} make check transcripts")
