# Bounded heuristic source evidence for dead and duplicate analysis

## Overview

The rebased `fix/source-analysis-evidence` branch gives the `dead` and `dup` engines one bounded,
stable source-intake contract. This documentation follow-up records the ownership policy, UTF-8
and containment boundary, resource limits, evidence semantics, and the remaining I4-3 work
without treating heuristic analysis as exact. The implementation history is intentionally
referred to by branch and change description rather than by ephemeral pre-rebase commit IDs.

The current version remains `0.10.2`. This is a documentation follow-up for open PR #133; the
implementation workflow evidence is recorded below, but the PR remains unmerged. The documentation
follow-up still requires its own CI and merge-gate confirmation. No release or version bump is
claimed.

## Context

Before this slice, the two heuristic engines opened their inputs independently. That left their
read/error behavior, source scope, ordering, and location evidence easier to disagree about. The
shared intake now makes the source snapshot a deterministic boundary before AST or token analysis:

- selected paths are lexically normalized without resolving symlinks, made project-relative,
  deduplicated, and sorted in deterministic lexical order;
- the intake accepts at most 8,192 unique candidate paths and, after the ownership policy is
  applied, at most 2,048 owned/analyzed source files;
- regular-file/no-follow bounded reads produce strict UTF-8 snapshots; NUL text is rejected;
- generated/autogen/moc and common vendor/dependency paths are excluded by default;
- policy-excluded files do not consume the owned/analyzed-file cap;
- missing, escaped, symlinked, malformed, unsupported, or oversized input fails closed; and
- downstream engine results retain source counts, byte counts, exclusion counts, and provenance.

## Changes Made

### 1. Shared source intake and ownership policy

File: `src/ici/engines/_source_inputs.py`

`read_analysis_sources()` is shared by both engines. Its limits are 8,192 unique candidate paths,
2,048 owned/analyzed source files, 8 MiB per file, and 64 MiB aggregate source bytes. Candidate
paths are normalized and deduplicated before the candidate limit is applied. The owned-file limit
is applied only after generated/vendor policy filtering, so excluded files do not consume it.

Generated/autogen directories and generated names such as `moc_`, `qrc_`, `ui_`,
`mocs_compilation`, and `.moc`, together with common vendor/dependency directories, are excluded
unless explicitly enabled. Owned C/C++ headers (`.h`, `.hh`, `.hpp`, `.hxx`) are discoverable by
`dup`; a standalone `.moc` is discoverable but classified as generated and therefore remains
excluded unless `include_generated = true`.

The `include_generated` and `include_vendor` switches are independent. A path classified as both
generated and vendor is included only when both switches are literally `true`; direct-engine
values such as the string `"true"` do not enable either policy. The `excluded` collection contains
one record per unique excluded path. Its reason counters intentionally count each blocking reason,
so a dual-classified path can contribute to both `generated` and `vendor` while the excluded-file
count remains one.

The bounded reader uses a no-follow descriptor. On platforms without directory-relative open
support it first prechecks every path component for symlinks. It verifies descriptor identity and
performs a second content read, rejecting a changed file instead of accepting a mixed-generation
snapshot. Injected limits must be positive integers; booleans, null, zero, negative, and other
malformed values fail closed with `invalid-limit`.

### 2. Dead-code evidence

Files: `src/ici/engines/dead.py` and `src/ici/core/project.py`

Python source discovery is captured once per `dead` run and the captured list is reused for source
directory precedence ordering and intake. This avoids a second discovery observing a different
filesystem state.

Python AST reachability and name-reference analysis consumes the shared snapshot. A clean source
file receives a PASS location target. Because the analysis is heuristic, an executed result is
reported as `EvidenceState.ESTIMATED` with `analysis_provenance = "python-ast-heuristic"`.
Intake or parse failures become located `ERROR`/`NOT_RUN` evidence instead of silently dropping a
file.

Compiler/linker/clang-tidy-backed exact dead-symbol evidence remains a pending I4-3 item.

### 3. Duplicate-code evidence

Files: `src/ici/engines/dup.py` and `src/ici/core/project.py`

The duplicate engine indexes the shared snapshots in stable path order. Python and C/C++ windows
are keyed by language before matching, so equal normalized token shapes across languages do not
become a clone. Clone groups record the stable `sha256/type2-region-v1` fingerprint and analyzed
files without a reported clone receive a PASS `DuplicateScan` target. The result remains
`EvidenceState.ESTIMATED` with `analysis_provenance = "token-region-heuristic"`.

Owned C/C++ headers are included in the project discovery inventory, including headers under the
configured source directories and the project `include` directory. Standalone `.moc` files are
discoverable so the generated policy can report and gate them; they are excluded by default and
require the literal `include_generated = true` opt-in.

Robust language tokenization and a complete language-semantic duplicate detector remain pending;
the full duplicate roadmap item is intentionally not marked complete.

### 4. Configuration and regression coverage

Files: `src/ici/config.py`, `src/ici/config_schema.py`, and
`tests/test_source_analysis_inputs.py`

