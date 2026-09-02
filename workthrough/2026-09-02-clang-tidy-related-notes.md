# Clang-tidy Related-Note Aggregation

## Overview

The clang-tidy text parser previously returned every explanatory `note:` as an independent
`CppDiagnostic`. That made the same actionable warning appear multiple times in targets, finding
counts, and report metadata. The `fix/clang-tidy-related-notes` implementation at `f999ee3`, the
linear grouping refactor at `3fc45a7`, reporter projection at `c3108a7`, and reporter contract
coverage at `0f46ec5` now attach ordinary clang-tidy and `clang-analyzer-*` explanation notes to
their preceding primary while preserving their locations, messages, and fix-its.

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
- A rule-less clang-tidy/analyzer `note:`, or one that repeats the primary's rule, is attached only
  to the immediately preceding primary in the same contiguous diagnostic stream. A new primary
  starts a new group; notes are not retroactively associated across a primary boundary.
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
- Native finding canonicalization normalizes every related location to a project-relative path and
  orders the list deterministically by path, start line/column, end line/column, and label. JSON
  and HTML retain the complete canonical related-location inventory. GitHub Markdown renders the
  non-informational, unsuppressed related rows up to 100 rows per engine and emits an omission
  notice while directing consumers to the full JSON/HTML reports. The Markdown projection also
  renders native-only related evidence when no legacy target rows exist. External locations are
  deliberately rendered as non-links; HTML and Markdown preserve accessible location labels and
  exact line/column coordinates. Native finding occurrences remain a multiset when fingerprints
  collide, so duplicate evidence is not silently discarded.

- `e1a665d` refactors Markdown detail rendering into bounded target-row, related-location, and
  failure-snippet helpers. It preserves the native-only, bounded, external-location, and exact
  coordinate contract while reducing the complexity of the single report-generation path.

### Function-boundary consumer adaptation

The shared parser's nested representation is intentionally not flattened for lint accounting.
`e86c982` updates the compiler-backed function-boundary parser to consume each primary followed by
its `related_diagnostics` in stream order when interpreting `readability-function-size` lines,
statements, and parameters evidence. This restores structural boundary mapping and invalid-location
rejection while keeping the lint engine's primary-only targets, findings, and counts. The parser
and reporters therefore use the same related-note contract for different purposes: structural
consumers read the evidence, while issue consumers preserve it without inventing findings.

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

These focused results do not constitute a full local gate. The version remains `0.10.2`; no
release is created.

## Final local gate attempt

The complete Python 3.10 pytest gate was run after the focused bundle:

```text
uv run --python 3.10 pytest
1750 passed, 7 skipped, 10 failed in 63.81s
```

All ten failures are in `tests/test_cpp_function_boundaries.py`:

- `test_parser_maps_template_operator_to_the_clang_confirmed_body`
- `test_parser_maps_literal_operator_from_suffix_diagnostic_column`
- `test_parser_maps_function_try_block_through_its_catch_handlers`
- `test_parser_rejects_a_metric_note_at_another_location`
- `test_adapter_uses_exact_sanitized_context_and_records_evidence`
- `test_same_geometry_with_metric_variants_stays_partial_and_exposes_configs`
- `test_preprocessor_conditional_boundary_is_partial_and_low_confidence`
- `test_boundary_missing_from_one_successful_configuration_stays_partial`
- `test_complexity_prefers_exact_operator_boundary_and_discloses_confidence`
- `test_required_mode_rejects_functions_left_to_the_source_scanner`

The failure is a downstream contract mismatch exposed by this change, not a silent pass. The shared
`parse_clang_tidy_diagnostics()` now returns only primary diagnostics at the top level and stores
their metric notes in `CppDiagnostic.related_diagnostics`. The compiler-backed function-boundary
parser still iterates only `parsed.diagnostics` and sends the missing notes to `_locate_body()`;
multi-line bodies then fail with `function-size lines note is required`, while the negative
location test no longer sees the note it expects to reject. No production or test fix was made in
this gate run.

The remaining local gates from that same historical attempt produced the following evidence:

```text
uvx ruff check .
All checks passed!

uvx ruff format --check .
167 files already formatted

uv run --python 3.10 mypy src
Success: no issues found in 98 source files

./scripts/build-pyz.sh  # run twice
both builds: 2241222 bytes, SHA-256
cdeb4a0fb8cff1c8dc489831e6b9c73146b2edc18baa568cb00c7b95f43a11a4

./scripts/smoke.sh
exit 0; packaged launcher, Python 3.10 direct execution, artifact equality, and HTML Zero-CDN
checks passed (the embedded verify returned 1 because the current quality suite is failing).
```

