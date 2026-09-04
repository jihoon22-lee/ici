"""CMake/CTest and qmake/Make build adapters.

The build and test engines share this module. Both need a configure step, and
running configure from each engine separately would either configure the same
shadow tree twice or drift on flags. Scope rules differing per engine is a
problem this repository has already hit twice (B-1, C-9), so the new build path
starts in one place.
"""

import hashlib
import os
import re
import shutil
import stat
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath

from ici.core._build_paths import prepare_owned_shadow, shadow_dir
from ici.core._cmake_test_results import (
    TestCaseResult,
    _attach_sanitizer_output,
    _qmake_results,
    _read_ctest_junit,
    parse_ctest_junit,
    parse_ctest_stdout,
    parse_make_check_stdout,
    parse_qtest_xunit,  # noqa: F401 - retained as a public cmake adapter import
)
from ici.core._qmake_commands import qmake_configure_argv
from ici.core.backend import (
    BACKEND_CMAKE,
    BACKEND_MAKE,
    BACKEND_QMAKE,
    BackendChoice,
    select_backend,
)
from ici.core.context import (
    MAX_ARTIFACT_MANIFEST_RECORDS,
    AnalysisContext,
    ArtifactManifest,
    ArtifactScope,
    BuildVariant,
)
from ici.core.make import MakeConfigError, MakePlan, make_plan, resolved_argv
from ici.core.models import ToolEvidence
from ici.core.redaction import _redact_compilation_argv
from ici.core.redaction_values import REDACTED
from ici.core.runner import run_process

__all__ = [
    "BACKEND_CMAKE",
    "BACKEND_MAKE",
    "BACKEND_QMAKE",
    "BackendChoice",
    "select_backend",
]

