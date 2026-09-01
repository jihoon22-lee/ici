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
src/core/compilation_model.cpp:431:60: note: the first parameter in the range is ...
src/core/compilation_model.cpp:432:54: note: the last parameter in the range is ...
src/core/compilation_model.cpp:431:50: note:
src/core/compilation_model.cpp:431:80: note: 'qsizetype' and 'int' may be implicitly converted: ...
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
- allows only the rule's bounded first/last-parameter child notes between the primary and the
  structural separator, then requires the next located diagnostic to be the same-line concrete
  implicit-conversion note and assigns its parent clang-tidy rule;
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

- `CHANGELOG.md` records the fix under `[0.9.1] - 2026-09-01`.

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
61 passed, 2 skipped
LLVM 18.1.8 process E2E: PASS
Ruff check: PASS
Ruff format: PASS
```

The skips are the actual-tool E2Es when clang-tidy is unavailable on the host. The new
swappable-parameter E2E is required, without skips, in repository CI.

The process E2E was also run against an unprivileged Ubuntu LLVM 18.1.8 extraction. Its real
warning, first/last child notes, empty structural note, concrete conversion note, generated count,
and source context all parsed in `exact` mode. The 356 MiB temporary extraction was removed after
this check.

### Failed remote iterations retained as evidence

- Run `33480708578` collected 1,431 tests and ended with 1,430 passing. Its first process fixture
  used all three parameters in one expression, so clang-tidy correctly suppressed the warning.
- Run `33480976522` again had 1,430 passing and one failed assertion. Separating the uses produced
  the primary warning, but primitive `long`/`int` output did not exercise LLVM's alias-expansion
  structural note.
- Replacing the fixture type with a local `qsizetype` alias reproduced BuildScope's exact LLVM
  output. This also exposed and fixed the necessary state transition across the rule's legitimate
  first/last-parameter notes.

### Full local gate

```text
Python 3.10 pytest: 1429 passed, 3 skipped
Ruff check: PASS
Ruff format: 153 files already formatted
mypy: no issues in 90 source files
build-pyz: PASS; all 10 distributions py3-none-any, two public schemas packaged
smoke: PASS; Python 3.10 launch, artifact integrity, and Zero-CDN HTML verified
```

## Final remote, release, and cross-repository evidence

The parser fix was merged through [ici PR #119](https://github.com/jihoon22-lee/ici/pull/119)
as `74030248345d61c6a394634a9ad9c19b7da4323d`. Exact ici-main [run
33482849448](https://github.com/jihoon22-lee/ici/actions/runs/33482849448) passed all required
checks. Release preparation [PR #120](https://github.com/jihoon22-lee/ici/pull/120) produced
exact main `d6022f613bd997eb557e6af860f5e9b7c6639327`; exact-main [run
33484388337](https://github.com/jihoon22-lee/ici/actions/runs/33484388337) was also green.

The annotated `v0.9.1` tag object is `ebce6307ff51ba14dfb2368f9807ecd24b544578`, dereferencing
to `d6022f613bd997eb557e6af860f5e9b7c6639327`. Release workflow
[33484950163](https://github.com/jihoon22-lee/ici/actions/runs/33484950163) passed provenance and
publish validation. The public release is non-draft/non-prerelease and contains exactly nine
independently audited assets; the packaged `ici.pyz` is 2,181,513 bytes with SHA-256
`8668af0eddf117d31e99e25cff4f64b1da68fb5e6d41fb01ef5c9d8107542284`, reports `ici 0.9.1`, and
the checksum, both `ici.result/v3` JSON reports, Zero-CDN HTML reports, and static `icirv` checks
passed. The complete asset table is kept in the current release section of the ici handover.

The released asset was pinned by toy-projects [PR #36](https://github.com/jihoon22-lee/toy-projects/pull/36).
Its final head `68ae3b59aacfbd5c57bde2a88718641cd1cfb9e0` passed all 16 checks in [run
33487556779](https://github.com/jihoon22-lee/toy-projects/actions/runs/33487556779). The sticky
comment retained exactly one marker, the current run, and three report links. BuildScope, DiskMap,
and LogLens hosted Pages returned HTTP 200 with exact titles and zero external references; their
audited sizes/digests are recorded in the handover. PR #36 was squash-merged to toy main
`590899a0a9430e9ce35162b301bfef5d7dfc78a4`, and its feature branch was deleted. Exact toy-main
[run 33488169769](https://github.com/jihoon22-lee/toy-projects/actions/runs/33488169769) passed
14 prerequisite jobs and Merge Gate (the PR-only publisher was correctly skipped), while
[Dependency Graph run 33488174425](https://github.com/jihoon22-lee/toy-projects/actions/runs/33488174425)
also passed.

This closes the B4 precondition for ici. I4-1 and its v0.9.1 release boundary are complete; the
next active stage is I4-2 (Qt clazy and generated moc/uic/rcc analysis). I4-2/I4-3/I4-4 and the
overall I4 checkpoint remain incomplete.

## Next Steps

- Start I4-2 on `feat/qt-analysis`, beginning with capability/profile contracts and real Qt5/Qt6
  evidence.
- Validate clazy diagnostics, Q_OBJECT/lifetime findings, and moc/uic/rcc generated artifacts
  through BuildScope and the existing Qt applications before marking I4-2 complete.