The no-cache source self-verify was also run with a temporary HTML output. It returned exit `1`
with suite `FAIL`:

| Metric | Result |
|---|---|
| Engines | 13 total: 5 PASS, 5 WARN, 2 FAIL, 0 ERROR, 1 SKIP |
| Test engine | `1750/1767` tests passed |
| Coverage | line `89.0%`, function `96.8%`, branch `81.3%` |
| TEM | `4.79 / 5.0` |
| Cache/time | 0 hits, `162.31s` |
| HTML | `7,774,723` bytes; SHA-256 `93944e22baf1fbc267b097734555bb13a9a436beb727cbf1636a150b1751395e` |
| Title | `ici Verification Report — ici` |
| Zero-CDN | 0 external executable/display asset references |

The temporary self-report was removed after its bytes, digest, title, and Zero-CDN result were
recorded. Because the complete local test gate fails, PR checks, CI, Pages, and merge verification
remain pending. The version remains `0.10.2`; no release is created.

## Resolution after the historical failure

The failure was resolved in `e86c982` (`fix(complexity): consume related function-size notes`).
The function-boundary parser now expands each top-level primary to the ordered pair
`(primary, *primary.related_diagnostics)` only inside its structural parsing loop. This allows the
function-size metric notes to reach `_consume_boundary_diagnostic()` and `_locate_body()` again,
without restoring them as independent lint targets or findings. The later `3fc45a7` grouping
refactor keeps the association linear and contiguous, while `c3108a7` and `0f46ec5` preserve and
exercise the related evidence in HTML and bounded Markdown output; JSON continues to serialize the
full `Finding.related_locations` list.

The final canonical Python 3.10 gate was subsequently run on clean commit `20cadb0`:

```text
uv run --python 3.10 pytest -rs
1768 passed, 2 skipped in 63.45s (0:01:03)
```

The two expected skips are the real-tool cases requiring unavailable `clang++`/`clazy`; the
available LLVM 21 clang-tidy path was exercised. `uvx ruff check .`,
`uvx ruff format --check .`, and `uv run --python 3.10 mypy src` passed (`98` source files).
Two consecutive `./scripts/build-pyz.sh` runs were byte-identical at `2,242,724` bytes with
SHA-256 `3602c2cb1b6998a54f00bf809a88d81617bec58c891bfaf12bf22bc882e71890`;
`./scripts/smoke.sh` passed, including its packaged verify and Zero-CDN check.

An intermediate no-cache self-check on `d1e5931` returned suite `FAIL` because the newly expanded
`generate_markdown_report()` reached critical complexity 31. That was a useful dogfood finding,
not an accepted warning: `e1a665d` split target, related-location, and snippet rendering into
bounded helpers. Focused reporter tests stayed byte-equivalent at 0/100/101/200-row boundaries,
and the final no-cache packaged self-check returned exit `0`:

| Metric | Final result |
|---|---|
| Engines | 13 total: 7 PASS, 5 WARN, 0 FAIL, 0 ERROR, 1 SKIP |
| Test engine | `1768/1770` tests passed |
| Coverage | line `89.1%`, function `96.8%`, branch `81.5%` |
| Complexity | maximum 25 across 1,267 functions; no critical finding |
| TEM/cache/time | `4.84 / 5.0`; 0 cache hits; `162.60s` |
| HTML | `8,288,600` bytes; SHA-256 `565854796c2ceb8e18f3ef6adf7771854a091a7e4996a148a6e3185664decab3` |
| Integrity | UTF-8 exact title `ici Verification Report — ici`; 0 external resources |

The earlier `1750 passed, 7 skipped, 10 failed` run remains historical evidence of the
pre-`e86c982` boundary-consumer mismatch. This follow-up's PR/CI/Pages checks are still pending.
The version remains `0.10.2`; no release is created.

## Follow-up and roadmap status

The primary/related split should remain covered by parser, adapter, reporter, and real-tool tests,
including LLVM 18 conversion-note output and note fix-it preservation. Before treating this as a
delivery checkpoint, run the repository's full quality gates and the required PR/CI/Pages evidence.

Compiler/linker-backed exact dead-symbol evidence and robust language-aware duplicate tokenization
remain pending I4-3 work. This note aggregation does not change those roadmap statuses.

`git diff --check` is the final whitespace check for the documentation-only follow-up.
