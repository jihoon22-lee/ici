# Task 3 implementation report — project boundaries and metadata parsing

## Scope

Implemented Task 3 on branch `fix/validation-hardening` from base commit
`b40d54b22c574f332b74b0fee04320c44367dc92`. Project path resolution and source discovery now
share canonical containment checks, and project metadata is parsed from TOML instead of
substring-matching source lines.

## Implementation

- Added `resolve_project_path()` with canonical `Path.resolve()` containment enforcement.
- Added safe source discovery that rejects explicitly configured escapes, ignores escaped default
  source links, and does not traverse symlink files or directories during Python/C++ scans.
- Applied the same boundary to project-signature files and C++ include directory discovery.
- Added `read_project_metadata()` using `tomli`; `ici.toml`/`dev.toml` consume top-level
  `name`/`version`, while `pyproject.toml` consumes only `[project]` metadata.
- Validated project names and versions against `[A-Za-z0-9._-]+`, normalized versions with a
  leading `v`, rejected path-special project names, and preserved the git/`v1.0.0` version
  fallback.
- Added regression coverage for parent traversal, outside symlinks, nested symlink traversal,
  malformed TOML, unsafe metadata, canonical resolution, and symlink-loop project roots.
- Synchronized the `[Unreleased]` `CHANGELOG.md` entry.

## TDD evidence

- RED: the new metadata test module failed collection because `read_project_metadata` and
  `resolve_project_path` were not present.
- GREEN: the initial project-layout and metadata regressions passed after the implementation
  (`17 passed`), followed by the additional metadata boundary suite (`9 passed`). The final
  focused run covered both modules with `19 passed`.
- RED: the dot-name regression initially did not raise; validation was tightened and the test
  passed (`1 passed`).
- RED: the symlink-loop project-root regression initially leaked `RuntimeError`; root resolution
  was normalized and the test passed (`9 passed` metadata suite).

## Verification

- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest tests/test_project_layout.py tests/test_project_metadata.py -v` — 19 passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest tests/test_complexity.py tests/test_test_engine.py tests/test_project_layout.py tests/test_project_metadata.py -v` — 32 passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest --tb=short` — 91 passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uvx ruff check .` — passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uvx ruff format --check src/ici/core/project.py tests/test_project_layout.py tests/test_project_metadata.py` — passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp ./scripts/build-pyz.sh` — passed; generated `dist/ici.pyz`
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp ./scripts/smoke.sh` — passed
- `git diff --check` — passed

## Concern

Repository-wide `uvx ruff format --check .` still reports two pre-existing unformatted planning
Markdown files outside this task:
`docs/superpowers/plans/2026-08-19-ci-validation-features.md` and
`docs/superpowers/plans/2026-08-19-existing-validation-hardening.md`. They were not changed.

## Fix round 1 — review findings addressed

### Important: parser recursion limit

`tomli` can raise `RecursionError` for a deeply nested dotted key before normal TOML parse
validation runs. The exception is now caught only inside the `tomli.load()` call and converted to
the existing controlled `ValueError` project-metadata error; filesystem exceptions remain handled
separately. The regression exercises a 5,000-part key through `read_project_metadata()`.

### Minor: C++ discovery and Git fallback coverage

Added C++ source tests covering escaped symlink files, escaped symlink directories, and symlink
loops, plus C++ include-directory tests covering escaped links and loops. Added tests for a
metadata version obtained from `git describe` and the `v1.0.0` fallback when Git is unavailable.

### Fix-round TDD and verification

- RED: the 5,000-part TOML key leaked raw `RecursionError`; the C++ and fallback regressions
  passed against the existing safe discovery/fallback implementation.
- GREEN: the parser-limit regression passes after the narrow `tomli.load()` catch; all five new
  fix-round regressions pass.
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest tests/test_project_layout.py tests/test_project_metadata.py -v` — 24 passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uv run --python 3.10 pytest --tb=short` — 96 passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uvx ruff check .` — passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp uvx ruff format --check src/ici/core/project.py tests/test_project_layout.py tests/test_project_metadata.py` — passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp ./scripts/build-pyz.sh` — passed
- `TMPDIR=/tmp TEMP=/tmp TMP=/tmp ./scripts/smoke.sh` — passed
- `git diff --check` — passed
