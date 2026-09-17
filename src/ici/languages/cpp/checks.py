"""The C++ pack's checks.

``cpp.line`` is ici's own counter — it already reads ``//`` and block
comments, so it is as honest on a ``.cpp`` file as on a ``.py`` one.
``cpp.compile`` is the coverage check #212 asked for: it states which of the
component's translation units the compilation database has an invocation
for, and ``provides`` the evidence token the other compile-input checks
``needs``. ``cpp.diagnostics`` (#214) replays each captured compile command
as ``-fsyntax-only`` through the project's own compiler, and ``cpp.tidy``
replays the database through ``clang-tidy -p`` — both consume the coverage
the compile check publishes rather than inventing flags of their own.
"""

from __future__ import annotations

from ici.domain.enums import Profile
from ici.languages.checks import CheckDefinition

__all__ = ["CPP_CHECKS", "CPP_LINE_CHECK"]

CPP_LINE_CHECK = CheckDefinition(
    id="cpp.line",
    title="Line counts",
    language="cpp",
    tool=None,
)

#: Compilation coverage: which of the component's translation units the
#: database has an invocation for. ici performs the read itself — the check's
#: job is to state coverage, and a component with no database is *blocked*,
#: not passed: a partial or absent capture cannot stand in for a C++ verdict
#: (#212's acceptance criterion). It ``provides`` the evidence every other
#: compile-input check declares it ``needs``.
CPP_COMPILE_CHECK = CheckDefinition(
    id="cpp.compile",
    title="Compilation coverage",
    language="cpp",
    tool=None,
    provides=("compile-inputs",),
)

#: Compiler diagnostics: the captured compile invocation re-run as
#: ``-fsyntax-only`` per covered translation unit. The check declares no tool
#: because the tool is the project's own compiler, found per TU in the
#: database — ``ici.cli.next_common`` expands one declared check into one task
#: per TU so each run's argv is the invocation the build actually used (#214).
CPP_DIAGNOSTICS_CHECK = CheckDefinition(
    id="cpp.diagnostics",
    title="Compiler diagnostics",
    language="cpp",
    tool=None,
    needs=("compile-inputs",),
)

#: clang-tidy replayed through ``-p <build>``: the database applies each
#: file's own recorded invocation, so no flag transform is invented. The
#: check needs the coverage evidence first — a partial database is a partial
#: analysis, and cpp.compile is what reports it.
CPP_TIDY_CHECK = CheckDefinition(
    id="cpp.tidy",
    title="clang-tidy analysis",
    language="cpp",
    tool="clang-tidy",
    # Advisory, not required: a host without clang-tidy still deserves a C++
    # verdict from the compile evidence. ``[checks."cpp.tidy"] required =
    # true`` opts the component into treating its absence as incomplete.
    required=False,
    needs=("compile-inputs",),
)

#: ``tool=None`` because the runner is the build's own: ctest reads the
#: generated ``CTestTestfile``, a QTest binary is run as the artifact the
#: build produced. The check provides the test evidence coverage reads —
#: an instrumented suite's ``.gcda`` only exists after this run (#219).
CPP_TEST_CHECK = CheckDefinition(
    id="cpp.test",
    title="C++ test suites",
    language="cpp",
    tool=None,
    provides=("test-evidence",),
)

#: ``tool=None`` because ici measures these itself. C++ function boundaries
#: come from the heuristic brace scanner — honest about its limits: findings
#: it produces are ESTIMATED evidence, not MEASURED, because a macro-built or
#: preprocessed function can mislead it (#218 item 5).
CPP_COMPLEXITY_CHECK = CheckDefinition(
    id="cpp.complexity",
    title="Cyclomatic complexity",
    language="cpp",
    tool=None,
)

CPP_COGNITIVE_CHECK = CheckDefinition(
    id="cpp.cognitive",
    title="Cognitive complexity",
    language="cpp",
    tool=None,
)

#: Include cycles — resolved by path suffix, a heuristic, so its findings
#: are ESTIMATED evidence (#218 item 5).
CPP_CYCLE_CHECK = CheckDefinition(
    id="cpp.cycle",
    title="Include cycles",
    language="cpp",
    tool=None,
)

