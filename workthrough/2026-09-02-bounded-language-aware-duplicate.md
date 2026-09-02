# Bounded language-aware duplicate tokenization

## Overview

This workthrough records the local duplicate-analysis slice that adds language-aware lexical
normalization and bounded Type-2 clone matching for Python and C/C++. It is deliberately narrower
than the I4-3 checkpoint: the lexical/token-region sub-scope is locally implemented and tested,
while compiler/linker-backed exact dead-symbol evidence and full duplicate semantic analysis remain
pending.

The ici version remains `0.10.2`. This slice has no release, PR, CI, main-branch, Pages, or hosted
artifact evidence; all results below are local evidence only.

## Context

The previous duplicate path needed a deterministic, language-separated representation before it
could compare source regions. A raw line or cross-file general-purpose sequence comparison would
mix Python and C/C++ syntax, cross structural boundaries, and make repeated low-information data
look like actionable code. The implementation therefore establishes a bounded source snapshot,
language-specific lexical records, hard region keys, and shared actionable window seeds.

## Changes Made

### 1. Language-specific lexical records

The duplicate engine now keeps Python and C/C++ records under separate language keys while retaining
physical line locations:

- `src/ici/engines/_python_dup_tokenization.py` uses Python tokenization plus bounded AST context.
  It preserves syntax-significant keywords and API/attribute anchors, distinguishes literal classes,
  normalizes ordinary identifiers for Type-2 matching, and keeps malformed input deterministic.
- `src/ici/engines/_cpp_dup_tokenization.py` provides a line-preserving C++ lexer. It handles
  translation-phase line splicing, comments, literals, punctuator maximal munch, directive input,
  and language/API anchors without pretending to be a preprocessor or compiler parser.
- `src/ici/engines/_dup_regions.py` assigns Python function/class/import segments and C/C++
  function/preprocessor segments. Blank and comment physical gaps may occur inside a match, but a
  match cannot cross its region key.
- `src/ici/engines/_dup_signal.py` rejects windows made only from low-information identifiers,
  punctuation, or literal/data-table shapes unless the normalized lines provide actionable signal.

The exposed tokenizer metadata is `python-lexical-v1` and `cpp-lexical-v1`; the region policy is
`language-function-scope-v1`, and the signal policy is `minimum-semantic-lines-v1`. These are
lexical/heuristic evidence contracts, not compiler-backed semantic analysis.

### 2. Shared bounded seed matching

`src/ici/engines/_dup_matching.py` implements the current internal API:

```python
find_raw_matches(files_data, window_size, limits)
_window_hashes(indexed, window_size)
```

The matcher indexes shared normalized window seeds with a rolling hash, verifies candidate windows
with actual normalized equality, and then extends left and right only while language and region
constraints remain valid: candidates are language-isolated and each occurrence must remain inside
its own region key. The two occurrences do not need to have identical region identifiers.
Overlapping seeds are covered and deduplicated into maximal matches. It does not use
`difflib.SequenceMatcher` for cross-file matching. Comparison, occurrence, pair, extension, and raw
match budgets fail closed through `DuplicateComparisonLimit` when they are exceeded. Direct
tokenizer `max_tokens` validation raises `ValueError`; the engine wraps tokenizer failures, and its
normalized-character and aggregate indexed-record bounds, as a located `SourceTokenizationError`.
Neither path returns partial PASS evidence.

The regression contract also forces `_record_hash` collisions and confirms that distinct
normalized windows still do not match. A window larger than the indexed input returns no hashes
without attempting work proportional to the requested window size.

The first packaged self-analysis exposed critical cyclomatic complexity in the new orchestration
functions (39 and 26). Commit `62aa740` split window indexing, comparison budgets, seed iteration,
extension, location projection, and Python canonicalization into independently testable stages.
The source-checkout complexity command then returned maximum 25 instead of accepting the critical
self-dogfood failure.

### 3. Engine evidence and fixtures

`src/ici/engines/dup.py` wires the common bounded source intake to the language tokenizers and
regions, preserves generated/vendor exclusion policy, and emits per-file PASS locations. A
successfully completed analysis reports `language-lexical-region-heuristic` provenance with
`ESTIMATED` evidence; bounded failures return `NOT_RUN`. Clone fingerprints use
`sha256/type2-region-v2`.

The focused fixtures cover Python and C/C++ syntax normalization, literal/API anchors, line
splicing and directives, hard scope barriers, low-information data, shared-seed extension and
deduplication, forced hash collisions, huge windows, deterministic comparison limits, and the
aggregate indexed-record limit. Fixtures are intentionally small and do not use wall-clock timing
assertions.

## Local Verification

The following measurements are local only; no remote acceptance is inferred from them. Test commands
disable repository-level coverage addopts so their pass counts describe the selected regression
inventory exactly:

```bash
uv run --python 3.10 pytest -q -o addopts='' tests/test_dup*.py
uv run --python 3.10 pytest -q -o addopts='' \
  tests/test_dup*.py tests/test_source_analysis_inputs.py \
  tests/test_cache_identity.py tests/test_cache_store.py
uv run --python 3.10 pytest -q -o addopts='' -rs
uv run --python 3.14 pytest -q -o addopts='' \
  tests/test_dup_python_tokenization.py tests/test_dup_python_syntax_edges.py \
  tests/test_dup_token_limits.py
```