# --test-dir arrived in CMake 3.20 and --output-junit in 3.21. The roadmap
# treats RHEL 7.9 as a target runtime, so an old ctest cannot be assumed away.
_CTEST_TEST_DIR_MIN = (3, 20)
_CTEST_JUNIT_MIN = (3, 21)
_ARTIFACT_ID_LIMIT = 512
_MAX_ARTIFACT_DISCOVERY_ENTRIES = 200_000
_PRODUCER_SECRET_FLAG_RE = re.compile(
    r"^--?(?:api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret|"
    r"password|passwd|secret|token)(?:=|$)",
    re.IGNORECASE,
)

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
    extra_c_flags: tuple[str, ...] = ()
    extra_cxx_flags: tuple[str, ...] = ()
    extra_link_flags: tuple[str, ...] = ()
    analysis_database: bool = False
    generator: str = ""
    shadow_suffix_override: str = ""
    qmake_capture_wrapper: str = ""
    qmake_capture_cxx: str = ""
    qmake_capture_cc: str = ""

    @property
    def coverage(self) -> bool:
        return self.variant is BuildVariant.COVERAGE

    @property
    def shadow_suffix(self) -> str:
        if self.shadow_suffix_override:
            if re.fullmatch(r"-[a-z0-9][a-z0-9-]{0,63}", self.shadow_suffix_override) is None:
                raise ValueError("shadow suffix override must be a bounded lowercase suffix")
            return self.shadow_suffix_override
        return {
            BuildVariant.RELEASE: "-build",
            BuildVariant.COVERAGE: "",
            BuildVariant.SANITIZE: "-asan",
            BuildVariant.THREAD_SANITIZE: "-tsan",
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
            BuildVariant.THREAD_SANITIZE: [
                "-fsanitize=thread",
                "-fno-omit-frame-pointer",
                "-g",
            ],
        }[self.variant]
        return flags + list(self.extra_cxx_flags)

    def c_flags(self) -> list[str]:
        """Return optional C-only instrumentation without changing existing variants."""

        return list(self.extra_c_flags)

    def link_flags(self) -> list[str]:
        flags = {
            BuildVariant.RELEASE: [],
            BuildVariant.COVERAGE: ["--coverage"],
            BuildVariant.SANITIZE: ["-fsanitize=address,undefined"],
            BuildVariant.THREAD_SANITIZE: ["-fsanitize=thread"],
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
    ]
    if options.generator:
        if options.generator != "Unix Makefiles":
            raise ValueError("only the audited Unix Makefiles generator may be forced")
        argv.extend(["-G", options.generator])
    argv.extend(
        [
            f"-DCMAKE_BUILD_TYPE={options.build_type}",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
    )
    if options.analysis_database:
        argv.append("-DCMAKE_UNITY_BUILD=OFF")
    c = " ".join(options.c_flags())
    cxx = " ".join(options.cxx_flags())
    link = " ".join(options.link_flags())
    if c:
        argv.append(f"-DCMAKE_C_FLAGS={c}")
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


def qmake_build_argv(make_bin: str, jobs: int) -> list[str]:
    return [make_bin, f"--jobs={max(1, jobs)}"]


def qmake_clean_argv(make_bin: str) -> list[str]:
    """Return the deterministic freshness step for a configured qmake tree."""

    return [make_bin, "clean"]


def qmake_test_argv(make_bin: str) -> list[str]:
    """`CONFIG += testcase` generates the check target and forwards TESTARGS."""

    return [make_bin, "check", "TESTARGS=-xunitxml"]


GCOV_OUTPUT_DIRNAME = "ici-gcov"


def plan_gcov(
    shadow: Path, gcov_bin: str, *, json_format: bool = False
) -> tuple[Path, list[list[str]]]:
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

    format_flags = ["--json-format"] if json_format else []
    argvs = [
        [gcov_bin, *format_flags, "-b", "-p", "-o", str(obj_dir), *files]
        for obj_dir, files in sorted(groups.items())
    ]
    return out_dir, argvs


def gcov_json_capability(result) -> bool | None:
    """Classify a bounded ``gcov --help`` result.

    ``False`` is reserved for a successful old-gcov help transcript that does
    not advertise JSON.  Failed, timed-out, or truncated probes return ``None``
    so callers cannot silently turn an indeterminate modern tool into the
    lower-fidelity text fallback.
    """

    if result.returncode != 0 or result.timed_out or result.truncated:
        return None
    output = f"{result.stdout}\n{result.stderr}"
    return "--json-format" in output


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
    make_plan: MakePlan | None = None
    tool_evidence: list[ToolEvidence] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    coverage_format: str = ""
    coverage_report_count: int = 0


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


def _clear_owned_gcov_output(shadow: Path) -> tuple[bool, str]:
    """Remove only ici's exact coverage-output directory before Make clean.

    ``collect_coverage`` writes its generated ``.gcov`` files below the
    dedicated ``ici-gcov`` directory.  A project's clean recipe may enforce
    ownership of everything below its shadow and reject that directory on the
    next run.  The directory name is the ownership boundary here: nothing
    else in the shadow is eligible for this cleanup.

    The lstat checks intentionally reject symlinks and non-directories before
    any deletion.  ``shutil.rmtree`` is then used only for the validated
    regular directory; an exception is returned to the caller so Make never
    runs after an incomplete cleanup.
    """

    try:
        shadow_info = shadow.lstat()
    except FileNotFoundError:
        # A fresh configured Make shadow has no analyzer output to clear.
        return True, ""
    except (OSError, RuntimeError) as err:
        return False, f"ici-gcov cleanup could not inspect the Make shadow: {err}"
    if stat.S_ISLNK(shadow_info.st_mode) or not stat.S_ISDIR(shadow_info.st_mode):
        return False, "Make shadow is not a regular directory; refusing ici-gcov cleanup"

    output = shadow / GCOV_OUTPUT_DIRNAME
    try:
        output_info = output.lstat()
    except FileNotFoundError:
        return True, ""
    except (OSError, RuntimeError) as err:
        return False, f"ici-gcov cleanup could not inspect {output}: {err}"
    if stat.S_ISLNK(output_info.st_mode):
        return False, f"refusing to remove symlinked ici-gcov output: {output}"
    if not stat.S_ISDIR(output_info.st_mode):
        return False, f"refusing to remove non-directory ici-gcov output: {output}"

    try:
        shutil.rmtree(output)
    except (OSError, RuntimeError) as err:
        return False, f"ici-gcov cleanup failed for {output}: {err}"
    return True, ""


def _which(session: BuildSession, name: str) -> str | None:
    found = shutil.which(name)
    if found is None:
        _fail(session, f"{name} executable was unavailable")
    return found


def configure(
    root: Path,
    options: ConfigureOptions,
    config: dict | None = None,
) -> BuildSession:
    """Select a backend and configure a shadow build tree."""

    root = root.resolve(strict=False)
    choice = select_backend(root, config)
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
    if choice.kind == BACKEND_MAKE:
        try:
            plan = make_plan(root, config or {}, options.variant)
            plan.shadow.mkdir(parents=True, exist_ok=True)
            session.shadow = plan.shadow.resolve(strict=True)
            session.make_plan = plan
        except (MakeConfigError, OSError, RuntimeError) as err:
            _fail(session, f"Make configuration failed: {err}")
            return session
        return _configure_make(session)

    prepared_shadow, shadow_error = prepare_owned_shadow(session.root, session.shadow)
    if prepared_shadow is None:
        _fail(session, shadow_error)
        return session
    session.shadow = prepared_shadow

    if choice.kind == BACKEND_CMAKE:
        return _configure_cmake(session, options)
    return _configure_qmake(session, options)


def _run_make_command(session: BuildSession, name: str, configured: tuple[str, ...], env=None):
    try:
        argv = resolved_argv(configured)
    except MakeConfigError as err:
        _fail(session, str(err))
        return None
    plan = session.make_plan
    if plan is None:
        _fail(session, "Make command plan is unavailable")
        return None
    result = run_process(argv, cwd=plan.workdir, env=env)
    _record(session, name, argv, result)
    return result


def _configure_make(session: BuildSession) -> BuildSession:
    plan = session.make_plan
    if plan is None:
        _fail(session, "Make command plan is unavailable")
        return session
    if plan.configure_argv:
        result = _run_make_command(session, "make configure", plan.configure_argv)
        if result is None:
            return session
        if result.returncode != 0 or result.timed_out or result.truncated:
            _fail(session, "Make configure command did not complete successfully")
            return session
    session.configured = True
    return session


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
    if result.returncode != 0 or result.timed_out or result.truncated:
        _fail(session, f"qmake configure failed: {result.stderr[:200]}")
        return session
    session.configured = True
    return session


def build(session: BuildSession, *, env: dict[str, str] | None = None) -> bool:
    """Build the configured tree. Returns False on failure."""

    if not session.configured:
        return False
    if session.backend == BACKEND_MAKE:
        plan = session.make_plan
        if plan is None:
            _fail(session, "Make command plan is unavailable")
            return False
        if plan.clean_argv:
            cleared, cleanup_error = _clear_owned_gcov_output(session.shadow)
            if not cleared:
                _fail(session, cleanup_error)
                return False
            clean_result = _run_make_command(session, "make clean", plan.clean_argv, env)
            if clean_result is None or (
                clean_result.returncode != 0 or clean_result.timed_out or clean_result.truncated
            ):
                _fail(session, "Make clean command did not complete successfully")
                return False
        result = _run_make_command(session, "make build", plan.build_argv, env)
        if result is None or result.returncode != 0 or result.timed_out or result.truncated:
            _fail(session, "Make build command did not complete successfully")
            return False
        return _capture_artifact_manifest(session)

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
        clean_result = run_process(clean_argv, cwd=session.shadow, env=env)
        _record(session, "qmake clean", clean_argv, clean_result)
        if clean_result.returncode != 0 or clean_result.timed_out or clean_result.truncated:
            _fail(session, f"qmake clean failed: {clean_result.stderr[:200]}")
            return False
        argv = qmake_build_argv(make_bin, os.cpu_count() or 1)
        cwd = session.shadow

    result = run_process(argv, cwd=cwd, env=env)
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


def _artifact_target(path: str, kind: str) -> str:
    """Derive a stable build-target label when the adapter exposes no target map.

    CMake/qmake/Make output discovery gives us a path, not the logical target
    name.  The filename is the most useful portable approximation: strip the
    conventional library prefix/suffix while retaining names such as versioned
    shared libraries.  A path fallback keeps unusual names non-empty.
    """

    name = PureWindowsPath(path).name if "\\" in path else Path(path).name
    if kind == "static-library":
        for suffix in (".a", ".lib"):
            if name.casefold().endswith(suffix):
                name = name[: -len(suffix)]
                break
    elif kind == "shared-library":
        if ".so." in name:
            name = name.split(".so.", 1)[0]
        else:
            for suffix in (".so", ".dylib", ".dll"):
                if name.casefold().endswith(suffix):
                    name = name[: -len(suffix)]
                    break
    else:
        name = Path(name).stem
    if name.startswith("lib") and len(name) > 3:
        name = name[3:]
    return name or path


def _redact_producer_argv(argv: tuple[str, ...], root: Path) -> tuple[str, ...]:
    """Normalize build argv and apply both compiler and generic flag redaction."""

    if not argv:
        return ()
    executable = argv[0]
    if Path(executable).is_absolute() or PureWindowsPath(executable).is_absolute():
        executable = (
            PureWindowsPath(executable).name if "\\" in executable else Path(executable).name
        )
    normalized = (executable, *argv[1:])
    redacted = list(_redact_compilation_argv(normalized, root))
    hide_next = False
    root_text = root.as_posix().rstrip("/")
    for index, raw in enumerate(normalized):
        if hide_next:
            redacted[index] = REDACTED
            hide_next = False
        flag = raw.split("=", 1)[0]
        if _PRODUCER_SECRET_FLAG_RE.fullmatch(flag + ("=" if "=" in raw else "")):
            if "=" in raw:
                redacted[index] = f"{flag}={REDACTED}"
            else:
                hide_next = True
        # The compiler redactor handles standalone path arguments.  This
        # additional replacement covers Make/CMake options that embed a
        # project/build path in an otherwise opaque token.
        if root_text and root_text in redacted[index]:
            redacted[index] = redacted[index].replace(root_text + "/", "")
            redacted[index] = redacted[index].replace(root_text, ".")
    return tuple(redacted)


def _artifact_id(variant: BuildVariant, scope: ArtifactScope, path: str) -> str:
    """Build a stable address, hashing only exceptionally long paths."""

    value = f"{variant.value}:{scope.value}:{path}"
    if len(value) <= _ARTIFACT_ID_LIMIT:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{variant.value}:{scope.value}:path-{digest}"


def _producer_command(session: BuildSession) -> tuple[str, ...]:
    """Return bounded, report-safe argv for the command that emitted outputs."""

    expected_name = f"{session.backend or 'unknown'} build"
    evidence = next(
        (item for item in reversed(session.tool_evidence) if item.name == expected_name),
        None,
    )
    if evidence is None or not evidence.argv:
        return ()
    return _redact_producer_argv(tuple(evidence.argv), session.root)


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
    candidates: list[Path] = []
    for candidate in shadow.rglob("*"):
        candidates.append(candidate)
        if len(candidates) > _MAX_ARTIFACT_DISCOVERY_ENTRIES:
            raise ValueError(
                f"artifact discovery exceeds the {_MAX_ARTIFACT_DISCOVERY_ENTRIES} entry limit"
            )
    for candidate in sorted(candidates):
        try:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.suffix in (".o", ".obj", ".gcno", ".gcda"):
                continue
            with candidate.open("rb") as stream:
                prefix = stream.read(8)
        except OSError:
            continue
        is_static_library = candidate.suffix in (".a", ".lib") and prefix.startswith(b"!<arch>\n")
        is_linked_binary = any(prefix.startswith(magic) for magic in binary_magics)
        if not is_static_library and not is_linked_binary:
            continue
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(shadow).as_posix()
        except (OSError, RuntimeError, ValueError):
            continue
        paths[resolved] = (Path(relative), ArtifactScope.SHADOW, _artifact_kind(resolved))
        if len(paths) > MAX_ARTIFACT_MANIFEST_RECORDS:
            raise ValueError(
                f"artifact discovery exceeds the {MAX_ARTIFACT_MANIFEST_RECORDS} record limit"
            )
    return [paths[path] for path in sorted(paths)]


def _capture_artifact_manifest(session: BuildSession) -> bool:
    """Publish a frozen manifest only when a run has a shared analysis identity."""

    context = session.analysis_context
    if context is None:
        return True
    try:
        manifest = ArtifactManifest.create(
            project_root=session.root,
            shadow_root=session.shadow,
            variant=session.variant,
            identity=context.identity,
            paths=_linked_artifact_paths(session.shadow),
            producer=f"{session.backend or 'unknown'}.build",
        )
        command = _producer_command(session)
        enriched = []
        for record in manifest.artifacts:
            target = _artifact_target(record.path, record.kind)
            # Include the variant and scope so the same output name from two
            # build trees remains addressable when manifests are combined.
            artifact_id = _artifact_id(session.variant, record.scope, record.path)
            enriched.append(
                replace(
                    record,
                    artifact_id=artifact_id,
                    target=target,
                    command=command,
                )
            )
        session.artifact_manifest = replace(manifest, artifacts=tuple(enriched)).validate()
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

    if session.backend == BACKEND_MAKE:
        plan = session.make_plan
        if plan is None or not plan.test_argv:
            return []
        result = _run_make_command(session, "make test", plan.test_argv, env)
        if result is None:
            return []
        if result.timed_out or result.truncated:
            reason = "timed out" if result.timed_out else "truncated its output"
            _fail(session, f"Make test command {reason}; test evidence is incomplete")
            return []
        output = result.stdout + result.stderr
        return _attach_sanitizer_output(parse_make_check_stdout(output, result.returncode), output)

    if session.backend == BACKEND_CMAKE:
        ctest_bin = _which(session, "ctest")
        if ctest_bin is None:
            return []
        argv, junit = cmake_test_argv(ctest_bin, session.shadow, session.cmake_version)
        if junit is not None:
            try:
                junit.unlink(missing_ok=True)
            except OSError as err:
                _fail(session, f"could not remove stale CTest JUnit report: {err}")
                return []
        result = run_process(argv, cwd=session.shadow, env=env)
        _record(session, "ctest", argv, result)
        if result.timed_out or result.truncated:
            reason = "timed out" if result.timed_out else "truncated its output"
            _fail(session, f"ctest {reason}; test evidence is incomplete")
            return []
        if junit is not None:
            report = _read_ctest_junit(junit, session.shadow)
            parsed = parse_ctest_junit(report) if report is not None else []
            if parsed:
                return _attach_sanitizer_output(parsed, result.stdout + result.stderr)
        return _attach_sanitizer_output(
            parse_ctest_stdout(result.stdout),
            result.stdout + result.stderr,
        )

    make_bin = _which(session, "make")
    if make_bin is None:
        return []
    argv = qmake_test_argv(make_bin)
    result = run_process(argv, cwd=session.shadow, env=env)
    _record(session, "make check", argv, result)
    if result.timed_out or result.truncated:
        reason = "timed out" if result.timed_out else "truncated its output"
        _fail(session, f"make check {reason}; test evidence is incomplete")
        return []

    output = result.stdout + result.stderr
    return _attach_sanitizer_output(_qmake_results(output, result.returncode), output)


def collect_coverage(session: BuildSession) -> Path | None:
    """Run gcov over the shadow tree. Must be called after run_tests."""

    gcov_bin = _which(session, "gcov")
    if gcov_bin is None:
        return None
    probe_argv = [gcov_bin, "--help"]
    probe = run_process(probe_argv, cwd=session.root)
    _record(session, "gcov capability", probe_argv, probe)
    json_capability = gcov_json_capability(probe)
    if json_capability is None:
        _fail(session, "gcov JSON capability probe was incomplete")
        return None

    out_dir, argvs = plan_gcov(session.shadow, gcov_bin, json_format=json_capability)
    if not argvs:
        _fail(session, "C++ gcov data files were unavailable")
        return None
    cleared, cleanup_error = _clear_owned_gcov_output(session.shadow)
    if not cleared:
        _fail(session, cleanup_error)
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    for argv in argvs:
        result = run_process(argv, cwd=out_dir)
        _record(session, "gcov", argv, result)
        if result.timed_out or result.truncated:
            _fail(session, "gcov output was incomplete")
            return None
        if result.returncode != 0:
            _fail(session, f"gcov failed with exit code {result.returncode}")
            return None
    pattern = "*.gcov.json.gz" if json_capability else "*.gcov"
    reports = sorted(out_dir.glob(pattern))
    if not reports:
        _fail(session, f"gcov produced no {'JSON' if json_capability else 'text'} reports")
        return None
    session.coverage_format = "gcov-json" if json_capability else "gcov-text"
    session.coverage_report_count = len(reports)
    return out_dir
