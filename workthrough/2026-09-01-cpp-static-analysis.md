# I4-1 C++ Static Analysis Adapters

## Overview

Commits `19008fd3d3b3a26d35ffdb15ae4dd38d4470cc68`, `4f8fbc5`, `f1cc3d9`, `aaacff4`,
and `87e0e8b` on `feat/cpp-static-analysis` implement and harden I4-1. C++ lint now consumes the
immutable I3 `AnalysisContext` and normalized
`CompilationUnit` contract to run exact compiler diagnostics and optional clang-tidy checks.
This is a local implementation record: no release/version bump or remote PR/CI/Pages evidence
is claimed here.

## Context

I4-1 requires compiler JSON/text diagnostics with stable rules and precise locations, selected
clang-tidy checks driven by compilation context, a distinct Clang Static Analyzer category,
project-bounded tool configuration with documented precedence, read-only fix-it suggestions,
and explicit handling for missing tools or failed compilation. A clean result must not be
claimed when the execution context or output cannot be trusted.

## Changes Made

### Architecture decisions

- `LintEngine` remains the facade and combines Python lint with C++ syntax, compiler diagnostics,
  and clang-tidy findings. Compiler, clang-tidy, and `clang-analyzer-*` diagnostics retain
  separate families, evidence, categories, confidence, and tool metadata.
- `src/ici/engines/_cpp_lint.py` selects covered production translation-unit configurations from
  `AnalysisContext`, then replays each through the shared approved, sanitized compiler command
  builder. It uses GCC 9+ JSON diagnostics and bounded parseable-fix-it text for Clang or unknown
  compiler versions. A heuristic `g++ -std=c++17` fallback is used only when no compilation
  context exists and is reported as `ESTIMATED`; an exact-context failure never falls back
  silently.
- `src/ici/engines/_cpp_diagnostics.py` atomically parses bounded GCC/Clang JSON and text output.
  It normalizes project-relative and external locations, stable rule IDs, severity, child/note
  diagnostics, analyzer family, and fix-it ranges/replacements into `CppDiagnostic` records.
- `src/ici/engines/_clang_tidy.py` provides read-only `auto`, `required`, and `off` modes over
  exact compilation context. Explicit `clang_tidy_checks` override built-in defaults; an
  explicit project config overrides bounded `.clang-tidy` discovery. Discovery stops at the
  project root, never inherits a parent-of-project config, and rejects unsafe config argument
  injection.
- `src/ici/config.py` and `src/ici/config_schema.py` add and validate clang-tidy mode, checks,
  and project-contained config settings. `src/ici/core/support.py` exposes clang-tidy as an
  optional or required capability according to policy. `src/ici/core/cache_identity.py` tracks
  `.clang-tidy` inputs; `LintEngine` declares the new replay, parser, adapter, and lint helper
  modules as cache implementation dependencies.
- `CHANGELOG.md` records the I4-1 contract. Focused coverage was added or extended in
  `tests/test_clang_tidy.py`, `tests/test_cpp_diagnostics.py`, `tests/test_config.py`,
  `tests/test_lint_compilation_context.py`, `tests/test_lint_engine.py`,
  `tests/test_support_matrix.py`, and `tests/test_cache_identity.py`.

### Safety and fail-closed semantics

- Execution uses only directly invoked, capability-approved external tools; source paths,
  working directories, and configs must remain within the project boundary. Replay uses a
  positive option allowlist, minimal replacement environment, closed stdin, bounded argv/output,
  no shell, per-unit limits, and a global clang-tidy budget.
- Missing or malformed compilation context/output, coverage mismatches, unsafe replay/config,
  spawn failures, non-verifiable exits, timeouts, truncation, and unit/global budget exhaustion
  become explicit `ERROR`/`NOT_RUN` evidence and targets. They are never presented as a clean
  analysis or hidden behind a heuristic fallback.
