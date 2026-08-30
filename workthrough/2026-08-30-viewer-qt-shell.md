# Viewer Qt Shell Integration and Failure-State Coverage

## Overview

The `viewer` GUI is now part of the root CMake project without making the
`icirv` CLI depend on Qt. `MainWindow` failure handling clears all state left by
a previously loaded report, and a headless QtTest exercises both replacement
failure paths after a real report has been loaded.

## Context

The GUI previously had its own CMake project, so the root CMake/CTest adapter
could not build or test the Qt shell. A failed replacement also left the prior
suite, model rows, score label, and window title visible. The project supports a
static CLI for Qt-free deployment and must also be buildable on Qt 5.15 and Qt 6
hosts.

## Changes Made

### Root CMake and build workflows

- `viewer/CMakeLists.txt` now exposes `ICIRV_BUILD_GUI` (default `ON`), keeps
  `icirv_core` and `icirv` Qt-free, and adds the optional GUI subdirectory.
- `viewer/src/gui/CMakeLists.txt` now exports `icirv_gui` and `icirv-gui` rather
  than defining a separate CMake project and duplicate core library.
- Qt 6 is preferred through `find_package(QT NAMES Qt6 Qt5 ...)`; an explicit
  `CMAKE_DISABLE_FIND_PACKAGE_Qt6=ON` branch selects Qt 5 reliably because the
  CMake NAMES probe otherwise records the disabled first candidate as the major.
- `.github/workflows/ci.yml` and `.github/workflows/release.yml` use the root
  configure tree, build `icirv-gui` from `src/gui`, and configure the static
  CLI with `ICIRV_BUILD_GUI=OFF`.

### MainWindow state and QtTest

- `viewer/src/gui/main_window.cpp` now has one `clearReport` path for unreadable,
  malformed, and schema-invalid reports. It resets `suite_`, the model, gate
  and score labels, status text, and loaded window title.
- Stable object names were added to the tested labels and tree. A read-only
  `hasLoadedReport()` seam lets the Qt test assert suite state without exposing
  mutable internals.
- `viewer/tests/test_main_window.cpp` covers loading the real report, missing
  and malformed replacement failures, stale-state clearing, and PASS/WARN/
  SKIP/FAIL gate colour selection.

### Configuration and documentation

- `viewer/ici.toml` lists both `Qt6Widgets` and `Qt5Widgets`; the CMake adapter
  still builds the GUI while the generic fallback retains its external-build
  directory setting.
- `CHANGELOG.md` records the root integration, Qt matrix, shell tests, and
  stale-state fix.
- `docs/user-guide.md` documents Qt-free CLI and optional GUI commands plus the
  forced Qt 5 configure switch.

## Verification Results

### Native builds and tests

```text
ICIRV_BUILD_GUI=OFF: configure/build succeeded; ldd: not a dynamic executable
Qt 6: 4/4 CTest tests passed (offscreen)
Qt 5.15 (CMAKE_DISABLE_FIND_PACKAGE_Qt6=ON): 4/4 CTest tests passed (offscreen)
```

### ici and Python quality gates

```text
uv run --python 3.10 pytest: 625 passed in 26.99s
uvx ruff check .: All checks passed
uvx ruff format --check .: 84 files already formatted
ici viewer verify: PASS — 4/4 tests, branch 80.2%, TEM 4.86/5.0
./scripts/build-pyz.sh: completed; dist/ici.pyz 2.0M
./scripts/smoke.sh: completed all integrity and Zero-CDN checks
```

The work was split into these conventional commits:

- `e77fca8` `build(viewer): bring the GUI into the root project`
- `78702ad` `test(viewer): cover the report-loading path in the shell`
- `b94320d` `docs(viewer): document the Qt build matrix and shell state`
- `49ca197` `test(viewer): cover gate status colours`
- `125e3e7` `fix(viewer): link the GUI against the detected Qt major`

No push, PR, or merge was performed from this worktree.
