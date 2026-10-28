# Dynamic validation evidence

The Rust `ici-validation` crate runs declared project tools through the common
executor. The fixed reference product remains v0.13.0 source
`6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`. Current checkpoints cover Python
test/coverage/runtime/import and C++ suite/gcov/sanitizer/ELF/process contracts.
Complete CLI integration, mutation capability probes and full
inventory closure remain separate work.

## Python checkpoint (2026-10-29)

```sh
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  cargo test --locked -p ici-validation -- --test-threads=2
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  ICI_TEST_PYTHON=/absolute/project/.venv/bin/python \
  cargo test --locked -p ici-validation --test native_python -- --ignored --test-threads=1
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  cargo clippy --locked -p ici-validation --all-targets -- -D warnings
cargo fmt --all -- --check
```

All eight contract tests and six explicitly invoked native tests passed. The
native run took 11.07 seconds using project CPython 3.10.21, pytest 9.1.1, and
coverage.py 7.15.4. Missing `ICI_TEST_PYTHON` or its pytest/coverage dependencies
fails the explicit gate. The ordinary command lists native tests as ignored;
that alone is not native execution evidence.

- Plan is read only and lists each process, output, timeout and dependency.
  One `coverage run -m pytest` suite can feed separate test and coverage logical
  checks. Pytest exit 1 unlocks coverage collection; zero tests, collection errors,
  timeout, truncation and cancellation do not. Native pytest configuration stays
  in effect, while pytest cache and bytecode writes are disabled.
- A new `OwnedRun` capability rejects preexisting directories and retains its
  directory descriptor/identity. Artifact reads reject symlink ancestors/leaves,
  FIFO/nonregular inputs, hardlinks, stale timestamps, oversized/changing files
  and replaced run directories. Neither a saved report nor an old data directory
  can satisfy a fresh run. These checks preserve existing user files.
- JSON coverage counters are nonnegative integers; file line/branch parts and
  totals must agree. Missing declared sources do not become zero or full
  coverage. Subset denominators come from raw counts after validating the full
  report. Percentages are recalculated, never averaged across components.
- TEM retains `tem/1`, its 80% cap and historical branch factor 1.25. Missing
  function evidence leaves the score N/A. Zero tests cannot earn full marks.
- Runtime version/platform/sysconfig and explicitly declared import-only probes
  use the fixed ADR-0008 scripts with bounded output/time. Import probes execute
  module initialization but never invoke module main. Project compilation uses
  the project's external `compileall`, with bytecode isolated inside the owned
  run directory. No Python program computes ici AST/rules/TEM.
- Opt-in bounded repeat/duration observations preserve source locations and
  distinguish advisory report mode from warn mode. Total repeat runs include
  the primary suite and never exceed three. Each repeat depends on the previous
  completed suite, so repeated tests cannot overlap.
- Coverage policy accepts explicit line/file/branch/function/TEM minima, per-file
  statement floors and bounded changed-line ranges. Every evaluated policy emits
  a source target, and breached minima emit measured findings. An unavailable
  configured denominator remains incomplete. Modern evidence-only callers leave
  minima absent; legacy callers pass their resolved defaults explicitly.

## C++ and process-contract checkpoint (2026-10-29)

Nine general contract, four C++ replay and six gcov parser/frozen producer tests
passed. All eight explicitly invoked native C++ tests passed in 13.80 seconds;
the subsequent gcov TEM/Unicode/freshness follow-up passed separately in 0.84
seconds. Strict all-target validation Clippy and scoped provider/validation
formatting passed. A workspace formatting check during concurrent work observed
an unrelated unfinished `ici-analysis/src/unicode.rs` edit; that workspace check
is not recorded as passed here. An earlier full-test link hit the full `/tmp`
tmpfs and failed with an LLVM linker bus error; the agent-owned target cache was
moved to workspace disk and the failed test gate was rerun successfully.

```sh
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  ICI_TEST_GXX=/absolute/g++ ICI_TEST_GCOV=/absolute/gcov \
  ICI_TEST_CTEST=/absolute/ctest ICI_TEST_QMAKE=/absolute/qmake \
  ICI_TEST_QMAKE6=/absolute/qmake6 ICI_TEST_MAKE=/absolute/make \
  ICI_TEST_READELF=/absolute/readelf \
  cargo test --locked -p ici-validation --test native_cpp -- --ignored --test-threads=1
```

