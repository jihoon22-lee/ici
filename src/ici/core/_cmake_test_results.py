"""Bounded test-result parsing for CTest, QtTest, and qmake ``make check``."""

import re
from dataclasses import dataclass, replace
from pathlib import Path
from xml.etree import ElementTree

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError

__all__ = [
    "TestCaseResult",
    "parse_ctest_junit",
    "parse_ctest_stdout",
    "parse_make_check_stdout",
    "parse_qtest_xunit",
]

_MAX_CTEST_REPORT_BYTES = 1_000_000
_MAX_TEST_RESULT_CHARS = 512
_MAX_SANITIZER_TRANSPORT_BYTES = 65_536

# 1/2 Test #1: test_name ......   Passed    0.01 sec
_CTEST_LINE_RE = re.compile(
    r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>\S+)\s+[. ]*(?P<verdict>.+?)\s+[\d.]+\s+sec\s*$"
)
_TESTSUITE_RE = re.compile(r"<testsuite\b.*?</testsuite>", re.DOTALL)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
_SANITIZER_FAILURE_MARKERS = (
    (
        "LeakSanitizer",
        re.compile(r"\bLeakSanitizer:\s*detected memory leaks\b", re.IGNORECASE),
    ),
    (
        "AddressSanitizer",
        re.compile(r"\b(?:ERROR|SUMMARY):\s*AddressSanitizer:\s*\S", re.IGNORECASE),
    ),
    (
        "UndefinedBehaviorSanitizer",
        re.compile(
            r"\b(?:ERROR|SUMMARY):\s*UndefinedBehaviorSanitizer:\s*\S",
            re.IGNORECASE,
        ),
    ),
    (
        "ThreadSanitizer",
        re.compile(r"\b(?:WARNING|SUMMARY):\s*ThreadSanitizer:\s*\S", re.IGNORECASE),
    ),
)
# ``make check`` echoes each test command before running it. Two shapes occur:
#
#   ./test_format -xunitxml
#   /abs/path/target_wrapper.sh  ./test_widget -xunitxml
#
# qmake wraps Qt-linked binaries so they find their libraries, so anchoring at
# the start of the line silently loses exactly the Qt tests this adapter exists
# to run.
_MAKE_INVOCATION_RE = re.compile(r"(?:^|\s)\./(?P<name>[\w.+-]+)(?:\s|$)")
# make's own chatter and the recursive-make guard both mention paths; neither is
# a test being run.
_MAKE_NOISE_RE = re.compile(r"^\s*(?:make(?:\[\d+\])?:|\()")
_MAKE_ERROR_RE = re.compile(r"^\s*make(?:\[\d+\])?: \*\*\* .*Error \d+")
_NOT_EXECUTED_STATES = frozenset({"notrun", "skip", "skipped", "disabled", "blacklisted"})
_PASS_STATES = frozenset({"", "run", "pass", "passed"})


@dataclass(frozen=True)
class TestCaseResult:
    """One test as the build system reported it."""

    # The name starts with "Test", so pytest tries to collect this as a test
    # class and warns on every run. It is a result record, not a test.
    __test__ = False

    name: str
    passed: bool
    message: str = ""
    # ``passed`` alone cannot distinguish an executed failure from a test the
    # build system collected but never ran. Keep this last with a default so
    # existing positional construction remains source compatible.
    executed: bool = True
    # Kept internal to the sanitizer engine. Generic test reporting continues
    # to expose only the bounded ``message`` above.
    diagnostic_output: str = ""
    diagnostic_output_truncated: bool = False

    def __post_init__(self) -> None:
        if self.passed and not self.executed:
            raise ValueError("a test that was not executed cannot be marked as passed")


def _attach_sanitizer_output(
    results: list[TestCaseResult],
    output: str,
    *,
    truncated: bool = False,
) -> list[TestCaseResult]:
    """Attach one captured sanitizer transcript without changing test messages."""

    if any(case.diagnostic_output for case in results):
        if not truncated:
            return results
        return [
            replace(case, diagnostic_output_truncated=True) if case.diagnostic_output else case
            for case in results
        ]
    sanitizer = next(
        (name for name, marker in _SANITIZER_FAILURE_MARKERS if marker.search(output) is not None),
        None,
    )
    if sanitizer is None:
        return results
    failed_index = next(
        (index for index, case in enumerate(results) if case.executed and not case.passed),
        None,
    )
    if failed_index is None:
        # Sanitizer runtimes can be configured to return zero even after a
        # complete report. CTest and make may therefore label every case as a
        # pass while the aggregate process stream still contains the only
        # defect evidence. Attribute that evidence conservatively to the first
        # executed case instead of turning a real report into a clean run.
        failed_index = next(
            (index for index, case in enumerate(results) if case.executed),
            None,
        )
    if failed_index is None:
        diagnostic_output, transport_truncated = _bounded_sanitizer_transport(output)
        return [
            *results,
            TestCaseResult(
                "sanitizer-process",
                False,
                f"{sanitizer} diagnostic",
                diagnostic_output=diagnostic_output,
                diagnostic_output_truncated=truncated or transport_truncated,
            ),
        ]
    diagnostic_output, transport_truncated = _bounded_sanitizer_transport(output)
    attached = list(results)
    selected = attached[failed_index]
    attached[failed_index] = replace(
        selected,
        passed=False,
        message=(
            selected.message
            if not selected.passed and selected.message
            else f"{sanitizer} diagnostic"
        ),
        diagnostic_output=diagnostic_output,
        diagnostic_output_truncated=truncated or transport_truncated,
    )
    return attached


