"""CMake/CTest and qmake/Make build adapters.

The build and test engines share this module. Both need a configure step, and
running configure from each engine separately would either configure the same
shadow tree twice or drift on flags. Scope rules differing per engine is a
problem this repository has already hit twice (B-1, C-9), so the new build path
starts in one place.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

BACKEND_CMAKE = "cmake"
BACKEND_QMAKE = "qmake"

# Only the project root is inspected. Descriptors in subdirectories do not
# select a backend, which is what keeps projects that have not been converted
# yet (a CMakeLists.txt under src/gui) on their existing g++ path.
_MAKEFILE_NAMES = ("Makefile", "makefile", "GNUmakefile")


@dataclass(frozen=True)
class BackendChoice:
    """Which backend runs, and why. The reason is recorded as tool evidence."""

    kind: str | None
    reason: str
    descriptor: str = ""


def _is_real_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def select_backend(root: Path) -> BackendChoice:
    """Pick a build backend from the descriptor at the project root."""

    cmake_file = root / "CMakeLists.txt"
    has_cmake = _is_real_file(cmake_file)
    pro_files = sorted(p for p in root.glob("*.pro") if _is_real_file(p))
    makefiles = [name for name in _MAKEFILE_NAMES if _is_real_file(root / name)]

    if has_cmake:
        reason = "CMakeLists.txt at the project root selected the CMake backend"
        if pro_files:
            reason += f"; {pro_files[0].name} was present and passed over"
        return BackendChoice(BACKEND_CMAKE, reason, "CMakeLists.txt")

    if pro_files:
        return BackendChoice(
            BACKEND_QMAKE,
            f"{pro_files[0].name} at the project root selected the qmake backend",
            pro_files[0].name,
        )

    if makefiles:
        return BackendChoice(
            None,
            f"{makefiles[0]} at the project root has no adapter; "
            "only CMake and qmake are supported",
            makefiles[0],
        )

    return BackendChoice(None, "No build descriptor at the project root", "")


# --test-dir arrived in CMake 3.20 and --output-junit in 3.21. The roadmap
# treats RHEL 7.9 as a target runtime, so an old ctest cannot be assumed away.
_CTEST_TEST_DIR_MIN = (3, 20)
_CTEST_JUNIT_MIN = (3, 21)

_CMAKE_VERSION_RE = re.compile(r"cmake version (\d+)\.(\d+)")


def shadow_dir(root: Path, backend: str) -> Path:
    """Build directory ici owns. Never the project's own build tree."""

    return root / "build" / f"ici-{backend}"


def parse_cmake_version(text: str) -> tuple[int, int] | None:
    match = _CMAKE_VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def cmake_configure_argv(cmake_bin: str, root: Path, shadow: Path) -> list[str]:
    return [
        cmake_bin,
        "-S",
        str(root),
        "-B",
        str(shadow),
        "-DCMAKE_BUILD_TYPE=Debug",
        "-DCMAKE_CXX_FLAGS=--coverage",
        "-DCMAKE_EXE_LINKER_FLAGS=--coverage",
    ]


def cmake_build_argv(cmake_bin: str, shadow: Path) -> list[str]:
    return [cmake_bin, "--build", str(shadow), "--parallel"]


def cmake_test_argv(
    ctest_bin: str, shadow: Path, version: tuple[int, int] | None
) -> tuple[list[str], Path | None]:
    """Return the ctest argv and the JUnit path, or None when stdout must be parsed."""

    argv = [ctest_bin, "--output-on-failure"]
    if version is not None and version >= _CTEST_TEST_DIR_MIN:
        argv.extend(["--test-dir", str(shadow)])
    if version is not None and version >= _CTEST_JUNIT_MIN:
        junit = shadow / "ici-ctest.xml"
        argv.extend(["--output-junit", str(junit)])
        return argv, junit
    return argv, None


