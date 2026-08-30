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
`01a0b01` on `feat/capability-reporting`. The follow-up import-cycle correction is in `0a38b68`
and `9d24815`.

The recorded local evidence is:

- `uv run --python 3.10 pytest`: **807 passed**.
- `uvx ruff check .` and `uvx ruff format --check .`: passed.
- `./scripts/smoke.sh`: passed.
- `./scripts/build-pyz.sh` run twice produced the reproducible SHA-256
  `5fd461d65add85d1ff7ae6d9267673e9db562cfd1c0b087f0c346214a2531546`. This measurement
  predates the final import-cycle correction, so a final rebuild and hash are still pending.
- Self verify completed with exit 0 and policy **WARN** due to existing findings: 12 engines,
  8 PASS / 4 WARN / 0 FAIL / 0 ERROR, TEM **4.84**, and **159.99s**. The verify result carries
  exactly one capability snapshot with **30 tools**: **21 ready / 0 incomplete / 9 unavailable**;
  required `ruff`, `pytest`, and `python3` were ready and capability health was **READY**.
- Self verify exposed a newly introduced core import cycle,
  `capabilities -> redaction -> models -> capabilities`. The cycle was fixed by extracting the
  model-independent `redaction_values` module; the focused cycle rerun now reports only the
  single pre-existing `test/test_interpreter` cycle.

These are local results only. Remote CI, PR checks, sticky comments, and Pages HTTP/Zero-CDN
evidence are not claimed in this workthrough.

## Next Steps

- Rebuild the pyz after the import-cycle correction and record its final reproducibility hash.
- Retain the optional field when consuming legacy `ici.result/v3` reports.
