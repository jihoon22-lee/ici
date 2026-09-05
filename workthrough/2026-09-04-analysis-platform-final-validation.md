# Analysis Platform Final Local Validation — 2026-09-04

## Overview

The consolidated analysis-platform branch was validated as one delivery before remote PR
acceptance. The scope combines Python package and wheel inspection, configured Make builds,
typed artifact provenance, ELF deployment compatibility, cross-language integration cases,
deep test-quality observations, deterministic SARIF, bounded large HTML reports, and candidate
validation against open toy-projects PRs. Stable version `0.10.2` was intentionally unchanged;
this validation does not authorize a release.

## Final Hardening

- Split wheel archive inspection, test-quality observations, integration configuration parsing,
  and analysis configuration validation into cache-identified modules below their hard file and
  complexity limits.
- Removed stale type suppressions found by the stricter self-dogfood Mypy overlay. Both the normal
  project profile and `--warn-unused-ignores` now report zero findings across 133 source files.
- Replaced the LLVM 18 clang-tidy transcript coordinator with focused state-transition helpers.
  `_split_clang_tidy_text` measures cyclomatic complexity 5 and cognitive complexity 6, down from
  cognitive complexity 73. Generated-warning summaries and header-filter hints terminate the
  structural-note context, so detached empty notes are rejected atomically.
- Added focused real-contract coverage for manifest-backed ELF inspection, optional and required
  integration outcomes, and LLVM multi-pair note order, locations, and terminal boundaries.

## Quality Gates

All commands ran from the final feature head with Python 3.10 compatibility enabled.

```text
uv run --python 3.10 pytest
2546 passed, 9 skipped in 100.81s

uv run --python 3.10 mypy src/ici
Success: no issues found in 133 source files

uv run --python 3.10 mypy --warn-unused-ignores src/ici
Success: no issues found in 133 source files

uv run --python 3.10 mypy scripts/candidate_merge_gate.py
Success: no issues found in 1 source file

uvx ruff check .
All checks passed!

uvx ruff format --check .
239 files already formatted

actionlint
PASS

git diff --check
PASS
```

The nine skips were explicit capability/runtime boundaries: one base-environment JSON Schema
check (validated separately below), six real clang/clang-tidy/clazy/Qt tool cases unavailable on
this host, one unavailable `clang++` trace case, and one newer-syntax case that Python 3.10's host
AST cannot parse. They were retained as skips rather than converted to synthetic passes.

## Reproducible Artifact and Smoke

Two consecutive `./scripts/build-pyz.sh` executions produced the same artifact:

```text
dist/ici.pyz
SHA-256 50d41d36775394f66f6620091f42a7a0333ee90758e19449a848d7ee0875a93c
11 distributions: all py3-none-any; certifi absent
2 public JSON Schemas packaged
```

`./scripts/smoke.sh` passed direct launcher execution, `doctor --brief`, shell environment output,
direct Python 3.10 execution, `dist/ici`/`dist/ici.pyz` byte identity, report integrity, and the
Zero-CDN HTML check.

## Deep Self-Dogfood

The exact packaged artifact ran with `verify --profile deep --no-cache` and emitted JSON, HTML,
and SARIF in one pass:

```text
Suite: WARN
Engines: 16 (10 PASS, 4 WARN, 0 FAIL, 0 ERROR, 2 SKIP)
Tests: 2546 / 2555 passed
Coverage: line 89.0%, function 96.2%, branch 80.9%
TEM: 4.79 / 5.0
Cache hits: 0
Duration: 271.06s
Actionable findings: 2075
```

`type`, `python_compat`, `resource`, `security`, `cycle`, `sanitize`, `dead`, and `exception`
passed. `compile_db` and `thread_sanitize` correctly skipped because ici itself has no production
C/C++ translation unit in scope. The four warnings remain visible structural debt rather than
being suppressed: 31 oversized-file observations, 113 cognitive-complexity observations, 148
cyclomatic/nesting observations with overall maximum CC 25, and 11.33% duplicate code. A diff-to-
`origin/main` audit found no hard-threshold cyclomatic or cognitive finding in changed files; the
clang-tidy coordinator no longer appears in either warning inventory.

Deep test mutation settings remain capability/report-only and were not treated as executed
mutation evidence. Real C++/Qt and cross-repository release-contract acceptance therefore remains
the responsibility of CI and the candidate Quality Zoo/toy-projects run.

## Report Contract Validation

```text
verify_report.json       21,745,139 bytes — ici.result/v3, Draft 2020-12 valid
ici-final-self.html       6,046,270 bytes — published HTML contract and Zero-CDN valid
ici-final-self.sarif      7,542,292 bytes — SARIF 2.1.0, 1 run, 13 rules, 7,987 results
```

The JSON report was validated with `jsonschema.Draft202012Validator` against the checked-in public
schema. The HTML was independently checked with toy-projects' publication contract verifier. SARIF
shape and exact rule/result counts were checked with `jq`.

## Release Decision

No version, tag, GitHub release, or stable artifact was created. The public stable version remains
`0.10.2`. A future version decision requires green PR and exact-main CI/Pages plus exact candidate
acceptance in toy-projects; one feature PR is not itself a release boundary.
