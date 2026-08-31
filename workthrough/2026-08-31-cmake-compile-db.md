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
- JSON/HTML/Markdown projections retain the provenance through the existing
  redaction boundary. Cache identity includes database parse state plus origin,
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

Local branch evidence (not PR, CI, or Pages evidence):

- Python 3.10: `pytest` 1,074 passed in 49.03s.
- Ruff check/format: 129 files; focused mypy: 10 source files clean.
- Reproducible pyz builds matched SHA-256
  `7ef7bc9b384771cc87246ab7d74d962a80cf1412cc397205512a112aef5c9ca5`.
- Packaging contained 10 pure-Python distributions and no certifi; smoke and
  Zero-CDN checks passed.
- Self verify: WARN, Pass 8 / Warn 4 / Skip 1; 1,074 tests; line/function/
  branch 88.7% / 97.2% / 79.7%; TEM 4.86; 110.73s; HTML 4,694,394 bytes;
  external dependencies 0.
- Candidate viewer: PASS, 5/5 production units, 20 configurations, 0 issues,
  7.58s.
- LogLens: PASS, 14/14, 40 configurations, 0 issues, 29.81s.
- Self-dogfood initially caught a silent CMake inspection `OSError` handler. The
  handler was fixed, and the final exception path passed.

## Next Steps

- Run buildscope target-by-target command comparison and record the result.
- Open the I3-2 PR only after the full local gate; require CI Merge Gate before
  merging and independently verify both PR HTML Pages links.
- Continue with I3-3 qmake capture and I3-4 lint/include graph migration. I3 as
  a whole is not complete.
