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

from ici.core._build_paths import prepare_owned_shadow, shadow_dir
from ici.core.backend import (
    BACKEND_CMAKE,
    BACKEND_QMAKE,
    BackendChoice,
    select_backend,
)
from ici.core.context import (
    AnalysisContext,
    ArtifactManifest,
    ArtifactScope,
    BuildVariant,
)
from ici.core.models import ToolEvidence
from ici.core.runner import run_process

__all__ = [
    "BACKEND_CMAKE",
    "BACKEND_QMAKE",
    "BackendChoice",
    "select_backend",
]

# --test-dir arrived in CMake 3.20 and --output-junit in 3.21. The roadmap
# treats RHEL 7.9 as a target runtime, so an old ctest cannot be assumed away.
_CTEST_TEST_DIR_MIN = (3, 20)
_CTEST_JUNIT_MIN = (3, 21)

_CMAKE_VERSION_RE = re.compile(r"cmake version (\d+)\.(\d+)")


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

    variant: BuildVariant
    extra_cxx_flags: tuple[str, ...] = ()
    extra_link_flags: tuple[str, ...] = ()
    analysis_database: bool = False

    @property
    def coverage(self) -> bool:
        return self.variant is BuildVariant.COVERAGE

    @property
    def shadow_suffix(self) -> str:
        return {
            BuildVariant.RELEASE: "-build",
            BuildVariant.COVERAGE: "",
            BuildVariant.SANITIZE: "-asan",
        }[self.variant]

    @property
    def build_type(self) -> str:
        return "Release" if self.variant is BuildVariant.RELEASE else "Debug"

    def cxx_flags(self) -> list[str]:
        flags = {
            BuildVariant.RELEASE: [],
            BuildVariant.COVERAGE: ["--coverage"],
            BuildVariant.SANITIZE: [
                "-fsanitize=address,undefined",
                "-fno-omit-frame-pointer",
                "-g",
            ],
        }[self.variant]
        return flags + list(self.extra_cxx_flags)

    def link_flags(self) -> list[str]:
        flags = {
            BuildVariant.RELEASE: [],
            BuildVariant.COVERAGE: ["--coverage"],
            BuildVariant.SANITIZE: ["-fsanitize=address,undefined"],
        }[self.variant]
        return flags + list(self.extra_link_flags)


