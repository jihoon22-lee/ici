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
```

### Exact tool evidence

The exact Ubuntu 24.04 + Qt 5 + clazy 1.11 run recorded 12/12 full-lint units, an accepted
targeted external macro note exported at `[external]`, and an unsuppressed CTest 8 run with 9 cases
including a LeakSanitizer diagnostic. Suppression work used for that downstream experiment belongs
to the toy repository only and must not be described as ici policy or an ici suppression contract.

## Next Steps

- Keep downstream toy-project suppression notes separate from ici's tool-evidence policy.
- Preserve the exact external-root and sanitizer evidence when publishing future release or
  cross-repository validation results.
