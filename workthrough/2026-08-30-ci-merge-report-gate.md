# CI Merge and Published-Report Gate

## Overview

CI now has one stable `Merge Gate` that requires the Python/self verification, Qt viewer build, sticky PR comment, and published HTML reports. The publisher also treats a failed PR comment as a failed publish instead of accepting an HTML upload alone.

## Context

The repository ruleset required only `Verify & Dogfood ici`. A broken Qt GUI or report comment therefore did not mechanically block a merge. Separately, `PublishResult.success` reflected the Contents API upload but ignored `_upsert_comment()` failure, and Pages could return a short-lived 404 after the comment was posted.

## Changes Made

### `/home/jihoon/projects/ici/src/ici/engines/publish.py`

- Requires a sticky comment URL for PR-mode success.
- Creates viewer URLs only after the corresponding upload succeeds.
- Makes single and multi-report comment failures return a hard failure.
- Formats multiple remote paths as separate paths rather than one invalid slash-joined path.

```python
success = uploaded and (pr_number is None or comment_url is not None)
```

### `/home/jihoon/projects/ici/.github/workflows/ci.yml`

- Makes `report-pr` wait for verify and viewer GUI results and run for generated failure reports.
- Serializes writes to `gh-pages` with a shared concurrency group.
- Reads the actual current-run sticky comment, finds both report URLs with pagination, and polls cache-busting URLs until both return HTML.
- Adds the stable `Merge Gate` result aggregator.

### Tests and documentation

- Extended `tests/test_publish.py` with comment-write and stale-URL failure contracts.
- Extended `tests/test_purity.py` with workflow dependency, permissions, publication, pagination, and gate assertions.
- Updated `CHANGELOG.md`, `README.md`, CI/user/architecture guides, and the stale handover state.
- Corrected the documented HTML navigation count from eight to nine tabs.

## Verification Results

```text
uv run --python 3.10 pytest: 628 passed before rebasing the already-merged cycle change
tests/test_purity.py + tests/test_publish.py: 33 passed
ruff check/format: passed
actionlint ci.yml release.yml: passed
build-pyz.sh and smoke.sh: passed
self verify: Pass 7, Warn 5, Fail 0, Error 0, TEM 4.78
```

## Next Steps

- Make `Merge Gate` the only required status check in the GitHub ruleset after this workflow is present on `main`.
- Apply the same aggregate gate and URL verification contract to `toy-projects`.
- Harden release ancestry and main-CI provenance before the next tag.