def _bounded_sanitizer_transport(value: str) -> tuple[str, bool]:
    """Bound private diagnostic transport by UTF-8 bytes, not code points."""

    try:
        payload = value.encode("utf-8")
    except UnicodeError:
        return "", True
    if len(payload) <= _MAX_SANITIZER_TRANSPORT_BYTES:
        return value, False
    bounded = payload[:_MAX_SANITIZER_TRANSPORT_BYTES].decode("utf-8", errors="ignore")
    return bounded, True


def _normalized_test_state(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _bounded_test_result(value: str) -> str:
    normalized = value.strip()
    if len(normalized) <= _MAX_TEST_RESULT_CHARS:
        return normalized
    return f"{normalized[: _MAX_TEST_RESULT_CHARS - 3]}..."


def _sanitizer_failure_evidence(
    node: ElementTree.Element,
    failures: list[ElementTree.Element],
) -> tuple[str, str, bool]:
    """Return a bounded class of sanitizer evidence without exporting its trace."""

    evidence: list[str] = []
    for failure in failures:
        evidence.extend((failure.get("message", ""), failure.text or ""))
    for tag in ("system-out", "system-err"):
        evidence.extend(child.text or "" for child in node.findall(tag))
    for sanitizer, marker in _SANITIZER_FAILURE_MARKERS:
        if any(marker.search(value) is not None for value in evidence):
            diagnostic_output, truncated = _bounded_sanitizer_transport("\n".join(evidence))
            return f"{sanitizer} diagnostic", diagnostic_output, truncated
    return "", "", False


def _junit_case(node: ElementTree.Element) -> TestCaseResult:
    name = _bounded_test_result(node.get("name", ""))
    failures = node.findall("failure") + node.findall("error")
    if failures:
        sanitizer, diagnostic_output, diagnostic_output_truncated = _sanitizer_failure_evidence(
            node, failures
        )
        if sanitizer:
            return TestCaseResult(
                name,
                False,
                sanitizer,
                diagnostic_output=diagnostic_output,
                diagnostic_output_truncated=diagnostic_output_truncated,
            )
        parts = [f.get("message", "") or (f.text or "").strip() for f in failures]
        return TestCaseResult(
            name,
            False,
            _bounded_test_result("; ".join(p for p in parts if p)),
        )
    skipped = node.findall("skipped")
    if skipped:
        parts = [item.get("message", "") or (item.text or "").strip() for item in skipped]
        message = _bounded_test_result("; ".join(part for part in parts if part))
        return TestCaseResult(name, False, message or "test was skipped", executed=False)
    sanitizer, diagnostic_output, diagnostic_output_truncated = _sanitizer_failure_evidence(
        node, []
    )
    if sanitizer:
        return TestCaseResult(
            name,
            False,
            sanitizer,
            diagnostic_output=diagnostic_output,
            diagnostic_output_truncated=diagnostic_output_truncated,
        )
    # A test ctest never ran is not evidence that it passes.
    status = node.get("status", "").strip()
    normalized_status = _normalized_test_state(status)
    if normalized_status in _NOT_EXECUTED_STATES:
        return TestCaseResult(
            name,
            False,
            _bounded_test_result(f"ctest reported status {status!r}"),
            executed=False,
        )
    if normalized_status not in _PASS_STATES:
        return TestCaseResult(
            name,
            False,
            _bounded_test_result(f"test framework reported unknown status {status!r}"),
        )
    return TestCaseResult(name, True)


def _parse_xml(text: str) -> ElementTree.Element | None:
    """Parse XML, refusing any document that carries a DTD.

    ElementTree expands internal entities, and the project being verified owns
    this input: ctest embeds test names taken from CMakeLists.txt, and the
    qmake path reads whatever the test binaries printed. On a CI gate that runs
    pull-request sources, that is enough for a billion-laughs document to be
    handed to us. Entities can only be declared in a DTD, so refusing a DOCTYPE
    removes the expansion entirely. Neither ctest nor QtTest emits one.
    """

    if _DOCTYPE_RE.search(text) is not None:
        return None
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None


def parse_ctest_junit(xml_text: str) -> list[TestCaseResult]:
    if not isinstance(xml_text, str) or len(xml_text) > _MAX_CTEST_REPORT_BYTES:
        return []
    try:
        encoded_size = len(xml_text.encode("utf-8"))
    except UnicodeError:
        return []
    if encoded_size > _MAX_CTEST_REPORT_BYTES or "\x00" in xml_text:
        return []
    root = _parse_xml(xml_text)
    if root is None:
        return []
    return [_junit_case(node) for node in root.iter("testcase")]


def _read_ctest_junit(path: Path, containment_root: Path) -> str | None:
    """Read a CTest report through a byte bound, including across file races."""

    try:
        payload = _read_bounded_regular(
            path,
            _MAX_CTEST_REPORT_BYTES,
            containment_root=containment_root,
        )
    except (FileNotFoundError, _ReadError):
        return None
    return payload.decode("utf-8", errors="replace")


def parse_ctest_stdout(text: str) -> list[TestCaseResult]:
    results: list[TestCaseResult] = []
    for line in text.splitlines():
        match = _CTEST_LINE_RE.match(line)
        if match is None:
            continue
        verdict = match.group("verdict").strip().lstrip("*")
        normalized_verdict = _normalized_test_state(verdict)
        executed = not (
            normalized_verdict.startswith("notrun")
            or normalized_verdict.startswith("disabled")
            or normalized_verdict.startswith("skip")
        )
        results.append(
            TestCaseResult(
                match.group("name"),
                executed and verdict == "Passed",
                "" if verdict == "Passed" else verdict,
                executed=executed,
            )
        )
    return results


def parse_qtest_xunit(text: str) -> list[TestCaseResult]:
    """Parse one or more concatenated QtTest xunitxml documents."""

    if _DOCTYPE_RE.search(text) is not None:
        return []

    results: list[TestCaseResult] = []
    for block in _TESTSUITE_RE.findall(text):
        suite = _parse_xml(block)
        if suite is None:
            continue
        suite_name = suite.get("name", "")
        for node in suite.iter("testcase"):
            case = _junit_case(node)
            name = f"{suite_name}::{case.name}" if suite_name else case.name
            result = node.get("result", "pass").strip()
            normalized_result = _normalized_test_state(result)
            if normalized_result in _NOT_EXECUTED_STATES:
                executed = False
                passed = False
            elif normalized_result in {"pass", "passed", "xfail"}:
                executed = case.executed
                passed = case.passed and executed
            elif normalized_result in {"fail", "failed", "error", "xpass"}:
                executed = True
                passed = False
            else:
                # Unknown framework states must not become a silent pass.
                executed = True
                passed = False
            message = case.message
            if not passed and not message:
                message = f"QtTest reported result {result!r}"
            results.append(
                TestCaseResult(
                    name,
                    passed,
                    message,
                    executed=executed,
                    diagnostic_output=case.diagnostic_output,
                    diagnostic_output_truncated=case.diagnostic_output_truncated,
                )
            )
    return results


def _qmake_results(output: str, returncode: int) -> list[TestCaseResult]:
    """Read a `make check` run, per test binary.

    The transcript is authoritative, not the XML. -xunitxml only means something
    to a QtTest binary, and a real qmake project mixes those with tests that
    roll their own main() and ignore the flag. Preferring the XML would report
    the QtTest binaries and silently drop every other one — a green gate over
    tests nobody looked at.

    Per binary also matches what CTest reports, so the two backends count the
    same kind of thing. QtTest's per-function detail is not lost: make stops at
    the first failing binary, so any failures in the XML belong to it.
    """

    results = parse_make_check_stdout(output, returncode)
    if not results:
        return parse_qtest_xunit(output)

    failures = [case for case in parse_qtest_xunit(output) if case.executed and not case.passed]
    if not failures:
        return results
    detail = "; ".join(f"{case.name}: {case.message}".strip(": ") for case in failures)
    return [
        case
        if case.passed
        else TestCaseResult(
            case.name,
            False,
            f"{case.message} — {detail}",
            diagnostic_output=case.diagnostic_output,
            diagnostic_output_truncated=case.diagnostic_output_truncated,
        )
        for case in results
    ]


def parse_make_check_stdout(text: str, returncode: int) -> list[TestCaseResult]:
    """Recover per-test results from a `make check` transcript.

    make echoes each command before running it, so the invocations name the
    tests. A failing test makes make print an Error line and stop, which is why
    a non-zero exit with no attributed failure is blamed on the last test that
    started: the ones after it never ran.
    """

    names: list[str] = []
    failed: set[str] = set()
    current: str | None = None
    for line in text.splitlines():
        if _MAKE_NOISE_RE.match(line):
            if current is not None and _MAKE_ERROR_RE.match(line):
                failed.add(current)
            continue
        match = _MAKE_INVOCATION_RE.search(line)
        if match is not None:
            current = match.group("name")
            if current not in names:
                names.append(current)
            continue

    results = [
        TestCaseResult(name, name not in failed, "" if name not in failed else "make check failed")
        for name in names
    ]
    if returncode != 0 and not failed and results:
        last = results[-1]
        results[-1] = TestCaseResult(
            last.name, False, "make check exited non-zero after this test started"
        )
    return results
