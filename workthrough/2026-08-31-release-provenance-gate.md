# Release Provenance Gate

## Overview

The release workflow now binds every published asset to an existing tag commit
that is reachable from `main` and has a successful `Merge Gate` check on that
exact SHA. A read-only provenance job resolves and validates the commit before
the separate write-enabled build job can start.

## Context

The previous workflow compared the event tag with `__version__`, but a manual
dispatch built whichever branch was selected in the Actions UI. It also did not
prove that a tag belonged to `main` or that the tagged commit passed CI. The
release build ran pytest and smoke checks but omitted Ruff and both ici
dogfood gates.

## Changes Made

### Read-only provenance validation

- The workflow defaults to no permissions. `validate-release` receives only
  `contents: read` and `checks: read`.
- Both tag-push and manual events resolve an existing `vX.Y.Z` tag to its commit
  and detach at that SHA.
- `git merge-base --is-ancestor` requires the commit to be reachable from
  `origin/main`.
- The GitHub Checks API is polled for a successful `Merge Gate` attached to the
  exact release SHA. The bounded poll accommodates a tag created while the
  post-merge workflow is still finishing.
- The tag, package `__version__`, and versioned CHANGELOG section must agree.

### Exact candidate build and evidence

- `build-release` depends on the provenance job, checks out its `target_sha`,
  confirms `HEAD`, and is the only job with `contents: write`.
- Ruff check/format, the Python 3.10 test suite, reproducible pyz build, and the
  isolated smoke test run before publication.
- The newly built pyz verifies both ici itself and the C++/Qt viewer with the Qt
  offscreen platform. The release GUI build also runs all CTests and a real
  report-open smoke test.
- The self and viewer HTML/JSON reports are retained as release assets so the
  gate evidence travels with the binary and checksums.

## Code Examples

The decisive ancestry and exact-check contracts are:

```bash
git merge-base --is-ancestor "$TARGET_SHA" refs/remotes/origin/main
gh api "repos/${GITHUB_REPOSITORY}/commits/${TARGET_SHA}/check-runs"
```

The write-enabled job consumes the validated output rather than an event ref:

```yaml
needs: validate-release
with:
  ref: ${{ needs.validate-release.outputs.target_sha }}
```

## Verification Results

- `actionlint -color`: passed.
- `uv run --python 3.10 pytest -q tests/test_purity.py`: 13 passed.
- Static policy tests cover explicit tags, detached checkout, main ancestry,
  exact-SHA `Merge Gate`, permission separation, candidate checkout, Ruff,
  pyz smoke, both dogfood gates, and release report assets.
- Full repository and packaging gates are run before the PR is published and
  again by CI. The live release execution is intentionally deferred until the
  final version commit is merged and its main `Merge Gate` succeeds.

## Next Steps

When preparing a release, merge the version bump through a green PR, wait for
the resulting main SHA's `Merge Gate`, create the matching tag, and verify the
release workflow plus every uploaded checksum/report asset. A tag outside main,
a stale manual-dispatch branch, or a commit without the exact gate now fails
before any write-enabled build begins.
