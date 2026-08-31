# I3-2 canonical CMake compilation database

## Overview

I3-2 adds a deterministic CMake preflight for C/C++ projects that do not already
provide `compile_commands.json`. The preflight produces one immutable,
compiler-context snapshot for the analyzer while keeping existing configured or
discovered databases authoritative.

## Context

The I3-1 loader could safely consume an existing compilation database, but a
CMake project with no exported database still had no exact translation-unit
context. Re-running arbitrary project commands would weaken reproducibility and
could make generated sources, unity builds, or multi-configuration output look
more precise than the evidence allowed. I3-2 therefore uses a project-contained
Release shadow, bounded cache metadata, and explicit diagnostics for unsupported
conditions.

## Changes Made

### 1. Canonical CMake preflight

- `src/ici/core/cmake.py` exports compile commands for every CMake configure.
- `src/ici/core/_build_paths.py` owns project-contained shadow construction so
  the native adapter remains below the 500-code-line quality threshold.
- `src/ici/core/cmake_context.py` uses `build/ici-cmake-build` only when a C/C++
  root CMake project has neither an explicit nor an auto-discovered database.
- The analyzer configure selects Release, enables export, and disables unity
  compilation. Only single-config `Ninja` or `*Makefiles` generators are exact.
- The canonical shadow and all generated paths remain inside the project/build
  boundary.

### 2. Generated sources and trust boundaries

- A first load that finds a stale generated source inside the canonical shadow
  performs exactly one full build and reloads the database. Ordinary stale
  sources do not cause an unrelated build.
- `CMakeCache.txt` is read through the bounded no-follow regular-file boundary
  with a 4 MiB maximum. Only generator, export and unity metadata are consumed.
- Unsupported generators, disabled export, unknown unity values, unity units,
  and generation failures remain bounded compilation diagnostics.
- CMake subdirectory output paths are reconciled against both the entry working
  directory and database parent only when the two resolutions identify the same
  output file; containment validation is not relaxed.

### 3. Report and cache provenance

- `CompilationContext` records `origin = "cmake"`, generator, unity state,
  database digest and diagnostics.
- Each `CompilationUnit` keeps normalized compiler metadata and a target derived
  from `CMakeFiles/<target>.dir` output paths.
- JSON report payloads and the HTML that embeds them retain the provenance
  through the existing redaction boundary. Cache identity includes database parse state plus origin,
  generator, unity and target metadata.

## Code Examples

The effective analysis configure is equivalent to:

```text
cmake -S <project> -B <project>/build/ici-cmake-build \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DCMAKE_UNITY_BUILD=OFF
```

The generated-source decision is intentionally narrow:

```text
load canonical compile_commands.json
if stale generated source is inside canonical shadow:
    build once
    reload compile_commands.json
```

The loader never executes commands recovered from the database.

## Verification Results

Local implementation evidence (preserved from the feature branch):

- Python 3.10: `pytest` 1,074 passed in 46.32s.
- Ruff check/format: 130 files; focused mypy: 11 source files clean.
- Reproducible pyz builds matched SHA-256
  `2874e081cc27e0fc7f77e1285229c5fd0ba2803a149ddf1c6e4a3c4fb4d6db90`.
- Packaging contained 10 pure-Python distributions and no certifi; smoke and
  Zero-CDN checks passed.
- Self verify: WARN, Pass 8 / Warn 4 / Skip 1; 1,074 tests; line/function/
  branch 88.7% / 97.2% / 79.7%; TEM 4.86; 113.38s; HTML 4,697,480 bytes;
  external dependencies 0.
- Candidate viewer: PASS, 5/5 production units, 20 configurations, 0 issues,
  23.27s.
- LogLens: PASS, 14/14, 40 configurations, 0 issues, 32.27s.
- Self-dogfood initially caught an unnecessary silent CMake inspection
  `OSError` path. The dead inspection was removed, and the final exception path
  passed. The extracted build-path module also returned `cmake.py` below ici's
  500-code-line warning threshold.

Merged PR evidence:

- [PR #101](https://github.com/jihoon22-lee/ici/pull/101) was squash-merged as
  [`459abbaa5d6c80d91dfe07e54403c9bf88e63602`](https://github.com/jihoon22-lee/ici/commit/459abbaa5d6c80d91dfe07e54403c9bf88e63602).
- [CI run 33386134812](https://github.com/jihoon22-lee/ici/actions/runs/33386134812)
  passed `Verify & Dogfood ici`, `Viewer GUI build Qt5`, `Viewer GUI build Qt6`,
  `Publish PR Report & Sticky Comment`, and `Merge Gate`; `Publish Main` was
  expectedly skipped for the PR.
- The [sticky comment](https://github.com/jihoon22-lee/ici/pull/101#issuecomment-5477565364)
  contains both ici and viewer HTML report links. CI stats were ici WARN
  (Pass 8, Warn 4, Fail 0, Error 0, Skip 1, TEM 4.86, 1,074 tests, branch
  79.8%) and viewer PASS (Pass 11, Warn 0, Fail 0, Error 0, Skip 2, TEM 4.89,
  7 tests, compile_db 5/5 production units, 20 configurations, 0 issues).
- Independent [ici Pages](https://jihoon22-lee.github.io/ici/ici/pr/101/) and
  [viewer Pages](https://jihoon22-lee.github.io/ici/viewer/pr/101/) checks both
  returned HTTP/2 200 `text/html` with a title and zero external dependencies;
  observed sizes were 4,574,483 and 337,918 bytes respectively.

## Next Steps

- Run buildscope target-by-target command comparison and record the result; this
  remains the only incomplete I3-2 checklist item.
- Continue with I3-3 qmake capture and I3-4 lint/include graph migration. I3 as
  a whole is not complete.