| Check or project | Result | Evidence class |
|---|---:|---|
| Focused duplicate tests | 120 passed | local |
| Source/cache-inclusive focused bundle | 250 passed | local |
| Full Python 3.10 suite | 1,879 passed / 7 environment skips | local |
| Python 3.14 Python-focused suite | 45 passed | local |
| DiskMap duplicate rate / groups | 1.7% / 10 | local |
| BuildScope duplicate rate / groups | 6.0% / 60 | local |
| LogLens duplicate rate / groups | 0.6% / 3 | local |
| ici duplicate rate / groups | 10.4% / 403 | local |

The final local toolchain was Python 3.10.21 and Python 3.14.7, Ruff 0.16.5, mypy 2.3.1,
and uv 0.12.5. The final Python 3.10 run completed in 63.94 seconds. Its seven skips were five
clang-tidy cases, one clang++/clazy case, and one clang++ cycle-context case unavailable in this
host environment. Ruff check and format covered 182 files, and mypy reported no issues in 103
source files.

Two consecutive package builds were byte-identical at 2,257,882 bytes with SHA-256
`cd4252f83933072170eaf32e3c9f5cea372f9e6db766d91cdc6529157d10007b`. The build verified ten
`py3-none-any` distributions, no certifi, and two packaged public schemas. `./scripts/smoke.sh`
passed launcher, Python 3.10 direct execution, artifact integrity, and Zero-CDN checks; its packaged
self-verification exited 0.

An independent packaged `verify --no-cache --report --html ...` at source commit
`62aa7401116e57eaf00700bf13c649dc8fcb044a` also exited 0 with suite `WARN`: 8 PASS, 4 WARN,
0 FAIL, 0 ERROR, 1 SKIP, TEM 4.84, and 0 cache hits. The test engine recorded 1,879/1,886,
line/function/branch coverage 89.3%/97.1%/81.7%, and the complexity engine returned maximum 25.
The duplicate result was `ESTIMATED`, 10.4%/403 groups with
`language-lexical-region-heuristic`, v2 fingerprint, both lexical-v1 tokenizers, and the documented
region/signal policies. Its v3 JSON was 14,474,692 bytes with SHA-256
`abd37281efcc6ad5f85c92026c9edfbfeaeee7f6a1c468f7cd1b317b6c70567d`; the HTML was 4,945,918
bytes with SHA-256 `48b5a9e985ccf9215fcf318d10f4643e83694ae9d22c63eed3416c7c3a856791`, exact UTF-8 title
`ici Verification Report — ici`, and zero external executable/display assets.

### Clean toy-project cross-check

The candidate was injected into a detached clean `toy-projects` worktree at
`7c0712d5fec0cba85e478b7800f280b8498c3bb5`. All final invocations used `--profile deep
--no-cache --report --html verify_report.html`, `QT_QPA_PLATFORM=offscreen`, and the same explicit
Python 3.10 tool environment used by CI. The first BuildScope invocation left the launcher
environment implicit; it selected a system Python 3.14 without pytest/coverage and
correctly failed closed as `ERROR`/`NOT_RUN`. Repeating it with explicit `ICI_PYTHON` and PATH
removed that environment-only failure.

| Project | Suite | Build context | Tests / TEM | Sanitizer | Duplicate | HTML bytes / SHA-256 |
|---|---|---|---|---|---|---|
| DiskMap | WARN; lint tools unavailable only | 16/16 units, 30 configs | 11/11; 4.95 | PASS | PASS, 1.7% / 10 | 400,218 / `dcb273ff43e93ca82aa91837c61e20317fa5eabcfcc363965a4be5057781f19b` |
| BuildScope | WARN; no FAIL/ERROR | 12/12 units, 27 configs | 97/97; 4.95 | PASS | WARN, 6.0% / 60 | 991,338 / `63206cb409580cabe1b4c4038b58f98e6b1c572ffffa96dac2d1a6f8f7680b4f` |
| LogLens | WARN; lint tools unavailable only | 14/14 units, 40 configs | 12/12; 4.83 | PASS | PASS, 0.6% / 3 | 467,967 / `ff81a7e9dc87f6833e9ea0182323ae08aea3405e17d001fab8843ba77a127128` |

All three reports used `ici.result/v3`, producer 0.10.2, the exact toy source commit, v2 duplicate
fingerprints, both lexical-v1 tokenizers, and the region/signal metadata. Their HTML was valid UTF-8
with exact project titles and zero external HTTP assets. The generated JSON/HTML lived only in the
detached validation worktree and was removed after inspection.

## Status and Boundaries

- [x] Language-aware Python and C/C++ lexical tokenization is locally implemented and tested.
- [x] Region-bounded shared-seed Type-2 matching, exact normalized verification, bounded extension,
  and maximal-match deduplication are locally implemented and tested.
- [ ] Compiler/linker-backed exact dead-symbol evidence remains pending.
- [ ] Full duplicate semantic analysis remains pending.
- [ ] I4-3 aggregate checkpoint remains pending.
- [ ] PR, CI, main-branch, Pages, and hosted artifact acceptance remains pending; no run IDs,
  artifact hashes, or hosted URLs are asserted for this slice.

No version or CI configuration change belongs to this slice, and no release is created.

## Next Steps

1. Keep the lexical/token-region slice labeled heuristic and `ESTIMATED` until its remote
   acceptance and broader semantic policy are separately completed.
2. Add compiler/linker-backed dead-symbol evidence before marking the exact-dead requirement done.
3. Define and validate full duplicate semantic analysis and the remaining I4-3 acceptance gates.
