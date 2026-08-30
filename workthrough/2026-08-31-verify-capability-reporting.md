# Verify/Report Shared Capability Snapshot

## Overview

I2-1 Slice 3 makes the bounded tool capability inventory a single per-run input to
`ici verify` and every reporter. The effective project support policy is calculated before
engines run, the registry is collected exactly once, and the immutable suite-level snapshot is
then reused by JSON, console, Markdown, and zero-CDN HTML output. This closes the stale
doctor-only boundary while keeping the v3 field optional for older reports.

## Context

Slice 1 introduced the deterministic bounded probe registry and Slice 2 connected it to
`ici doctor`. Before this slice, `verify` and its reporters had no shared capability snapshot;
adding independent probes in reporters would have duplicated subprocess work and could have
produced inconsistent evidence. The implementation therefore keeps collection in the verify
orchestrator and treats reporter output as a projection of the attached result.

## Changes Made

### 1. Effective verify policy and one collection point

- `src/ici/engines/verify.py` evaluates the support matrix before constructing engines.
- Only support rows that are both `applicable` and `enabled` contribute engine/language tool
  requirements; configured `[doctor].required_tools` remain `doctor.config` requirements.
- Required provenance promotes a tool over optional use while preserving every optional consumer
  in the resulting requirement record.
- `collect_capability_inventory()` is called exactly once before the first engine and the same
  immutable `CapabilityInventory` is attached to `VerificationSuiteResult`.

### 2. Shared model, redaction, and schema

- `src/ici/core/models.py` gives the suite an optional `capability_inventory` field.
- `src/ici/core/capabilities.py` owns the shared policy derivation and stable serialization.
- `src/ici/core/redaction.py` preserves the inventory shape while redacting capability name,
  path, version, error, details, probe argv, and evidence fields.
- `src/ici/reporters/json_rep.py` writes the snapshot once at the suite root as optional
  `capability_inventory`; engine results do not duplicate it.
- `src/ici/schemas/ici-result-v3.schema.json` declares the optional field without changing the
  `ici.result/v3` schema version. Reports without the field remain valid and serializable.

### 3. Reporter projections

- `src/ici/reporters/console.py` shows compact health and ready/incomplete/unavailable counts.
- `src/ici/reporters/markdown.py` shows a short health summary and a collapsed complete tool
  inventory with requirement provenance.
- `src/ici/reporters/html/report.py` and
  `src/ici/reporters/html/sections/support.py` add the complete capability rows to the existing
  zero-CDN **Support & Capabilities** tab, with actionable rows first.
- No reporter calls the probe collector; all projections consume the suite snapshot.

## Contract Tests

`tests/test_capability_reporting.py` fixes the boundary with deterministic fakes:

- applicable/enabled policy filtering, deterministic ordering, and required-over-optional
  provenance;
- one pre-engine collection and exact snapshot identity on the suite;
- JSON root placement without engine duplication;
- recursive redaction of capability metadata and bounded evidence;
- reporter reuse without re-probing;
- backward-compatible absent inventory serialization; and
- checked-in schema declaration for the optional root field.

## Verification Status

The implementation and contract-test changes are present in commits `2048ad3`, `e0fd024`, and
`01a0b01` on `feat/capability-reporting`. This documentation-only follow-up verifies formatting
with `git diff --check`.

The final full quality gate (Python 3.10 test suite, Ruff, reproducible pyz, smoke, and self
verify), CI workflow, PR checks, sticky HTML comment, and Pages HTTP/Zero-CDN evidence are
intentionally **pending main integration** and must be recorded after the parent branch runs
those checks. No result is claimed here.

## Next Steps

- Run the complete local and remote gates from the main integration workflow.
- Confirm the PR sticky comment links the generated HTML and that published Pages return HTTP 200
  with zero external script/stylesheet references.
- Retain the optional field when consuming legacy `ici.result/v3` reports.
