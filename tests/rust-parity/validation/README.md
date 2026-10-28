# Dynamic validation evidence

The Rust `ici-validation` crate runs declared project tools through the common
executor. The fixed reference product remains v0.13.0 source
`6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`. Current checkpoints cover Python
test/coverage/runtime/import and C++ suite/gcov/sanitizer/ELF/process contracts.
Complete CLI integration and full inventory closure remain separate work.

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

## Normalization and optional-capability audit

The latest scoped gate passed 12 general contracts, five C++ contracts, six gcov
tests, three runtime tests and two sanitizer tests. The sanitizer replay covers
55 cases captured from the fixed source (including every memory/thread defect
taxonomy entry and byte/frame/diagnostic limits). Source hash and capture command
are preserved in `sanitizer-oracle.json` and `capture_sanitizer_oracle.py`.

All eight native Python cases passed in 17.18 seconds; the import protocol's later
project-`json.py`/missing-newline correction passed separately in 0.92 seconds.
Actual ASan/UBSan/TSan replay after normalization changes passed in 0.75 seconds,
and native ELF policy after header capture passed in 0.19 seconds. Resolver tests
also cover project-local mutmut/cosmic-ray/mutpy precedence. Strict scoped
Clippy/fmt and the development capture script's Ruff checks passed.

- Optional mutation capability probes run the selected external command with
  bounded output/time. Missing, failed or timed-out capability leaves the base
  suite verdict unchanged. Every optional step's actual state, timeout,
  cancellation and truncation remain in `extra.optional_steps`. The native
  fixture exercises explicit project commands returning success/failure/timeout;
  it does not claim a particular mutator version or perform mutations.
- Runtime version predicates reuse the shared native PEP 440 implementation.
  `compileall` syntax and Sorry/indentation diagnostics retain their actual source
  line and message. Failed metadata does not erase independently obtained
  compilation findings.
- Sanitizer details preserve frame labels, project/observed counts and redacted
  external locations. Unknown external columns remain explicitly native metadata,
  rather than being published as guessed Unicode columns.
- Execution-phase binary header capture distinguishes non-ELF policy from
  readelf failure, honors `allow_non_elf` and `max_artifacts` (default 64), and
  rechecks artifact identity after inspection. Planning still works before the
  future build artifacts exist. Integration output bounds remain configurable
  with a 64 KiB default.
- Import-only protocol bookkeeping does not leave its own newly imported JSON
  helpers cached ahead of a declared project `json` module. Project initialization
  output without a trailing newline cannot corrupt the final JSON response.

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

## Native CLI runtime and release preparation foundation

The CLI now binds configured interpreter matrices before selecting a primary
runtime, applies required/optional interpreter policy to compile/import checks,
and resolves typed Python or existing declared artifact integration inputs.
Integration cases preserve their original grouped assertions, optional policy,
rich finding metadata, declared environment inheritance, exit contract, bounded
streams, and fresh output evidence. Existing user output is retained when the
freshness assertion fails. Python ResourceWarning checks use the project's pytest,
clear configured addopts, retain full traceback locations, and keep unrelated
pytest failures or entirely skipped scopes incomplete.

Measured CLI evidence on 2026-10-29 uses explicit Python 3.10.21 and 3.14.7:

- Six runtime/integration test functions passed, including missing required and
  optional interpreters, optional version incompatibility, import-only behavior,
  environment isolation, a declared nonzero exit, output freshness, optional
  case failures, and an existing native executable without a Python fallback.
- A separate actual pytest fixture passed clean, ResourceWarning, unrelated test
  failure, and entirely skipped scenarios. ResourceWarning retains its actual
  project file and line. No project bytecode is produced.
- Two ordinary CLI tests passed Rust release preparation: read-only planning,
  copied modules, generated callable launchers and environment files, verified
  ownership manifests, updates to unchanged owned files, preservation of user
  modifications and unknown existing files, and ancestor-symlink rejection.
- Strict all-target CLI/validation Clippy passed. The earlier validation library
  contract fixtures also passed after the named-case extension.

