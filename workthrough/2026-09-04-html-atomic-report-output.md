# Atomic HTML Report Output — 2026-09-04

## Overview

The HTML reporter now publishes reports through a same-directory temporary file and
atomic replacement. This prevents an existing output symlink from being followed and
keeps incomplete reports from being published.

## Changes Made

- Added `_save_html()` in `src/ici/reporters/html/report.py`.
  It writes UTF-8 content to a uniquely named temporary file, flushes and `fsync`s it,
  then replaces the requested output path. Cleanup runs for encoding, fsync, and
  replacement failures.
- Added focused regressions in `tests/test_html_large_report.py` for symlink replacement,
  referent preservation, temporary-file cleanup, and injected fsync failure cleanup.

## Verification Results

```text
uv run --offline pytest -q tests/test_html_large_report.py tests/test_reporters.py
11 passed

uvx ruff check src/ici/reporters/html/report.py tests/test_html_large_report.py
All checks passed!

uvx ruff format --check src/ici/reporters/html/report.py tests/test_html_large_report.py
2 files already formatted
```