def cmake_configure_argv(
    cmake_bin: str, root: Path, shadow: Path, options: ConfigureOptions
) -> list[str]:
    argv = [
        cmake_bin,
        "-S",
        str(root),
        "-B",
        str(shadow),
        f"-DCMAKE_BUILD_TYPE={options.build_type}",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    if options.analysis_database:
        argv.append("-DCMAKE_UNITY_BUILD=OFF")
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


def qmake_configure_argv(qmake_bin: str, pro_file: Path, options: ConfigureOptions) -> list[str]:
    """qmake runs with the shadow directory as its cwd; the .pro path is absolute."""

    argv = [qmake_bin, str(pro_file)]
    argv.extend(f"QMAKE_CXXFLAGS+={flag}" for flag in options.cxx_flags())
    argv.extend(f"QMAKE_LFLAGS+={flag}" for flag in options.link_flags())
    return argv


def qmake_build_argv(make_bin: str, jobs: int) -> list[str]:
    return [make_bin, f"--jobs={max(1, jobs)}"]


def qmake_clean_argv(make_bin: str) -> list[str]:
    """Return the deterministic freshness step for a configured qmake tree."""

    return [make_bin, "clean"]


def qmake_test_argv(make_bin: str) -> list[str]:
    """`CONFIG += testcase` generates the check target and forwards TESTARGS."""

    return [make_bin, "check", "TESTARGS=-xunitxml"]


# 1/2 Test #1: test_name ......   Passed    0.01 sec
_CTEST_LINE_RE = re.compile(
    r"^\s*\d+/\d+\s+Test\s+#\d+:\s+(?P<name>\S+)\s+[. ]*(?P<verdict>.+?)\s+[\d.]+\s+sec\s*$"
)
_TESTSUITE_RE = re.compile(r"<testsuite\b.*?</testsuite>", re.DOTALL)
_DOCTYPE_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
# `make check` echoes each test command before running it. Two shapes occur:
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
    variant: BuildVariant = BuildVariant.COVERAGE
    backend: str | None = None
    descriptor: str = ""
    reason: str = ""
    configured: bool = False
    cmake_version: tuple[int, int] | None = None
    analysis_context: AnalysisContext | None = None
    artifact_manifest: ArtifactManifest | None = None
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


def configure(root: Path, options: ConfigureOptions) -> BuildSession:
    """Select a backend and configure a shadow build tree."""

    root = root.resolve(strict=False)
    choice = select_backend(root)
    session = BuildSession(
        root=root,
        shadow=shadow_dir(root, choice.kind or BACKEND_CMAKE, options.shadow_suffix),
        variant=options.variant,
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
    prepared_shadow, shadow_error = prepare_owned_shadow(session.root, session.shadow)
    if prepared_shadow is None:
        _fail(session, shadow_error)
        return session
    session.shadow = prepared_shadow

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
    if result.returncode != 0 or result.timed_out or result.truncated:
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
        # qmake's LIBS entry is a linker argument, not necessarily a Makefile
        # dependency.  In a reused shadow tree an unchanged test executable can
        # therefore remain linked to an older static archive.  Besides testing
        # stale code, coverage then writes a .gcda whose stamp does not match
        # the newly-built .gcno and gcov reports a false 0%.  A configured
        # qmake tree always has a clean target, so make freshness explicit
        # before the parallel build until content-addressed shadows land.
        clean_argv = qmake_clean_argv(make_bin)
        clean_result = run_process(clean_argv, cwd=session.shadow)
        _record(session, "qmake clean", clean_argv, clean_result)
        if clean_result.returncode != 0 or clean_result.timed_out or clean_result.truncated:
            _fail(session, f"qmake clean failed: {clean_result.stderr[:200]}")
            return False
        argv = qmake_build_argv(make_bin, os.cpu_count() or 1)
        cwd = session.shadow

    result = run_process(argv, cwd=cwd)
    _record(session, f"{session.backend} build", argv, result)
    if result.returncode != 0 or result.timed_out or result.truncated:
        _fail(session, f"{session.backend} build failed: {result.stderr[:200]}")
        return False
    return _capture_artifact_manifest(session)


def _artifact_kind(path: Path) -> str:
    if path.suffix in (".a", ".lib"):
        return "static-library"
    if path.suffix in (".so", ".dylib", ".dll") or ".so." in path.name:
        return "shared-library"
    return "executable"


def _linked_artifact_paths(shadow: Path) -> list[tuple[Path, ArtifactScope, str]]:
    """Find linked binaries/libraries without treating generated scripts as products."""

    paths: dict[Path, tuple[Path, ArtifactScope, str]] = {}
    binary_magics = (
        b"\x7fELF",
        b"MZ",
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
    )
    for candidate in sorted(shadow.rglob("*")):
        try:
            if not candidate.is_file():
                continue
            if candidate.suffix in (".o", ".obj", ".gcno", ".gcda"):
                continue
            with candidate.open("rb") as stream:
                prefix = stream.read(4)
        except OSError:
            continue
        is_library = candidate.suffix in (".a", ".lib", ".so", ".dylib", ".dll") or (
            ".so." in candidate.name
        )
        if not is_library and not any(prefix.startswith(magic) for magic in binary_magics):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(shadow).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        paths[resolved] = (Path(relative), ArtifactScope.SHADOW, _artifact_kind(resolved))
    return [paths[path] for path in sorted(paths)]


def _capture_artifact_manifest(session: BuildSession) -> bool:
    """Publish a frozen manifest only when a run has a shared analysis identity."""

    context = session.analysis_context
    if context is None:
        return True
    try:
        session.artifact_manifest = ArtifactManifest.create(
            project_root=session.root,
            shadow_root=session.shadow,
            variant=session.variant,
            identity=context.identity,
            paths=_linked_artifact_paths(session.shadow),
            producer=f"{session.backend or 'unknown'}.build",
        )
        return True
    except (OSError, ValueError) as err:
        session.artifact_manifest = None
        _fail(session, f"artifact manifest validation failed: {err}")
        return False


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

    return _qmake_results(result.stdout + result.stderr, result.returncode)


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

    failures = [case for case in parse_qtest_xunit(output) if not case.passed]
    if not failures:
        return results
    detail = "; ".join(f"{case.name}: {case.message}".strip(": ") for case in failures)
    return [
        case if case.passed else TestCaseResult(case.name, False, f"{case.message} — {detail}")
        for case in results
    ]


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
