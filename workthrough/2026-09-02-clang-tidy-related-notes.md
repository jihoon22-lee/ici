# Clang-tidy Related-Note Aggregation

## Overview

The clang-tidy text parser previously returned every explanatory `note:` as an independent
`CppDiagnostic`. That made the same actionable warning appear multiple times in targets, finding
counts, and report metadata. The `fix/clang-tidy-related-notes` implementation at `f999ee3`
attaches ordinary clang-tidy and `clang-analyzer-*` explanation notes to their preceding primary
diagnostic while preserving their locations, messages, and fix-its.

This is a focused analyzer-correctness follow-up. It does not close I4-1 or I4-3 as a new roadmap
checkpoint, and it does not change compiler-diagnostic or Clazy note policy.

## Context

The shared text parser recognizes `warning:`, `error:`, `note:`, and `remark:` lines. Before this
change, clang-tidy normalization inherited the preceding rule and family for a rule-less note but
still appended the note as a separate diagnostic. The clang-tidy adapter copied every parsed item
to `outcome.targets`, and `LintEngine` created one v3 `Finding` per item. A primary warning plus
three explanatory notes therefore produced four warning/finding records.

LLVM 18's swappable-parameter output includes concrete parameter-range and implicit-conversion
notes. Those messages remain valuable evidence, but they describe the primary check rather than
independent actionable checks.

## Changes reviewed from `f999ee3`

### Parser aggregation

- `CppDiagnostic.related_diagnostics` stores explanatory child diagnostics without flattening them
  into the top-level diagnostic sequence.
- A rule-less clang-tidy/analyzer `note:`, or one that repeats the primary's rule, is attached to
  the immediately preceding primary.
- A note that names a different check rule is rejected atomically.
- A leading/orphan note is rejected atomically.
- Existing bounded parsing, empty LLVM structural-note validation, location normalization, and
  note-level fix-it parsing remain in force.

### Finding and metric projection

- Only top-level primary clang-tidy/analyzer diagnostics become `InspectionTarget` and v3
  `Finding` records.
- Each related note is projected to `Finding.related_locations`; its project-relative location and
  full diagnostic message are retained as the related-location label.
- Note fix-its are included in the primary finding remediation and `extra` fix-it metadata.
- `cpp_related_notes` exposes the number of attached notes without counting them as violations.
- clang-tidy/analyzer warning, violation, diagnostic-family, and finding counts now count primary
  diagnostics only.

### Unchanged boundaries

- Compiler text and GCC JSON child/note diagnostics retain their existing independent-diagnostic
  behavior.
- Clazy rule-owned `ClazyNote` diagnostics retain their existing independent behavior; compiler
  diagnostics encountered in Clazy output remain filtered according to the Clazy parser contract.
- No source or context mutation, automatic fix application, or release/version change is part of
  this work.

## Verification results

The focused Python 3.10 pytest run covered five related files:

```text
uv run --python 3.10 pytest tests/test_cpp_diagnostics.py tests/test_clang_tidy.py \
  tests/test_cpp_tool_e2e.py tests/test_lint_engine.py tests/test_finding_contract.py
177 passed, 6 skipped in 1.93s
```

The local static checks also passed:

```text
uv run --python 3.10 ruff check .
All checks passed!

uv run --python 3.10 ruff format --check .
167 files already formatted

uv run --python 3.10 mypy src
Success: no issues found in 98 source files
```

These are focused local results only. Full local quality gates, PR checks, CI, Pages, and merge
verification remain pending. The version remains `0.10.2`; no release is created.

## Follow-up and roadmap status

The primary/related split should remain covered by parser, adapter, reporter, and real-tool tests,
including LLVM 18 conversion-note output and note fix-it preservation. Before treating this as a
delivery checkpoint, run the repository's full quality gates and the required PR/CI/Pages evidence.

Compiler/linker-backed exact dead-symbol evidence and robust language-aware duplicate tokenization
remain pending I4-3 work. This note aggregation does not change those roadmap statuses.

`git diff --check` is the final whitespace check for the documentation-only follow-up.