#: Type-2 clone detection over the component's C++ sources and headers —
#: tokenized, window-matched and clustered in-process (#218).
CPP_DUP_CHECK = CheckDefinition(
    id="cpp.dup",
    title="Duplicate code",
    language="cpp",
    tool=None,
)

#: gcov over the notes and data the instrumented build and the shared test
#: run left — this check never builds or reruns the suite itself.
#: Throwing destructors and silent catch-all blocks — the masked-literal scan
#: shared with the stable ``exception`` engine. Heuristic over stripped text,
#: so findings carry medium confidence (#218).
CPP_EXCEPTION_CHECK = CheckDefinition(
    id="cpp.exception",
    title="Exception safety",
    language="cpp",
    tool=None,
)

CPP_COVERAGE_CHECK = CheckDefinition(
    id="cpp.coverage",
    title="gcov coverage",
    language="cpp",
    tool=None,
    needs=("test-evidence",),
)

#: The build's declared output contract (#220): every glob the linked
#: ``[builds.<id>] artifacts`` lists must exist under the build directory.
#: Separated from build preparation — ici never builds; it verifies what the
#: project's own build left. Advisory by default because a component whose
#: builds declare no artifacts has no contract to break — the check is
#: blocked there, and ``required = true`` is how a workspace makes a broken
#: contract fail the gate.
CPP_ARTIFACT_CHECK = CheckDefinition(
    id="cpp.artifact",
    title="Declared build artifacts",
    language="cpp",
    tool=None,
    required=False,
    provides=("artifact-contract",),
)

#: Dynamic sanitizer runs (#220): the suite binaries a ``variant =
#: "sanitize"`` / ``variant = "thread-sanitize"`` build produced, executed
#: under the runtime options the sanitizer reads. ici never compiles the
#: instrumentation — the variant declaration is the project's claim, and the
#: plan gate verifies it by the markers the runtime leaves in the binary.
#: Deep profile only: they are the expensive dynamic checks a project opted
#: into, and a workspace with no such variant build reads blocked, never a
#: vacuous pass.
CPP_SANITIZE_CHECK = CheckDefinition(
    id="cpp.sanitize",
    title="ASan/UBSan/LSan suite run",
    language="cpp",
    tool=None,
    profiles=frozenset({Profile.DEEP}),
)

CPP_TSAN_CHECK = CheckDefinition(
    id="cpp.tsan",
    title="ThreadSanitizer suite run",
    language="cpp",
    tool=None,
    profiles=frozenset({Profile.DEEP}),
)

#: ELF/ABI evidence over the artifact contract (#220): one ``readelf`` read
#: per ELF binary the linked builds' ``artifacts`` globs name, judged by the
#: stable engine's default policy. ``tool=None`` because the binaries come
#: from the declared contract, not from a discovered tool argv — the plan
#: gate expands one declared check into one readelf task per ELF artifact
#: and blocks when readelf or a contract is absent. Advisory like the
#: contract check — a component with no declared artifacts has nothing for
#: it to inspect, and is blocked rather than silently passed.
CPP_BINARY_COMPAT_CHECK = CheckDefinition(
    id="cpp.binary-compat",
    title="Binary ABI compatibility",
    language="cpp",
    tool=None,
    required=False,
    needs=("artifact-contract",),
)

#: Declaration only. Importing this must not look at the machine.
CPP_CHECKS: tuple[CheckDefinition, ...] = (
    CPP_LINE_CHECK,
    CPP_COMPILE_CHECK,
    CPP_DIAGNOSTICS_CHECK,
    CPP_TIDY_CHECK,
    CPP_TEST_CHECK,
    CPP_COVERAGE_CHECK,
    CPP_COMPLEXITY_CHECK,
    CPP_COGNITIVE_CHECK,
    CPP_CYCLE_CHECK,
    CPP_DUP_CHECK,
    CPP_EXCEPTION_CHECK,
    CPP_ARTIFACT_CHECK,
    CPP_SANITIZE_CHECK,
    CPP_TSAN_CHECK,
    CPP_BINARY_COMPAT_CHECK,
)
