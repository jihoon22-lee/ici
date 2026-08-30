"""CMake/CTest and qmake/Make build adapters.

The build and test engines share this module. Both need a configure step, and
running configure from each engine separately would either configure the same
shadow tree twice or drift on flags. Scope rules differing per engine is a
problem this repository has already hit twice (B-1, C-9), so the new build path
starts in one place.
"""

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from ici.core.models import ToolEvidence
from ici.core.runner import run_process

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


def shadow_dir(root: Path, backend: str, suffix: str = "") -> Path:
    """Build directory ici owns. Never the project's own build tree.

    The suffix keeps engines apart. test builds with --coverage and sanitize
    with -fsanitize; sharing one tree would make each engine silently rebuild
    the other's objects with the wrong flags on every run.
    """

    return root / "build" / f"ici-{backend}{suffix}"


def parse_cmake_version(text: str) -> tuple[int, int] | None:
    match = _CMAKE_VERSION_RE.search(text)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


@dataclass(frozen=True)
class ConfigureOptions:
    """What an engine wants out of its own build tree.

    `build` wants neither coverage nor sanitizers — instrumenting a release
    artifact would be wrong. `test` wants coverage. `sanitize` wants the
    sanitizers and no coverage, since it measures crashes rather than lines.
    """

    coverage: bool = True
    extra_cxx_flags: tuple[str, ...] = ()
    extra_link_flags: tuple[str, ...] = ()
    shadow_suffix: str = ""

    def cxx_flags(self) -> list[str]:
        flags = ["--coverage"] if self.coverage else []
        return flags + list(self.extra_cxx_flags)

    def link_flags(self) -> list[str]:
        flags = ["--coverage"] if self.coverage else []
        return flags + list(self.extra_link_flags)


def cmake_configure_argv(
    cmake_bin: str, root: Path, shadow: Path, options: "ConfigureOptions | None" = None
) -> list[str]:
    options = options or ConfigureOptions()
    argv = [
        cmake_bin,
        "-S",
        str(root),
        "-B",
        str(shadow),
        "-DCMAKE_BUILD_TYPE=Debug",
    ]
    cxx = " ".join(options.cxx_flags())
    link = " ".join(options.link_flags())
    if cxx:
        argv.append(f"-DCMAKE_CXX_FLAGS={cxx}")
    if link:
        argv.append(f"-DCMAKE_EXE_LINKER_FLAGS={link}")
    return argv


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


def qmake_configure_argv(
    qmake_bin: str, pro_file: Path, options: "ConfigureOptions | None" = None
) -> list[str]:
    """qmake runs with the shadow directory as its cwd; the .pro path is absolute."""

    options = options or ConfigureOptions()
    argv = [qmake_bin, str(pro_file)]
    argv.extend(f"QMAKE_CXXFLAGS+={flag}" for flag in options.cxx_flags())
    argv.extend(f"QMAKE_LFLAGS+={flag}" for flag in options.link_flags())
    return argv


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
_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)


@dataclass(frozen=True)
class TestCaseResult:
    """One test as the build system reported it."""

    # The name starts with "Test", so pytest tries to collect this as a test
    # class and warns on every run. It is a result record, not a test.
    __test__ = False

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
    root = _parse_xml(xml_text)
    if root is None:
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


@dataclass
class BuildSession:
    """One configure of one project. Shared by the build and test engines."""

    root: Path
    shadow: Path
    backend: str | None = None
    descriptor: str = ""
    reason: str = ""
    configured: bool = False
    cmake_version: tuple[int, int] | None = None
    tool_evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _record(session: BuildSession, name: str, argv: list[str], result) -> None:
    session.tool_evidence.append(
        ToolEvidence(
            name=name,
            path=argv[0],
            argv=argv,
            returncode=result.returncode,
            timed_out=result.timed_out,
            truncated=result.truncated,
        )
    )


def _fail(session: BuildSession, message: str) -> None:
    if message not in session.errors:
        session.errors.append(message)


def _which(session: BuildSession, name: str) -> str | None:
    found = shutil.which(name)
    if found is None:
        _fail(session, f"{name} executable was unavailable")
    return found


