# C++ function-scope policy

## Overview

This workthrough records the unmerged `feat/cpp-function-scope-policy` slice. It closes the
classification contract around compiler-backed C++ complexity boundaries while keeping the public
stable release at `v0.10.2`. The slice is intentionally narrower than the remaining I4-3 aggregate,
dead-symbol, and duplicate-code work.

## Context

PR #130 established clang-tidy-backed body geometry, but the boundary result still needed an
explicit policy for templates, operators, literal operators, lambdas, macro-generated functions,
and configuration-dependent metrics. Without that policy, a fallback scanner could lose operator
names, count a lambda as a named function, or associate a macro expansion with the next brace in a
file.

## Contract

- Compiler-backed targets are source-spelled named functions. Function templates, conversion/call/
  subscript operators, and literal operators are included. The result preserves
  `function_kind`, `function_template`, and `function_origin` metadata.
- A lambda is not an independent function target. Its body is masked out of the enclosing function's
  CC and nesting calculation. The aggregate exclusion count is exposed as
  `extra.cpp_scope_exclusions.lambda`.
- A macro-generated function diagnosed at an expansion site is explicitly excluded and counted as
  `extra.cpp_scope_exclusions.macro_generated_function`. The mapper never searches forward to the
  next brace to invent a body for that diagnostic.
- The fallback scanner preserves operator names, blanks multiline preprocessor definitions and
  continuations, and skips standalone macro invocations. Unreported source-spelled definitions may
  still be estimated; macro-generated expansion scopes are not promoted as targets.
- A boundary is promoted across successful configurations only when geometry, name, kind, and
  provenance all agree. Clang-tidy function-size lines/statements/parameters are retained per
  configuration in `configuration_metrics`.
- Different per-configuration function-size values, or a body containing a conditional
  preprocessor branch, marks the run `partial` and the target metric confidence `low`. `required`
  treats that partial/low-confidence result as `ERROR`/`NOT_RUN` (fail-closed). Compiler-backed
  function metrics or configuration coverage that remains partial/low-confidence is not accepted
  by `required`.

The resulting metadata is intentionally explicit rather than folded into the CC value:

```json
{
  "function_kind": "operator",
  "function_template": true,
  "function_origin": "source-spelled",
  "configuration_metrics": [
    {"configuration": "sha256:...", "lines": 5, "statements": 3, "parameters": 1}
  ],
  "excluded_nested_lambdas": 1,
  "metric_confidence": "medium"
}
```

For a configuration-dependent metric, the same target retains all observed configuration rows and
sets `metric_variant: true` with `metric_confidence: "low"`; it is not silently reduced to one
configuration's value.

## Verification status