- Compiler and clang-tidy adapters independently cap selection at 2,048 translation units, each
  unit at 120 seconds, and the whole adapter at 600 seconds. A context-level error prevents all
  compiler replay instead of retaining partial successful evidence. Valid unlocated GCC
  command-line/ICE diagnostics remain visible at the bounded `[external]`:1 target.
- Project clang-tidy config rejects `InheritParentConfig` as well as `ExtraArgs` and
  `ExtraArgsBefore`, preventing config or compiler arguments from being inherited above the
  project boundary.
- `clang-tidy = "auto"` records an unavailable tool as a warning; `"required"` promotes it to an
  error; `"off"` performs no invocation or evidence collection. Fix-its are retained only as
  finding remediation/report suggestions; the default run never edits source or compilation
  context.

## Code Examples

The shared execution and reporting path is:

```text
AnalysisContext + CompilationUnit
  -> build_replay_command(operation="syntax")
  -> approved direct compiler or clang-tidy
  -> atomic diagnostic parser
  -> InspectionTarget + native Finding + ToolEvidence
```

The lint policy surface is intentionally small:

```toml
[engines.lint]
clang_tidy = "auto"                 # auto | required | off
clang_tidy_checks = ["bugprone-*"]  # optional explicit checks
clang_tidy_config = ".clang-tidy"    # optional project-contained config
```

## Verification Results

The exact local gate evidence for the current branch head is:

- Python 3.10 full pytest collected 1,414 tests; 1,413 passed and the environment-only `clang++`
  case skipped. The required clang-tidy actual-process case ran against an unprivileged LLVM 18
  extraction rather than skipping.
- The actual-process GCC JSON and clang-tidy adapter E2Es both passed locally. CI and release
  install clang-tidy and set `ICI_REQUIRE_STATIC_ANALYSIS_TOOLS=1`, so the clang-tidy case cannot
  skip remotely.
- Ruff passed for 153 files.
- mypy passed for 90 source files.
- `build-pyz` passed with 10 pure-Python distributions and 2 schemas packaged.
- Smoke, self-verification, and Zero-CDN checks passed with verify exit 0; the generated report was
  audited and its temporary HTML/JSON were removed.
- The reproducible packaged artifact SHA-256 is
  `a0cbeed617e49ca481659e1dfe03da16bd2f073b246c6c692bc0cd1781034aad` (2,180,524 bytes).

The base environment has no installed `clang-tidy` binary. For the CI failure reproduction,
LLVM 18 packages were extracted without installation into a temporary directory and executed with
their own bounded library paths. Both required actual-process GCC/clang-tidy E2Es then passed; in
ordinary `auto` mode a missing binary remains an explicit warning, while `required` mode fails
closed.

## Next Steps

The first PR #115 run (`33466122397`) proved the required real clang-tidy E2E but failed the
self-dogfood gate because the new orchestration entry point had cyclomatic complexity 30. Commit
`87e0e8b` split that path into focused helpers: the entry point is now 10, every helper is at most
11, the repository maximum is back to the warning-only 25, and local self-verification exits 0.
That failed run is diagnostic evidence, not completion; the amended head still requires a fully
green rerun and fresh report publication.

The second PR run (`33466820095`) passed unit tests, isolated smoke, self-dogfood, and both Qt
builds, then correctly blocked merge when the viewer's eight clean translation units produced an
LLVM 18 summary that the parser could not account for. Reproduction showed that `--quiet` emitted
only `15780 warnings generated.`, while normal mode also emitted the matching
`Suppressed 15780 warnings (15780 in non-user code).` trailer and the extended LLVM 18
system-header hint. The adapter now preserves suppression accounting, the parser accepts that
bounded hint variant, and an unaccounted quiet summary remains a fail-closed parse error. Another
remote rerun is still required before merge.

- Open the feature PR and obtain its CI/Merge Gate, sticky-comment, and independent Pages
  evidence; then verify the exact-main CI/Pages result after merge.
- Complete the downstream BuildScope B4 validation. Until those remote and cross-repository
  checks are complete, I4-1 remains an unreleased implementation checkpoint and no release
  boundary is advanced.
