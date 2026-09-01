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
  - recognizes only a located, bounded empty clang-tidy note;
  - bounds the file field and validates line and column values against the existing diagnostic
    position limit;
  - omits the message-less structural record instead of manufacturing an empty finding;
  - preserves the following concrete note and assigns its parent clang-tidy rule;
  - rejects an empty note when the stream contains no diagnostic context.

### Regression coverage

- `tests/test_cpp_diagnostics.py`
  - covers the LLVM 18 warning/empty-note/concrete-note sequence;
  - covers an isolated empty note as an atomic error;
  - proves an out-of-range empty note cannot bypass the ordinary strict parser.

### Release notes

- `CHANGELOG.md` records the fix under `Unreleased`.

## Key Contract

```python
if text.empty_notes and not diagnostics:
    return "clang-tidy empty note has no diagnostic context"
```

This keeps the adapter strict: the recognized line is structural only when the complete stream
still contains a valid normalized diagnostic.

## Verification Results

### Focused verification

```text
50 passed, 1 skipped
Ruff check: PASS
Ruff format: PASS
```

The skip is the existing optional actual-tool E2E when clang-tidy is unavailable on the host.

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