The implementation commits add both inclusion switches to the shipped defaults and schema
validation, requiring TOML booleans. The focused regression module covers resource bounds,
strict UTF-8/NUL handling, lexical normalization and deterministic ordering, symlink/parent
traversal, generated/vendor defaults and independent opt-ins, unique exclusion accounting,
located PASS and error targets, estimated evidence, stable fingerprints, language isolation,
single dead discovery, owned header and `.moc` discovery, literal-boolean direct config behavior,
and invalid injected bounds.

### 5. Documentation synchronization

The following repository-relative files were updated to describe the same contract:

- `CHANGELOG.md`
- `README.md`
- `docs/architecture.md`
- `docs/engine-reference.md`
- `docs/user-guide.md`
- `docs/superpowers/2026-08-30-handover.md`
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- `workthrough/2026-09-02-source-analysis-evidence.md`

The plan and handover retain the distinction between this bounded heuristic slice and future exact
dead-symbol analysis or robust duplicate tokenization. They also retain the fact that this slice
does not bump the version or create a release.

## Verification Results

The following final local checks were run in this checkout with Python 3.10. The complete local
gate is green; the suite-level WARN consists only of the repository's documented heuristic and
tool-availability findings, not a required-engine failure:

```text
uv run --python 3.10 pytest tests/test_source_analysis_inputs.py
79 passed in 0.26s

uv run --python 3.10 pytest tests/test_source_analysis_inputs.py tests/test_config.py tests/test_dead_engine.py tests/test_dup_policy.py
238 passed in 0.47s

uv run --python 3.10 ruff check src tests/test_source_analysis_inputs.py tests/test_compile_context.py
All checks passed!

uv run --python 3.10 ruff format --check src tests/test_source_analysis_inputs.py tests/test_compile_context.py
99 files already formatted

uv run --python 3.10 pytest -q
1764 passed, 2 skipped (1766 collected)

uvx ruff check .
All checks passed!

uvx ruff format --check .
167 files already formatted

uv run --python 3.10 mypy src/ici
Success: no issues found in 98 source files

./scripts/build-pyz.sh  # run twice
both builds: 2240881 bytes, SHA-256
715bddd5d76540f97d6f78c9349a5177ce5935a80925a5761ea39fb0988d9b0d

./scripts/smoke.sh
PASS (artifact, launcher, Python 3.10 compatibility, packaged verify, and Zero-CDN checks)

source self verify with a temporary HTML output:
ici verify --no-cache --report --html <temporary-report>.html
exit 0; suite WARN; Pass 7, Warn 5, Fail 0, Error 0, Skip 1; 1764/1766 tests passed;
TEM 4.84; line/function/branch 89.1%/96.8%/81.5%; HTML 7763578 bytes;
title `ici Verification Report — ici`; external resource references 0
```

The focused source-input module collected and passed 79 tests. The directly selected adjacent
`config`/`dead`/`dup` bundle collected and passed 238 tests in this checkout. The full Python 3.10
suite is green at 1764 passed and 2 skipped out of 1766 collected. The two package builds are
byte-identical, packaged smoke passes, and the source self-verify exits 0 with the expected WARN
suite status.

The implementation PR is [#133](https://github.com/jihoon22-lee/ici/pull/133), titled
`fix(analysis): make heuristic source evidence bounded and deterministic`. Its first
[workflow run `33605000619`](https://github.com/jihoon22-lee/ici/actions/runs/33605000619) passed
all required checks: `Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`,
`Publish PR Report & Sticky Comment`, and `Merge Gate`. The [sticky comment](https://github.com/jihoon22-lee/ici/pull/133#issuecomment-5506324653)
contains exactly one `github-actions` marker/current-run comment.

The extracted artifact HTML and PR Pages are byte-identical. Independent Pages checks also found
UTF-8 exact titles and zero external resource URLs:

| Report | HTML bytes | SHA-256 | Pages/title |
|---|---:|---|---|
| ici | 7,701,814 | `071d83ef1fac4d39102bcb8eecad68d614dda736d74a6b3a93b210c9feecf38b` | [ici PR Pages](https://jihoon22-lee.github.io/ici/ici/pr/133/) — `ici Verification Report — ici` |
| viewer | 358,047 | `9e7e295e8d28fe0633039f58099c82a5914d30cb6fcd8c9f2ba82d25e84c4305` | [viewer PR Pages](https://jihoon22-lee.github.io/ici/viewer/pr/133/) — `ici Verification Report — viewer` |

PR #133 remains open and unmerged. The documentation follow-up's CI and Merge Gate are still
pending; the version remains `0.10.2` and no release is created.

`git diff --check` is run after the documentation edits as the final whitespace gate.

## Next Steps

- Add compiler/linker-backed exact dead-symbol evidence before changing the dead-code status.
- Replace the remaining heuristic duplicate tokenizer with robust language-aware tokenization.
- Revisit the I4-3 aggregate only after the required local, real-tool, cross-repository, and remote
  evidence gates are independently available.
