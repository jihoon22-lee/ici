# Dynamic validation evidence

The Rust `ici-validation` crate runs declared project tools through the common
executor. The fixed reference product remains v0.13.0 source
`6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`. This checkpoint covers Python
test/coverage/runtime/import contracts; C++ suite/coverage, sanitizer, integration
and binary ABI validation remain subsequent work.

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