Release preparation reads immutable captured source bytes and writes in Rust;
it never invokes a package installer or a Python backend. The native prepare
operation is visible in plan output and remains uncached. Exact output hashes
and permissions back ownership proofs; only fully verified owned files can be
excluded from later broad source discovery. Unknown or modified files remain
visible. A new release version has a separate ownership record.

This foundation currently handles declared project versions. Visible Git version
fallback, broader discovery acceptance, actual generated launcher execution, and
legacy declared wheel/report publication are follow-up acceptance work; the
foundation evidence does not close product-wide parity.

The sanitizer framework extension distinguishes a CTest driver from the programs
it executes. A visible JSON inventory must name 1..2048 enabled tests whose direct
executables belong to the declared contained artifact inventory. Every declared
artifact has its own readelf instrumentation probe before the suite runs; a
wrapper, external command, unknown format, empty/disabled inventory, or missing
runtime symbol keeps the scope incomplete. QTest direct mode also validates its
framework totals. Actual test-driver output and sanitizer findings remain
separate evidence, and CTest names may contain spaces. The 15 scoped C++/GCOV/
sanitizer test functions passed together with strict validation Clippy; the
existing sanitizer functions include the 55-case frozen original normalizer
oracle. This is library contract evidence; actual CMake sanitizer/TSan CLI
execution is recorded separately by the C++ acceptance gate.

GCOV plans retain their exact tool/task bindings when old counters exist, but
execution readiness and observation reject those counters as potentially
accumulated evidence. The original files remain unchanged. This preserves the
independent test suite while requiring fresh coverage data before publishing
counts, and avoids making the mere appearance of newly produced counters look
like a changed executable/tool binding.

The following artifact acceptance then passed on 2026-10-29: four ordinary CLI
fixtures (including the frozen original launcher/environment content, broad root
scope, source retirement, version changes, user data, and version-probe policy),
actual generated project-callable launcher execution, and actual Git tagged /
non-repository fallback runs. Strict CLI/validation Clippy passed with these
changes. A Git probe is visible before native preparation, uses its resolved
executable identity, and resolves actual output paths before any release write.
Unavailable unconfigured Git or an actually failed/empty probe uses the historical
v1.0.0 default with explicit provenance. Broken explicit tools, changed executable
identity, malformed versions, and cancellation cannot silently select a default.
Unchanged retired release files keep separate ownership evidence and remain on
disk; only active artifacts are published in the current release manifest.
Legacy declared wheel/report publication and shared compiled-artifact export are
still separate follow-up acceptance work.

Native release publication now shares the Rust-owned copy/ownership transaction with Python
sources. `describe_compiled` accepts producer-measured canonical regular files, verifies their
SHA-256, byte size and permission mode, and places executables in `bin` and shared/static
libraries in `lib`. SONAME aliases become regular copies of the same captured bytes. No project
Python is required for a C++ only component. Existing unrecognized or modified destination data
is preserved. Compiled environment scripts add the release library directory to
`LD_LIBRARY_PATH`; the frozen Python-only environment/launcher bytes are unchanged.

`build.artifacts.python_wheel` and `build.artifacts.report` publish a separate `config.artifacts`
manifest with typed path, byte size, mode, SHA-256 and `release:project:<path>` identity. Existing
files remain unowned and are captured through canonical contained paths; aliases outside the
workspace and unmatched globs fail explicitly. Generated package paths may also match declared
globs. Git version resolution re-evaluates only frozen candidates plus planned generated paths,
without filesystem access. The inventories are bounded (32 globs per kind, 512 records, 32768
entries/depth 64); payloads are bounded at 256 MiB per file and 512 MiB aggregate. The plan exposes
its exact binary input set for the CLI to apply that bound separately from ordinary source limits.

Scoped evidence: `artifact-declarations-tests.log` passed the two ordinary declaration cases
(publish wheel/report/generated env, contained alias, unchanged ownership, revised user bytes,
missing/escaping artifact refusal). `artifact-compiled-native.log` passed actual GCC 15.2.0
executable/shared-library packaging and regular SONAME alias consumption, including execution,
producer-digest mismatch and output collision refusal. Git 2.53.0 was the earlier explicit version
probe tool. This does not substitute for root C++ build-context orchestration or full R8 closure.