Tested tools: GCC/gcov 15.2.0, CTest 4.2.3, GNU readelf 2.46, Qt 5.15.18 and
Qt 6.10.2. The native tests actually compile and run ASan heap-use-after-free,
UBSan signed overflow and TSan data-race programs. They also run CTest
pass/fail/disabled/empty fixtures, Qt5/Qt6 QTest failures with source locations,
ELF RPATH policy, a declared process contract with output artifacts, and a
non-instrumented binary which must never execute under a claimed sanitizer check.

- A readelf symbol-table proof precedes sanitizer execution and the executor
  revalidates the executable's digest. A linked runtime does not prove every
  translation unit was instrumented. Runtime failure/incomplete reports remain
  incomplete; parsed defects preserve bounded frames, source positions and
  external-frame redaction. The source index supplies Unicode display columns.
- GCC JSON/gzip parsing rejects unknown/duplicate fields, wrong scalar types,
  invalid geometry/counts, partial prime-path metadata, missing v2 block IDs,
  malformed/concatenated/trailing gzip and excessive bytes/records. It retains
  branches, calls, conditions and prime-path structures. Each report is limited
  to 8 MiB compressed / 64 MiB decompressed; all reports together to 64/256 MiB.
- A shared CTest/QTest suite feeds gcov only after complete nonempty evidence;
  assertion failures may still feed coverage. Its `.gcda` must have been written
  after the actual suite start. Missing/stale/mismatched data cannot become a
  measured zero. Report files are captured from the fresh owned directory.
- Cross-unit merging uses raw line, function geometry and branch block identity
  with OR coverage, preserving template instances while excluding compiler
  exception-unwind edges from both counts. Declared sources cannot disappear.
  `gcov-native.json` preserves actual GCC output with only its temporary root
  normalized, including measured byte-column 13 → Unicode-column 10 conversion.
- ELF interpretation and ABI policy reuse `ici-cpp` facts. No binary is loaded
  to inspect it. Process contracts separately evaluate declared exits, stream
  assertions and fresh regular output-file metadata without reading or deleting
  user output contents. Every validation step declares component resource locks.
- A measured empty coverage axis is N/A, with no invented 0%/100%. Missing
  function evidence is distinct and cannot satisfy a configured policy or TEM.

## Runtime matrix and artifact-phase follow-up

Eleven general contracts, two matrix replay tests and all seven native Python
tests passed; the native Python gate took 13.37 seconds. The matrix additionally
used an explicitly selected CPython 3.14.7 alongside project CPython 3.10.21.
Strict all-target validation Clippy passed.

Configured runtimes have separate visible metadata/compile/import tasks and
isolated bytecode directories. Required names must form a unique subset of the
configured matrix. Missing optional interpreters produce warning targets and
limitations; required missing/failed runtimes remain incomplete. Optional
compatibility findings keep measured evidence and advisory policy tags. No
core-interpreter fallback occurs. The native matrix proves that Python 3.10
rejects a `type` statement which Python 3.14 accepts without executing its main.

Artifact captures now require each producer's actual start marker. A later gcov
phase sharing an owned run directory excludes earlier producers' report files.
An explicit two-producer fixture verifies that a fresh first-phase report cannot
be adopted by the second phase. Older coverage JSON without per-function sections
can still measure native multiline body hits; definition/header-only hits remain
insufficient for one-line functions.

## Confirmed function-coverage correction

The old body-range rule could count `def never(): pass` as called just because
module initialization executed its definition line. Actual coverage.py 7.15.4
JSON also puts that header in the function's `executed_lines`; function metadata
alone therefore does not fix it. `function-invocation.json` preserves actual
one-line and decorated producer reports, normalizing only temporary source paths
and timestamps. Native function body hits or function exit arcs supply invocation
evidence. Exit arcs use the earliest decorator line (the code object's entry),
which differs from the displayed def line. Unknown invocation evidence leaves
the function denominator unavailable instead of inventing measured coverage.

These library fixtures do not establish full CLI, packaging or R6/R8 closure.
