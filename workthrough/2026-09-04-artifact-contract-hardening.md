# Artifact Contract Boundary Hardening

## Overview

The release-artifact follow-up closed two trust-boundary gaps found during the
final feature audit. Reports containing producer metadata now validate against
the checked-in `ici.result/v3` JSON Schema, and build artifact discovery and
hashing are explicitly bounded.

## Changes Made

- `src/ici/schemas/ici-result-v3.schema.json`
  - Defines strict v1 and v2 artifact records.
  - Requires v2 `id`, `target`, and `command` fields while retaining v1
    compatibility.
  - Bounds record counts, file sizes, command lengths, and accepts the existing
    redacted external compilation path without an overlapping `oneOf` failure.
- `src/ici/core/context.py`
  - Limits manifests to 512 records, 512 MiB per artifact, and 1 GiB total.
  - Applies byte bounds while streaming hashes and again at validation.
- `src/ici/core/cmake.py` and `src/ici/engines/build.py`
  - Bound recursive discovery to 200,000 entries and 512 linked artifacts.
  - Skip symlinks and object/coverage intermediates.
  - Require linked-binary or archive magic instead of trusting a library-like
    filename suffix.
- Contract tests validate both manifest schema versions with a real Draft 2020-12
  validator and cover record, byte, discovery, and false-positive boundaries.

## Verification Results

```text
uv run --python 3.10 --with jsonschema pytest -q \
  tests/test_context_reporting.py tests/test_artifact_manifest.py \
  tests/test_build_manifest_integration.py tests/test_build_engine.py
passed

uvx ruff check <changed Python files>
All checks passed!

uvx ruff format --check <changed Python files>
All files already formatted
```