def qmake_configure_argv(qmake_bin: str, pro_file: Path) -> list[str]:
    """qmake runs with the shadow directory as its cwd; the .pro path is absolute."""

    return [
        qmake_bin,
        str(pro_file),
        "QMAKE_CXXFLAGS+=--coverage",
        "QMAKE_LFLAGS+=--coverage",
    ]


def qmake_build_argv(make_bin: str, jobs: int) -> list[str]:
    return [make_bin, f"--jobs={max(1, jobs)}"]


def qmake_test_argv(make_bin: str) -> list[str]:
    """`CONFIG += testcase` generates the check target and forwards TESTARGS."""

    return [make_bin, "check", "TESTARGS=-xunitxml"]


# 1/2 Test #1: test_name ......   Passed    0.01 sec
_CTEST_LINE_RE = re.compile(
    r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>\S+)\s+[. ]*(?P<verdict>.+?)\s+[\d.]+\s+sec\s*$"
)
_TESTSUITE_RE = re.compile(r"<testsuite\b.*?</testsuite>", re.DOTALL)


@dataclass(frozen=True)
class TestCaseResult:
    """One test as the build system reported it."""

    name: str
    passed: bool
    message: str = ""


def _junit_case(node: ElementTree.Element) -> TestCaseResult:
    name = node.get("name", "")
    failures = node.findall("failure") + node.findall("error")
    if failures:
        parts = [f.get("message", "") or (f.text or "").strip() for f in failures]
        return TestCaseResult(name, False, "; ".join(p for p in parts if p))
    # A test ctest never ran is not evidence that it passes.
    status = node.get("status", "")
    if status and status not in ("run", "passed"):
        return TestCaseResult(name, False, f"ctest reported status {status!r}")
    return TestCaseResult(name, True)


def parse_ctest_junit(xml_text: str) -> list[TestCaseResult]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    return [_junit_case(node) for node in root.iter("testcase")]


def parse_ctest_stdout(text: str) -> list[TestCaseResult]:
    results: list[TestCaseResult] = []
    for line in text.splitlines():
        match = _CTEST_LINE_RE.match(line)
        if match is None:
            continue
        verdict = match.group("verdict").strip().lstrip("*")
        results.append(
            TestCaseResult(
                match.group("name"),
                verdict == "Passed",
                "" if verdict == "Passed" else verdict,
            )
        )
    return results


def parse_qtest_xunit(text: str) -> list[TestCaseResult]:
    """Parse one or more concatenated QtTest xunitxml documents."""

    results: list[TestCaseResult] = []
    for block in _TESTSUITE_RE.findall(text):
        try:
            suite = ElementTree.fromstring(block)
        except ElementTree.ParseError:
            continue
        suite_name = suite.get("name", "")
        for node in suite.iter("testcase"):
            case = _junit_case(node)
            name = f"{suite_name}::{case.name}" if suite_name else case.name
            passed = case.passed and node.get("result", "pass") == "pass"
            message = case.message
            if not passed and not message:
                message = f"QtTest reported result {node.get('result', '')!r}"
            results.append(TestCaseResult(name, passed, message))
    return results


GCOV_OUTPUT_DIRNAME = "ici-gcov"


def plan_gcov(shadow: Path, gcov_bin: str) -> tuple[Path, list[list[str]]]:
    """Group .gcno files by object directory and return one gcov argv per group.

    Callers must run every argv with ``cwd`` set to the returned directory.
    gcov writes .gcov files into its own working directory, so fixing the cwd
    makes the output flat and lets engines.coverage_support.parse_gcov_dir stay
    as it is — it globs a single directory level.
    """

    out_dir = shadow / GCOV_OUTPUT_DIRNAME
    groups: dict[Path, list[str]] = {}
    for gcno in sorted(shadow.rglob("*.gcno")):
        if out_dir == gcno.parent or out_dir in gcno.parents:
            continue
        groups.setdefault(gcno.parent, []).append(str(gcno))

    argvs = [
        [gcov_bin, "-b", "-p", "-o", str(obj_dir), *files]
        for obj_dir, files in sorted(groups.items())
    ]
    return out_dir, argvs
