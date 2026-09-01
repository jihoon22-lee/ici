# Bounded C++ tool and test failure evidence corrections

## Overview

This work records the verified evidence boundaries for exact-context Qt/clazy diagnostics and
CMake/CTest failures. The documentation now distinguishes safe external source-preview validation,
atomic clazy process errors, and bounded sanitizer evidence from downstream toy-repository work.
No version metadata was changed.

## Context

Ubuntu 24.04 with Qt 5 and clazy 1.11 can emit a legacy macro note whose source preview is outside
the project root. The preview is useful only when it is validated against an include root present in
the exact sanitized compiler context. Separately, a nonzero clazy process is a tool failure even if
its output contains parseable-looking warnings, and CTest JUnit output must not make test reporting
an unbounded XML or stack-trace ingestion path.

## Changes Made

### Approved external source previews

- Documented that only explicit, sanitized compiler include roots (plus the project root) grant
  source-preview read authority; external diagnostic locations are exported as `[external]`.
- Recorded the maximum of 512 include roots, no-follow regular-file reads, before/after device,
  inode, size, and mtime identity checks, a 1,000,000-byte aggregate source-context budget, and an
  8,192-character line bound.
- Documented fail-closed behavior for root/file/identity violations, symlinks, source mismatches,
  forged or extra previews, and budget exhaustion.

### Selected GCC standard-library replay

- `_cpp_tooling` now verifies that the replay compiler is the capability-approved `g++` by resolved
  file identity, then runs two bounded include-search probes (`c++` and `c`) with only sanitized
  `-m*` and sysroot selectors retained.
- The C++ search result minus the C search result is projected in compiler order as
  `-nostdinc++` plus ordered `-isystem` pairs. Both clang-tidy and clazy consume this projection;
  probe records are emitted as `g++ stdlib include search` evidence and cached by compiler, working
  directory, selector, and replacement-sensitive file identity. Raw verbose compiler prose is
  discarded after parsing. C translation units bypass the C++ standard-library projection, and an
  in-flight compiler identity change fails atomically.
- A different compiler identity makes the projection inapplicable. For a matching GCC, bounded
  output, timeout, malformed search blocks, nonzero exits, and unresolved standard-library roots
  fail closed before a Clang-based analyzer starts.

### Atomic clazy and CTest evidence

- Documented that every nonzero clazy exit remains an atomic `ERROR` with no partial diagnostics.
  Its bounded evidence contains exit status, `fatal`/`error`/`warning`/`note`/`remark` counts, and
  processing/output flags; raw tool prose and host paths are not copied.
- Documented CTest JUnit reads through a stable regular-file/no-follow boundary capped at
  1,000,000 bytes, with bounded stdout fallback for unavailable, malformed, changed, or oversized
  reports.
- Documented bounded `LeakSanitizer`, `AddressSanitizer`, and `UndefinedBehaviorSanitizer`
  classifications without raw stacks or source paths; names and ordinary messages are capped at
  512 characters.

### Files updated

- `CHANGELOG.md` — Unreleased correction and verification boundary.
- `README.md` — clazy section and current evidence summary.
- `docs/user-guide.md` — clazy source-preview, process-error, and CTest/JUnit user contracts.
- `docs/engine-reference.md` — lint, test, and sanitize engine limits and classifications.
- `docs/architecture.md` — parser trust boundaries and CTest evidence flow.

## Code Examples

```text
exact sanitized compiler argv
  -> approved include-root projection
  -> no-follow regular-file + identity-checked source read
  -> exact legacy preview match
  -> project path or [external] target
```

```text
nonzero clazy / bounded JUnit sanitizer marker
  -> bounded classification
  -> atomic ERROR or test failure
  -> no raw tool prose, stack, or host path
```

## Verification Results

### Local Python 3.10 tests

```text
uv run --python 3.10 pytest tests/test_cpp_tooling.py tests/test_cpp_diagnostics.py \
  tests/test_clazy.py tests/test_build_adapter.py
161 passed in 1.33s

uv run --python 3.10 pytest
1,538 passed, 4 skipped in 60.31s

uv run --python 3.10 pytest tests/test_cpp_tooling.py tests/test_clang_tidy.py tests/test_clazy.py
71 passed in 0.25s
```

PR run `33536327520` then exercised the process-level tests on the hosted GCC 13/clang-tidy/clazy
image. All three analyzers completed successfully with the selected GCC 13 projection, but three
legacy assertions still required exactly one evidence record. The E2E contract now requires the two
ordered `g++ stdlib include search` records plus exactly one analyzer record and checks the probe
driver identity, language order, return code, timeout, truncation, and error fields.

### Exact tool evidence

The exact Ubuntu 24.04 + Qt 5 + clazy 1.11 run recorded 12/12 full-lint units, an accepted
targeted external macro note exported at `[external]`, and an unsuppressed CTest 8 run with 9 cases
including a LeakSanitizer diagnostic. Suppression work used for that downstream experiment belongs
to the toy repository only and must not be described as ici policy or an ici suppression contract.

The dual-GCC failure was observed in toy-projects PR #38 run `33531285208`, where Qt 5 and Qt 6 deep
checks selected the newest installed libstdc++ instead of the compile database's GCC. On Ubuntu 24.04
with GCC 13 and GCC 14 installed, the fixed local `dist/ici.pyz` projected
`/usr/include/c++/13`, `/usr/include/x86_64-linux-gnu/c++/13`, and
`/usr/include/c++/13/backward`; it recorded 2 probes, ran clazy at exit 0 for 12 sources, and kept
the expected warnings.

## Next Steps

- Keep downstream toy-project suppression notes separate from ici's tool-evidence policy.
- Preserve the exact external-root and sanitizer evidence when publishing future release or
  cross-repository validation results.