def configure(root: Path, options: ConfigureOptions | None = None) -> BuildSession:
    """Select a backend and configure a shadow build tree."""

    options = options or ConfigureOptions()
    choice = select_backend(root)
    session = BuildSession(
        root=root,
        shadow=shadow_dir(root, choice.kind or BACKEND_CMAKE, options.shadow_suffix),
        backend=choice.kind,
        descriptor=choice.descriptor,
        reason=choice.reason,
    )
    if choice.kind is None:
        return session

    # The choice itself is evidence. Without it the report cannot say why one
    # backend ran and the other did not. The reason goes in the name, not in
    # `error` — this is a normal outcome, not a failure.
    session.tool_evidence.append(
        ToolEvidence(name=f"build backend selection: {choice.reason}", path="")
    )
    session.shadow.mkdir(parents=True, exist_ok=True)

    if choice.kind == BACKEND_CMAKE:
        return _configure_cmake(session, options)
    return _configure_qmake(session, options)


def _configure_cmake(session: BuildSession, options: ConfigureOptions) -> BuildSession:
    cmake_bin = _which(session, "cmake")
    if cmake_bin is None:
        return session

    version_argv = [cmake_bin, "--version"]
    version_result = run_process(version_argv)
    _record(session, "cmake --version", version_argv, version_result)
    session.cmake_version = parse_cmake_version(version_result.stdout)

    argv = cmake_configure_argv(cmake_bin, session.root, session.shadow, options)
    result = run_process(argv, cwd=session.root)
    _record(session, "cmake configure", argv, result)
    if result.returncode != 0:
        _fail(session, f"cmake configure failed: {result.stderr[:200]}")
        return session
    session.configured = True
    return session


def _configure_qmake(session: BuildSession, options: ConfigureOptions) -> BuildSession:
    # Debian ships Qt6's qmake as qmake6; some distributions only have `qmake`.
    # Probing both before recording an error keeps the message honest.
    qmake_bin = shutil.which("qmake6") or shutil.which("qmake")
    if qmake_bin is None:
        _fail(session, "qmake6 executable was unavailable")
        return session

    argv = qmake_configure_argv(qmake_bin, session.root / session.descriptor, options)
    result = run_process(argv, cwd=session.shadow)
    _record(session, "qmake configure", argv, result)
    if result.returncode != 0:
        _fail(session, f"qmake configure failed: {result.stderr[:200]}")
        return session
    session.configured = True
    return session


def build(session: BuildSession) -> bool:
    """Build the configured tree. Returns False on failure."""

    if not session.configured:
        return False
    if session.backend == BACKEND_CMAKE:
        cmake_bin = _which(session, "cmake")
        if cmake_bin is None:
            return False
        argv = cmake_build_argv(cmake_bin, session.shadow)
        cwd = session.root
    else:
        make_bin = _which(session, "make")
        if make_bin is None:
            return False
        argv = qmake_build_argv(make_bin, os.cpu_count() or 1)
        cwd = session.shadow

    result = run_process(argv, cwd=cwd)
    _record(session, f"{session.backend} build", argv, result)
    if result.returncode != 0:
        _fail(session, f"{session.backend} build failed: {result.stderr[:200]}")
        return False
    return True


def run_tests(session: BuildSession, env: dict[str, str] | None = None) -> list[TestCaseResult]:
    """Run the project's tests through its own build system.

    `env` carries options the runner needs but the build does not — ASAN_OPTIONS
    and UBSAN_OPTIONS for the sanitize engine. Without it the adapter path would
    run the sanitizers with different settings than the generic g++ path.
    """

    if session.backend == BACKEND_CMAKE:
        ctest_bin = _which(session, "ctest")
        if ctest_bin is None:
            return []
        argv, junit = cmake_test_argv(ctest_bin, session.shadow, session.cmake_version)
        result = run_process(argv, cwd=session.shadow, env=env)
        _record(session, "ctest", argv, result)
        if junit is not None and junit.is_file():
            parsed = parse_ctest_junit(junit.read_text(encoding="utf-8", errors="replace"))
            if parsed:
                return parsed
        return parse_ctest_stdout(result.stdout)

    make_bin = _which(session, "make")
    if make_bin is None:
        return []
    argv = qmake_test_argv(make_bin)
    result = run_process(argv, cwd=session.shadow, env=env)
    _record(session, "make check", argv, result)
    return parse_qtest_xunit(result.stdout)


def collect_coverage(session: BuildSession) -> Path | None:
    """Run gcov over the shadow tree. Must be called after run_tests."""

    gcov_bin = _which(session, "gcov")
    if gcov_bin is None:
        return None
    out_dir, argvs = plan_gcov(session.shadow, gcov_bin)
    if not argvs:
        _fail(session, "C++ gcov data files were unavailable")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    for argv in argvs:
        result = run_process(argv, cwd=out_dir)
        _record(session, "gcov", argv, result)
    return out_dir