The already merged compiler-boundary baseline was accepted at exact main by [CI run
`33580383887`](https://github.com/jihoon22-lee/ici/actions/runs/33580383887) on merge commit
`8083267d864d3f29e6f3ae7c53358ce0b1674b44`. Its trusted main ici/viewer Pages were independently
byte-matched to the extracted HTML in the `ici-verification-report` artifact:

| Report | Extracted HTML bytes | Extracted HTML SHA-256 |
|---|---:|---|
| ici | 7,146,767 | `58a168d1e83d1e75a2bfbbb17d00bdc5828f9a5d9380f21b74cfb8741483e8e2` |
| viewer | 356,596 | `29cd2bc0af33ca9fe60378350d65dfa723c00a690e2fb8fe1008a1981b7aa83b` |

Both pages returned HTTP 200 and `text/html`, carried the expected report title, and contained no
external resource URLs. The first remote attempt for this follow-up, workflow
[`33588501321`](https://github.com/jihoon22-lee/ici/actions/runs/33588501321), correctly blocked the
merge because `_cpp_function_boundaries.py` had reached 1,031 pure code lines over the 1,000-line
self gate. Its Qt 5 and Qt 6 jobs passed. The viewer publisher failure was downstream rather than a
separate uploader defect: the failed root dogfood step did not produce a viewer report, while the
publisher required both report directories. PR #131 still retained exactly one sticky marker/comment
and published the available ici report. Parser/source mapping was then separated from process
orchestration behind the existing import and monkeypatch facade; focused and full regressions prove
the split did not change the analysis contract.

The current unmerged policy candidate's exact SHA is recorded only in package-external
documentation because `README.md` is embedded in `dist-info/METADATA`; adding it to README would
change the artifact hash. Its local verification is:

| Check | Result |
|---|---|
| Python 3.10 full suite with real extracted `clang-tidy-21` | `1,656 passed, 2 skipped` |
| Ruff | `check` and `format --check` passed |
| Mypy | passed |
| Focused split regression | `89 passed, 6 skipped`; Ruff and Python byte-compilation passed |
| Boundary-module self gate | parser/source mapping 628 pure code lines; process-runner compatibility facade 487; `ici line` exit 0 |
| ZipApp | Two candidate builds were byte-identical at SHA `2af5198d1348a64c39f4f37d12657aa9a2c4bf3ddf034a9099909c41e86e30e7` |
| Packaged smoke | passed, including Python 3.10 execution, artifact integrity, and Zero-CDN self-report checks (`verify` exit 0) |

The Python 3.10 and smoke results above are local evidence; they do not close the remote delivery
gate. The final candidate was then injected into a fresh clean
`toy-projects` `main` checkout (commit `d5f248c41375e2c0b4286890e1b359f59e11e728`) with Python
`3.10.21` and extracted `clang-tidy-21` reporting version `Ubuntu LLVM 21.1.8`. BuildScope used the `deep` profile for both
`auto` and `required`; DiskMap and LogLens used their `auto` probes. Every probe wrote both JSON and
HTML reports.

| Probe | Exit | Suite | Complexity / evidence | Mode | Exact / estimated | Config | Source | Functions / result targets | Lambda / macro exclusions | HTML bytes / SHA-256 |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---|
| BuildScope `auto` | 0 | `WARN` (total 14; pass 10, warn 4, fail 0, error 0, skip 0) | `WARN` / `ESTIMATED` | `partial` | 214 / 0 | 12 | 12 | 393 / 393 | 10 / 0 | 1,330,564 / `2ab94e917d308acf64ba55ccd0a0e445f0d10cb384c189e3aa07604c11dd4646` |
| BuildScope `required` | 1 | `ERROR` (total 14; pass 10, warn 3, fail 0, error 1, skip 0) | `ERROR` / `NOT_RUN` | `error` | 214 / 0 | 12 | 12 | 393 / 394 | 10 / 0 | 1,331,375 / `3f96fd23427c56b4fbc7254ee186128cad4d99b5812e69bd96bf2d63ff08c91a` |
| DiskMap `auto` | 0 | `WARN` (total 13; pass 10, warn 1, fail 0, error 0, skip 2) | `PASS` / `ESTIMATED` | `partial` | 127 / 2 | 9 | 9 | 129 / 129 | 7 / 0 | 337,286 / `0f7ab818b87b03c09bdbdf0da258760481a858292d7e5bbf101b8229678924f3` |
| LogLens `auto` | 0 | `WARN` (total 13; pass 10, warn 1, fail 0, error 0, skip 2) | `PASS` / `ESTIMATED` | `partial` | 207 / 1 | 14 | 14 | 208 / 208 | 8 / 0 | 489,910 / `5fec22e7bbc723b4268dbbc26cd8b05bfd361818f7689f90a920be620aa9e42b` |

The BuildScope `required` overlay was `[engines.complexity] cpp_boundaries = "required"`. It
failed closed with this exact error (the partial conditional metrics are not silently promoted):

```text
compiler-backed C++ function boundaries were required, but compiler-backed function metrics or configuration coverage remained partial/low-confidence
```

Its console accounting was `fail 0, error 1`; the v3 suite JSON also retained aggregate
`failed_count=1` together with `error_count=1`, so the required failure is not mistaken for a
measured complexity defect.

The `auto` partial evidence was retained in the reports: BuildScope warned at
`src/core/contract.cpp:43`, `:57`, and `:117`; DiskMap at `src/fs_source.cpp:52` and
`src/gui/treemap_widget.cpp:26`; and LogLens at `src/log_source.cpp:71`, `:314`, and `:331`.
The boundary target sources were AST-backed (`clang-tidy-ast`) for all exact counts; the reported
`partial` mode reflects the conditional-preprocessor metric warnings, while the estimated counts
remain explicit in the table.

`ci/check_published_html.py` was run once against each of the four generated HTML files. All four
invocations exited 0 and passed the report-title and Zero-CDN checks. The ici source files and its
pre-existing worktree status were unchanged before and after the probe; the isolated toy checkout
remained clean at exact `main` commit `d5f248c41375e2c0b4286890e1b359f59e11e728`. The final
temporary checkout `/tmp/ici-final-candidate.B63bi6` and its reports were deleted after collection.

## Reviewer-hardening coverage

The local regression suite records these scope-policy outcomes:

- only source-defined function-like macros prove a generated expansion; source-spelled uppercase
  functions, including next-line inline constructors, remain targets;
- multiline macro calls and same-line class/namespace members map without stealing the next brace;
- canonical and spaced operator spellings, including operator mentions, are preserved, while
  trailing `requires` expressions are excluded from the function body;
- nested lambda bodies are excluded and counted, empty tool output with fallback targets remains
  partial, and suppression-only output is rejected;
- the mapped-source cache enforces its 16 MiB limit.

## Delivery boundary

No version was bumped and no release was created. `v0.10.2` remains the public stable artifact.
The remaining I4-3 aggregate, dead/unused-symbol exactness, and generated/moc/vendor duplicate
policy are still open, as is the broader I4 checkpoint.
