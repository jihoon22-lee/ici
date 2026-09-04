# Make-backed sanitizer process evidence fix

## Overview

Make-backed sanitizer adapter runs recorded their test command as `make test`, but the sanitizer
engine only recognized `ctest` and `make check` as process evidence. A successful Make test run
was therefore incorrectly reported as `ERROR`/`NOT_RUN`. The evidence matcher and focused
sanitizer/ThreadSanitizer regressions now cover the actual Make command.

## Changes Made

- `src/ici/engines/sanitize.py`
  - Accepted `make test` in `_adapter_process_evidence_index()` while preserving CTest and qmake
    evidence names.
- `tests/test_sanitize_engine.py`
  - Added a clean Make adapter case asserting `PASS`, `MEASURED`, zero sanitizer issues, and the
    recorded `make test` process evidence.
- `tests/test_thread_sanitize_engine.py`
  - Added Make to the existing adapter parametrization and asserted measured evidence for the
    Make diagnostic path.
- `CHANGELOG.md`
  - Recorded the unreleased defect correction without changing the stable version.

## Code Example

```python
if self._tool_evidence[index].name in {"ctest", "make check", "make test"}:
    return index
```

## Verification Results

```text
uv run --python 3.10 pytest -q \
  tests/test_sanitize_engine.py::test_adapter_sanitizer_accepts_make_test_process_evidence \
  tests/test_thread_sanitize_engine.py::test_adapter_thread_sanitize_keeps_process_linked_diagnostic
4 passed

uv run --python 3.10 pytest -q tests/test_sanitize_engine.py tests/test_thread_sanitize_engine.py
54 passed

uvx ruff check src/ici/engines/sanitize.py tests/test_sanitize_engine.py tests/test_thread_sanitize_engine.py
All checks passed!

uvx ruff format --check src/ici/engines/sanitize.py tests/test_sanitize_engine.py tests/test_thread_sanitize_engine.py
3 files already formatted

uv run --python 3.10 pytest
2548 passed, 9 skipped

uv run --python 3.10 mypy src
Success: no issues found in 133 source files

uvx ruff check .
All checks passed!

uvx ruff format --check .
239 files already formatted

./scripts/build-pyz.sh
dist/ici.pyz SHA-256 23d9922b94b2ba34ab8884cd2d39c8eda358ccb32d0925af5c0a3d52a7ddc893

./scripts/smoke.sh
All smoke checks passed, including Python 3.10 execution and Zero-CDN HTML validation.
```

The initial plain-system `pytest` invocation was not usable because that interpreter lacked the
project dependency `tomli`; the prescribed Python 3.10 `uv run` environment passed all tests.

The built candidate was also run uncached with the deep profile against a clean AbiLens source
tree using its configured Make adapter. The suite completed with no failures or errors: complexity
reported a maximum of 14, both sanitizer engines reported `PASS`, and build, binary compatibility,
and both integration contracts passed. The overall suite was `WARN` only for the expected
clang-tidy/clazy and compilation-database limitations plus estimated coverage evidence.
