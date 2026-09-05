# Wheel Inspection Boundary Hardening

## Overview

Hardened the Python wheel inspector at archive and metadata trust boundaries.
Wheel members now require one canonical POSIX spelling, the archive must have
one root-level `.dist-info` directory matching the wheel filename identity, and
singleton metadata headers cannot be duplicated.

## Changes Made

- `src/ici/engines/_python_packaging.py`
  - Reject redundant separators, dot components, traversal, and ambiguous
    non-canonical member spellings, file/directory aliases, and non-empty
    directory entries while retaining canonical directory entries such as
    `demo/`.
  - Require exactly one root-level `.dist-info` directory and match its
    normalized distribution/version to the wheel filename.
  - Bind `WHEEL`, `METADATA`, `RECORD`, and `entry_points.txt` to that directory.
  - Reject duplicate `Name`/`Version` metadata and duplicate WHEEL singleton
    headers (`Wheel-Version`, `Generator`, and `Root-Is-Purelib`).
  - Parse singleton headers only from the metadata header section so legitimate
    `Version:` text in a long description is not treated as a duplicate.

- `tests/test_python_packaging.py`
  - Added canonical path rejection and canonical directory acceptance cases.
  - Added normalized dist-info identity, mismatched identity, and multiple
    dist-info directory cases.
  - Added duplicate singleton-header coverage for METADATA and WHEEL files.

## Verification Results

```text
uv run --python 3.10 pytest -q tests/test_python_packaging.py
..................................                                       [100%]

uvx ruff check src/ici/engines/_python_packaging.py tests/test_python_packaging.py
All checks passed!

uvx ruff format --check src/ici/engines/_python_packaging.py tests/test_python_packaging.py
2 files already formatted
```
