# C++ function-scope policy

## Overview

This workthrough records the `feat/cpp-function-scope-policy` slice delivered by PR #131, now
merged into `main` at `41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`. It closes the classification
contract around compiler-backed C++ complexity boundaries while keeping the public stable release
at `v0.10.2`. The slice is intentionally narrower than the remaining I4-3 aggregate, dead-symbol,
and duplicate-code work.

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
publisher required both report directories. That first remote attempt retained exactly one PR #131
sticky marker/comment and published the available ici report. Parser/source mapping was then separated from process
orchestration behind the existing import and monkeypatch facade; focused and full regressions prove
the split did not change the analysis contract.

The PR #131 policy candidate's exact local SHA is recorded only in package-external
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

The Python 3.10 and smoke results above are local evidence. The final candidate was then injected
into a fresh clean
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

## PR #131 exact-main acceptance

PR #131, titled `feat(complexity): classify C++ function scopes and metric provenance`, merged as
[`41690c9c2848fbc0332db4b80a4a1e2ed35db5d7`](https://github.com/jihoon22-lee/ici/commit/41690c9c2848fbc0332db4b80a4a1e2ed35db5d7).
PR CI [run `33592482495`](https://github.com/jihoon22-lee/ici/actions/runs/33592482495) succeeded,
with exactly one sticky marker/current run. PR ici/viewer Pages passed HTTP/title/Zero-CDN checks
and artifact byte-match at `7,454,995` and `356,598` bytes. Exact-main [run
`33593218450`](https://github.com/jihoon22-lee/ici/actions/runs/33593218450) also succeeded;
main JSON `source_commit` matched the same SHA, and main ici/viewer Pages passed HTTP/title/Zero-CDN
and artifact byte-match checks:

| Report | Bytes | Artifact SHA |
|---|---:|---|
| ici | 7,454,995 | `182a0d05…5adbb75` |
| viewer | 356,598 | `fb772d4a…c0c4794` |

Only the expected PR/main publish jobs were skipped. This remote acceptance closes the scope-policy
slice, not the remaining I4-3 aggregate, dead/duplicate policy, I4-4, or the broader I4 checkpoint.

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

This boundary is a historical snapshot of the function-scope policy work. No version was bumped
and no release was created; `v0.10.2` remains the public stable artifact. The current
compiler-backed C/C++ unused-function slice is recorded in the [`compiler-backed C/C++
unused-function workthrough`](2026-09-03-compiler-backed-cpp-unused-functions.md). Its narrow
TU-local exactness is no longer the open dead-code item; the remaining dead/unused-symbol
exactness is the broader whole-program/linker-backed reachability scope. Generated/moc/vendor
duplicate policy and the broader I4-3/I4 checkpoint remain open.

## Current compiler-backed unused-function follow-up

The separate TU-local unused-function slice is now accepted remotely and does not change the
historical function-scope policy recorded above. Its final commits through `13099ca` are
`8f8f4d0` (compiler-family alias detection), `7522fe7` (strict option-separator operands),
`88c18da` (alias-family regression coverage), `3a38997` (compiler/cwd-bound include-projection
cache identity), `ea1d4b5` (project formatting), `2b7ff41` (all owned/external C/C++ source scope
and automatic-policy alignment), and `13099ca` (the strict `c`/`c++` translation-unit language
guard). The current local gate is focused `607 passed, 6 skipped` (17.99s), full `1,966 passed,
7 skipped` (68.60s), Ruff 184 files, mypy 104 files, two byte-identical pyz builds at 2,273,944
bytes with SHA-256
`2a3c8b011e53d21529ee03e20b0f7eeafbf7fbfaf6b8a9e35f5445b166c88d28`, and smoke PASS.

The corresponding no-cache self verify is WARN with 8 pass/5 warn/0 fail/0 error/1 skip, TEM 4.84,
duration 184.31s, wall 188.75s, RSS 605,144, HTML 5,526,617 bytes / `159ba3db…`, and JSON
15,590,867 bytes / `0d38d3b…`; the title is `ici Verification Report — ici` and Zero-CDN is
`[]`. Viewer standalone is PASS with `8/8/8 targets/tools`, wall 10.73s, RSS 360,320. Viewer
deep no-cache is WARN with 12 pass/1 warn/0 fail/0 error/1 skip, 14 engines, 7/7, TEM 4.89,
duration 20.00s, wall 20.42s, RSS 360,556, HTML 355,996 bytes / `9098…`, and JSON 743,422
bytes / `069eb0…`; the title is `ici Verification Report — viewer` and Zero-CDN is `[]`.
PR #137, titled `feat(dead): add compiler-backed C/C++ unused-function evidence`, passed all
required checks in workflow run [`33675765436`](https://github.com/jihoon22-lee/ici/actions/runs/33675765436)
at head `9c9d83cdaae02384bbc58e7cb79b4bbb098b86d3` with synthetic merge
`f2cfce8b8a7ebc90308bb442f3a323e01ed9ef34`. Its single current-run sticky comment
([comment](https://github.com/jihoon22-lee/ici/pull/137#issuecomment-5515582296)) contained exactly
two report links. PR report artifacts and Pages copies were byte-identical, with synthetic-merge
`source_commit`, exact titles, valid UTF-8, and Zero-CDN checks:

| Report | HTML bytes / SHA-256 | JSON bytes / SHA-256 |
| --- | --- | --- |
| ici | 5,188,748 / `8648d7ac06fded3afaa004568a9665bb3bc2b10c7e41f1da06af41b0eb3952f8` | 15,288,643 / `f9401da10828ab3d0c1c6b9430789d25b4ef4ac15e8dbe410f0f244a584aefef` |
| viewer | 363,787 / `0123db7d6e5c820fc0bd952a0fd55b82752b63d873b4f0502e12f676b3e71cda` | 905,151 / `edde8208502d4af5c060e556ece1650518893c7274487cca2283c02f63322f98` |

The PR was squash-merged as `782589a4ef02209703e882a09cc0d8b0c7940218`, and the feature branch
was deleted. Exact-main run [`33676873412`](https://github.com/jihoon22-lee/ici/actions/runs/33676873412)
completed all relevant checks successfully. Pages build API run `1190632325` and workflow run
[`33677689026`](https://github.com/jihoon22-lee/ici/actions/runs/33677689026) succeeded as well. Main
report artifacts and Pages copies were byte-identical, with `source_commit` matching the merged
main SHA and exact title, UTF-8, and Zero-CDN checks passing:

| Report | HTML bytes / SHA-256 | JSON bytes / SHA-256 |
| --- | --- | --- |
| ici | 5,188,748 / `7d9a23d5eb47bcf0ab82f074a85e65eb264869f8f0333318673890d75b0c4eaf` | 15,288,649 / `99d5c208a30518e0c356c4e9a26b2306a99468d51369dd91e9eaa19b71a22e19` |
| viewer | 363,788 / `223c027a6cbbef5aa08c464f210286c6a90ae2a702451739aa94bf704648188f` | 905,152 / `152e6c2f6d2b53728f39680b3198b5fb46d1c28e915731c6a7693f85c0175557` |

No release or version bump is implied; `v0.10.2` remains the public stable artifact.
