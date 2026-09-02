# Compiler-backed C++ function boundaries

## Overview

This workthrough records the historical PR #130 `feat/compiler-backed-cpp-functions` checkpoint.
It was merged into `main` at merge commit `8083267d864d3f29e6f3ae7c53358ce0b1674b44` after the
exact-main CI run `33580383887`. The current unmerged `feat/cpp-function-scope-policy` follow-up
is documented separately. This baseline is historical evidence, not a stable release record.

## Design

`ComplexityEngine` consumes the immutable `AnalysisContext` and its exact `CompilationContext`.
When a covered production unit has a capability-approved direct `clang-tidy`, the dedicated
`readability-function-size` probe emits threshold-zero diagnostics. The adapter maps the diagnostic
location and size notes to the source body geometry; it does not reuse lint checks or enable fixes,
shell execution, or source/context writes. The caller supplies a bounded source snapshot and the
adapter keeps a mapped-source cache; source bytes are revalidated before replay/projection and after
the tool returns. The complete C++ source inventory is bounded to 2,048 source files and 64 MiB of
aggregate UTF-8 source bytes. Equal body
geometry is promoted only when it is present in every successfully checked configuration; a missing
or configuration-dependent boundary remains a partial warning. Mapping covers trailing `requires`
expressions in addition to same-line/overloaded definitions, braced declarators/default/noexcept
expressions, function-try/catch bodies, and `<%`/`%>` digraph braces. Assigned `[]` and `+[]`
lambda initializers are excluded from fallback function candidates so they cannot create phantom
estimated functions; this does not close the broader lambda policy.
The complexity cache identity declares `ici.core._compile_db_paths`,
`ici.core._cpp_replay_policy`, `ici.core.cpp_replay`, `ici.engines._cpp_function_boundaries`,
`ici.engines._cpp_tooling`, and `ici.engines.cpp_text`.

The compiler-backed part ends at the function boundary:

- `boundary_source = "clang-tidy-ast"` and `boundary_confidence = "exact"` describe the confirmed
  function region only.
- Cyclomatic complexity and nesting inside that region are still ici's masked source-token and
  brace metrics (`if`/`for`/`while`/`case`/`catch`, `&&`/`||`/`?`, and braces), with
  `metric_confidence = "medium"`.
- clang-tidy `lines`/`statements`/`parameters` notes remain separate tool metadata, not compiler-
  computed CC or nesting.

## Boundary policy

```toml
[engines.complexity]
cpp_boundaries = "auto"  # auto | required | off
```

| Policy | Behavior |
|---|---|
| `auto` | Use AST-confirmed geometry when exact context/database and the approved tool exist; only context/tool unavailability falls back to the source scanner and `ESTIMATED` evidence. |
| `required` | Require every boundary to be exact; unavailable context/tool and any partial/estimated boundary become `ERROR`. |
| `off` | Do not run the probe; use the source scanner as an intentionally heuristic/`ESTIMATED` path. |

Empty or otherwise unreported definitions, including macro definitions excluded by the probe, may
remain heuristic. Once the tool has been attempted, tool/process nonzero exit, replay or coverage
failure, parser, timeout, truncation, or unit/global budget exhaustion is `ERROR`/`NOT_RUN`; it
never silently falls back. The C++ source inventory is bounded to 2,048 source files and 64 MiB of
aggregate UTF-8 source bytes (with 2,048 replay units, 8 MiB per source, and 64 MiB per run also bounded).
The mapped-source cache is limited to 16 MiB of UTF-8 source bytes, output to 1,000,000 characters, parser time to
10 seconds, unit time to 120 seconds, and the global run to 600 seconds. The approved tool executable
is re-resolved and its device/inode/mode/size/mtime/ctime identity is checked immediately before every
process execution; a changed or unavailable executable is an error. Descriptor reads compare
before/after identity; when `dir_fd`/`O_DIRECTORY` is unavailable, the named path is revalidated
after the read to close intermediate-symlink/TOCTOU races. The sole accepted suppression form is when clang-tidy emits the exact
`Suppressed N warnings (N in non-user code).` summary alongside visible project diagnostics,
because it accounts only for external/system diagnostics. NOLINT, project, mixed, malformed, or
count-mismatched suppression is otherwise `ERROR`/`NOT_RUN` and fail-closed.

## Historical PR #130 verification

| Check | Result |
|---|---|
| Python 3.10 pytest | `1,626 passed, 2 skipped` |
| Real process E2E | Extracted `clang-tidy-21` run of `tests/test_cpp_tool_e2e.py::test_cpp_function_boundaries_use_real_clang_ast_and_exact_context`; exact boundary/evidence and integrated complexity assertions passed. |
| Ruff | `check` and `format --check` passed |
| Mypy | passed |
| ZipApp | Two candidate `dist/ici.pyz` builds were byte-identical |
| Smoke | `./scripts/smoke.sh` passed; HTML Zero-CDN verification exited 0 |

The full-gate count above includes the historical PR #130 suppression, executable-identity, and
source-inventory regression tests. Candidate smoke and the HTML Zero-CDN check were green for that
baseline. The merged baseline was accepted by [exact-main CI run `33580383887`](https://github.com/jihoon22-lee/ici/actions/runs/33580383887)
on merge commit `8083267d864d3f29e6f3ae7c53358ce0b1674b44`.

## Historical PR #130 candidate toy verification

A temporary clean checkout was tested with candidate `dist/ici.pyz` SHA
`7945475868717131b1a908d93ec84e86e42020567182485b686e736e79268f7f`; two candidate builds produced
the same SHA. These are historical PR #130 cross-repository results; candidate smoke and HTML checks
were green for that baseline.

| Project | Result | HTML SHA |
|---|---|---|
| BuildScope | `required = WARN/MEASURED/exact`; exact 214, estimated 0; configurations/sources 12/12; total functions 393 | `4bf86b25be0e242d43a765e94685f8c0ce8f4ab40197658ae76b53591e844d4b` |
| DiskMap | `auto = PASS/ESTIMATED/mixed`; exact 127, estimated 2; configurations/sources 9/9; total functions 129; `required` expected `ERROR` for 2 inactive Windows functions | `cf6025d44601aa40d649e5c131ec58e87435321e90f417c1baa65a7fea933506` |
| LogLens | `auto = PASS/ESTIMATED/mixed`; exact 207, estimated 1; configurations/sources 14/14; total functions 208; `required` expected `ERROR` for 1 inactive portable Windows function | `133395792a401b6ea748954cfabbf75c51f86badec8fd4a6d83d97af018d4fc1` |

All three extracted HTML artifacts passed the published checker and the Zero-CDN checker. The toy
exact-main run `33574455762` succeeded at `d5f248...`, with 21 successful checks and 1 expected skip;
main Pages and the extracted artifact HTML bytes matched. These are historical PR #130 cross-repository
results, not evidence for the current scope-policy follow-up.

## Delivery status

The historical PR #130 candidate evidence is complete for this workthrough:

| Boundary | Status |
|---|---|
| ici smoke and HTML Zero-CDN check | passed |

Lambda and macro-generated-code policy was not closed by the PR #130 baseline; template/operator
coverage did not close that broader policy item. The current unmerged scope-policy follow-up tracks
that classification contract separately. C++ cognitive complexity and the remaining I4-3
maintainability items are also outside this workthrough.

No version was bumped. This is a feature/refactor checkpoint, and the repository release policy
keeps `v0.10.2` stable until the aggregate I4-3/I4 work and its release gates are complete.
