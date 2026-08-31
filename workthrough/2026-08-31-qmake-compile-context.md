# I3-3 qmake exact compilation context

## Overview

I3-3 adds a bounded qmake preflight that captures the compiler invocations qmake
actually builds, then publishes them as the shared immutable compilation context.
This workthrough records the implementation present on `feat/qmake-compile-context`
and the limited local E2E evidence available for the slice. The current task changed
documentation only; it did not modify production or test files.

## Context

qmake does not emit `compile_commands.json`. Verbose or trace output is useful for
diagnosis but is not a stable compiler-argv contract, and replaying Makefile recipes
would cross the shell/execution trust boundary. A spike compared qmake verbose and
trace output, optional external capture tools, and a compiler wrapper. DiskMap also
showed that injecting wrapper text into the first qmake pass can collapse nested
`$$` expressions. The selected design therefore probes qmake's generated metadata
first, then performs a separate exact capture pass in an owned canonical shadow.

## Changes Made

### 1. Two-stage canonical qmake preflight

- `src/ici/core/qmake_context.py` is used only for qmake C/C++ projects that have no
  explicit or automatically discovered compilation database. An existing database
  remains authoritative.
- The preflight resets the owned `build/ici-qmake-build` shadow, runs one `-recursive`
  qmake configure, and reads the nested Makefiles to discover compiler metadata.
- It then runs a second `-recursive` configure in the same canonical shadow with the
  capture wrapper and the literal absolute compiler paths found in the probe.
- The adapter's normal deterministic qmake `make clean` is recorded after the second
  configure and before the wrapper build, preventing stale metadata and artifacts
  from being reused.

### 2. Bounded compiler metadata probe

- The first stage recursively reads `Makefile*` files without following symlinks.
- Exactly one consistent `CC`/`CXX` pair is accepted. Each value must be a single
  recognized gcc/g++/clang driver that resolves to an executable regular file.
- Whitespace or multiword values, conflicting declarations, unavailable drivers,
  symlinked metadata, and unsafe paths fail closed.
- Each Makefile is capped at 4 MiB and the traversal at 4,096 Makefiles, with an
  aggregate metadata limit as an additional bound.

### 3. Exact compiler-wrapper journal

- `src/ici/core/_qmake_wrapper.py` keeps the standalone wrapper source that the
  preflight materializes; `qmake_context.py` owns the capture lifecycle and row
  validation.
- The generated wrapper is pinned to the selected resolved `sys.executable` by its
  shebang and has mode 0700.
- For every compiler invocation containing `-c`, it records the exact post-wrapper
  `argv` and `os.getcwd()` in a JSONL journal, then calls the original compiler with
  `os.execvp`. ici does not parse a shell command or replay a captured recipe.
- The journal is limited to 32 MiB and 200,000 records. Writes use a no-follow
  regular-file descriptor, locking, ownership and permission rechecks; the journal
  starts at mode 0600.
- Rows are validated for strict JSON, bounded arguments, project/shadow containment,
  and exactly one source operand. Generated sources such as moc output remain valid
  when they are inside the owned shadow.

### 4. Database, diagnostics, and provenance

- `compile_commands.json` is written inside the owned shadow through a temporary file
  and atomic replace after the capture journal is validated.
- A missing production translation unit emits `qmake-capture-incomplete` instead of
  silently publishing an apparently exact context.
- Non-POSIX hosts do not attempt the capture and receive the explicit warning
  `qmake-capture-unsupported`, preserving a lower-confidence result. Other unsafe or
  unavailable capture paths likewise remain warning diagnostics.
- `CompilationContext` records `origin = "qmake"`, `generator = "qmake"`,
  `unity_build = null`, and capture diagnostics. The v3 schema accepts the qmake
  origin; compilation identity is `ici.compilation-identity/v2` and the cache key
  contract is `ici.analysis-cache-key/v2`.
- `VerifyOrchestrator` routes qmake projects to
  `prepare_qmake_compilation_context` and keeps the existing CMake preflight for
  CMake projects.
- The first packaged self-run showed that an inline backend conditional raised
  `VerifyOrchestrator.run_all` from complexity 25 to the fail threshold at 26.
  Dispatch now lives in a typed module-level helper, restoring the final packaged
  result to complexity 25/WARN without weakening the gate.
- qmake argv construction moved to `src/ici/core/_qmake_commands.py`; this reduced
  `cmake.py` from 512 to 495 code lines and removed the line warning introduced by
  the initial implementation.

## Code Examples

The effective two-stage sequence is:

```text
reset owned build/ici-qmake-build
qmake -recursive <absolute-project.pro>                 # metadata probe
read bounded Makefile* -> one safe absolute CC/CXX pair
qmake -recursive <absolute-project.pro> -after \
  QMAKE_CXX=<wrapper> <absolute-cxx> \
  QMAKE_CC=<wrapper> <absolute-cc>                      # capture configure
make clean                                                # recorded freshness step
make --jobs=N                                             # wrapper journals -c argv/cwd
atomic replace compile_commands.json
```

The wrapper contract is intentionally direct:

```text
if "-c" in sys.argv[1:]:
    append_jsonl({"arguments": sys.argv[1:], "directory": os.getcwd()})
os.execvp(sys.argv[1], sys.argv[1:])
```

## Verification Results

The final local evidence for this slice is:

- The real qmake fixture produced 3 compilation units on both Qt5 and Qt6,
  including the generated moc unit.
- Actual DiskMap Qt5 and Qt6 runs covered 20 configurations, included 9/9
  production units, produced no compilation diagnostics, and cleaned temporary
  capture shadows.
- Python 3.10: 1,112 tests passed in 52.96 seconds.
- Ruff check and format covered 134 files; focused mypy passed 7 source files.
- Two current-source pyz builds had identical SHA-256
  `5610617022a6accaf0b8fa0313ee0fd6c414317e839d23e2c879fa8b4c918d23`;
  packaging found 10 pure-Python distributions and no certifi.
- Smoke passed direct execution, Python 3.10 execution, artifact integrity, and
  Zero-CDN.
- Packaged self-verify returned WARN: 8 pass, 4 warn, 0 fail/error, 1 skip;
  1,112 tests; line/function/branch 88.8%/96.5%/79.9%; TEM 4.82;
  complexity 25; 117.25 seconds; line issues decreased from 10 to 9.
- The self HTML was 4,722,391 bytes, had a title, and contained zero external
  script/link/image dependencies.
- `git diff --check` returned exit 0.

## Next Steps

1. Open the PR and record its Merge Gate, sticky comment, and Pages/Zero-CDN evidence.
2. Complete the outstanding BuildScope target-by-target comparison.
3. Keep I3-4 lint/include-graph migration and the I3 checkpoint pending until their
   own implementation and evidence are complete.