The native CLI now binds explicit TEM/changed-line coverage policy and the deep test-quality
mutation capability probe, including custom argv and missing-tool behavior. Project test,
coverage and ResourceWarning suites receive the selected project interpreter directory on PATH
and configured source roots on PYTHONPATH; runtime/import-only probes retain their own isolation.
Mutation probes remain optional and never turn missing capability into a code defect. The
`repeat` and `slow_threshold` aliases use their configured values; inherited defaults are
normalized in the shared configuration reader before mutually exclusive alias validation.

`cli-quality-policy-native-final.log` passed real source-layout import and selected-runtime PATH
checks, custom mutation exit 7 with a passing base suite, changed-line coverage, and baseline
failure -> same-node skip -> completed pass. The skip retains the prior failure as carried; the
later pass resolves it. `cli-changed-tem-native.log` additionally passed the actual configured
TEM minimum and changed-line minimum together, preserving the uncovered source line. The
ordinary read-only alias/missing-probe case and all 31 validation contract tests passed in
`quality-scope-gates-final.log`, followed by strict CLI/validation Clippy.

CTest and QTest publish explicit per-node outcomes, including disabled/skipped cases. Their
shared GCOV and sanitizer evidence retains those node maps. `qtest-scope.json` freezes actual
Qt 5.15.18/6.10.2 output and its test-source SHA: XFAIL is an assertion annotation followed by
one terminal PASS, so the raw totals count that case once as passed while its baseline node
remains XFAIL. Contained `../main.cpp` locations from build directories normalize lexically;
external traversal and missing terminal evidence remain incomplete. `qtest-scope-final.log`
passed the seven C++ contracts and actual Qt 5/6 suites with failure, expected failure and skip
(4.92 seconds). The later complete validation gate includes the external-traversal regression.
These are scoped library/CLI acceptance results; historical baseline coverage regression policy
and full R8 inventory closure remain separate root-owned work.

Coverage outputs retain a typed `ici.coverage-policy` v1 snapshot with raw aggregate/file
denominators, explicit measured/empty/unavailable/not-selected axes, presentation percentages,
the configured statement floor and optional regression tolerance. Deserialization rejects
inconsistent counts/percentages and unknown contracts. Aggregate line counts reconcile to
file parts; empty or unmeasured axes never become invented zero-percent regressions.

The pure comparison helper produces source-located baseline-only/advisory findings, targets,
raw ratios and separate policy/gating/completeness flags. A drop must exceed the configured
tolerance. Fingerprints depend on component/provider/rule/path, preserving identity when the
measured drop changes. The application must first prove compatible native baseline and completed
matching check scope; ordinary source verification gates remain separate from fail-on-new.
Four pure comparison/validation cases and strict CLI/validation Clippy passed in
`coverage-baseline-gates-final.log`; the actual shared pytest/coverage case passed in 2.07s
(`coverage-baseline-native.log`). Root baseline/exit/report wiring has its own acceptance cases.

Python test/coverage plans now show a bounded coverage version probe and mutually exclusive
covered/plain-pytest suite branches. Only a completed exact missing-module diagnostic selects
plain pytest; broken, ambiguous, truncated or timed-out probes remain incomplete. Shared
coverage checks retain the actual suite identity and skip report generation when unavailable.
Missing coverage has no score or invented denominator/TEM evidence, while completed pytest
cases remain measured. Legacy coverage defaults to optional unless the historical engine policy
or an explicit per-check required setting requests it; modern next checks keep their defaults.

`validation-optional-cli-native.log` passed four actual CLI executions using an owned pytest-only
virtual environment: optional legacy coverage passed the selected gate, while required engine
policy, explicit check policy and modern next defaults remained incomplete. Actual pytest passed
in all four runs. `validation-optional-all-gates.log` passed all 38 validation contract cases,
11 doctor cases, eight ordinary extra CLI cases and strict CLI/validation Clippy. The three new
conditional-coverage contracts cover both branches, repeats, malformed probes and unavailable
raw policy evidence. These scoped results do not close the root R8/R9 inventory.
