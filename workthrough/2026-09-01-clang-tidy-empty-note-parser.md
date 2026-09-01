# LLVM 18 clang-tidy Empty-Note Parser Fix

## Overview

BuildScope B4 verification exposed a fail-closed parser error in ici v0.9.0. LLVM 18 emitted a
located but message-less `note:` while explaining convertible adjacent parameters, and ici treated
the otherwise valid clang-tidy stream as unknown output. This change recognizes that one bounded
structural form while retaining atomic rejection for malformed or unaccounted output.

## Context

- toy-projects PR #36 run `33478407659` checked the released `ici.pyz` from v0.9.0.
- The BuildScope job completed 92/92 tests with 93.5% line, 99.0% function, and 77.3% branch
  coverage, but lint ended in `ERROR` on `src/core/compilation_model.cpp`.
- An unprivileged, temporary LLVM 18.1.8 extraction reproduced the relevant output:

```text
src/core/compilation_model.cpp:431:50: warning: ... [bugprone-easily-swappable-parameters]
src/core/compilation_model.cpp:431:50: note:
src/core/compilation_model.cpp:431:80: note: 'qsizetype' and 'int' may be implicitly converted
```

The temporary LLVM packages, CMake shadow, and captured output were deleted after reproduction.

## Changes Made

### Parser contract

- `src/ici/engines/_cpp_diagnostics.py`
- recognizes only a located, bounded empty clang-tidy note attached to the
  `bugprone-easily-swappable-parameters` primary at the same location;
  - bounds the file field and validates line and column values against the existing diagnostic
    position limit;
  - omits the message-less structural record instead of manufacturing an empty finding;
- requires the next located diagnostic to be the same-line concrete implicit-conversion note and
  assigns its parent clang-tidy rule;
- rejects unrelated, leading, trailing, repeated, out-of-range, mismatched-path, and wrong-child
  empty-note sequences atomically.

### Regression coverage

- `tests/test_cpp_diagnostics.py`
  - covers the LLVM 18 warning/empty-note/concrete-note sequence;
  - covers an isolated empty note as an atomic error;
  - proves an out-of-range empty note cannot bypass the ordinary strict parser.
- `tests/test_cpp_tool_e2e.py`
  - runs the exact swappable-parameter check through the approved real clang-tidy capability and
    sanitized compilation context;
  - requires the primary and concrete conversion findings with no parser or tool-evidence error.

### Release notes

- `CHANGELOG.md` records the fix under `Unreleased`.

## Key Contract

```python
if pending_empty_note and not _is_expected_conversion_note(
    pending_empty_note, diagnostic
):
    return _empty_note_error()
```

This keeps the adapter strict: the recognized line is structural only when the complete stream
still contains a valid normalized diagnostic.

## Verification Results

### Focused verification

```text
60 passed, 2 skipped
Ruff check: PASS
Ruff format: PASS
```

The skips are the actual-tool E2Es when clang-tidy is unavailable on the host. The new
swappable-parameter E2E is required, without skips, in repository CI.

### Full local gate

```text
Python 3.10 pytest: 1418 passed, 2 skipped
Ruff check: PASS
Ruff format: 153 files already formatted
mypy: no issues in 90 source files
build-pyz: PASS; all 10 distributions py3-none-any, two public schemas packaged
smoke: PASS; Python 3.10 launch, artifact integrity, and Zero-CDN HTML verified
```

## Next Steps

- Obtain a green ici PR run with the required installed clang-tidy E2E.
- Publish a patch release and independently audit its provenance, checksum, packaged version, JSON
  schema, and Zero-CDN reports.
- Pin toy-projects to that released asset and rerun PR #36, including sticky-comment and hosted
  HTML verification.
