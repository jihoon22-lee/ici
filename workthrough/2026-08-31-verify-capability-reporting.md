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

- `uv run --python 3.10 pytest`: **807 passed in 40.32s**.
- `uvx ruff check .` and `uvx ruff format --check .`: passed for **103 files**.
- `./scripts/build-pyz.sh` run twice produced identical, reproducible SHA-256
  `0d91f4ab698aed53781669125200e5ae2291484c4083d2c181aacee06d5c80e2`; both artifacts contain
  10 pure-Python distributions and no `certifi`.
- `./scripts/smoke.sh`: passed, including the doctor capability summary (**21/30 ready**, required
  `ruff`/`pytest`/`python3` ready), Python 3.10, integrity, and Zero-CDN checks.
- Final self verify completed with exit 0 and policy **WARN** due to existing findings: 12 engines,
  **8 PASS / 4 WARN / 0 FAIL / 0 ERROR / 0 SKIP**, TEM **4.84**, and **105.98s**. The capability
  inventory is exactly **30 tools** (**21 ready / 0 incomplete / 9 unavailable**); required
  `ruff`, `pytest`, and `python3` are ready and capability health is **PASS**.
- The JSON report contains the capability inventory exactly once at the suite root, uses schema
  `ici.capabilities/v1`, and has no nested copies. The final HTML report at
  `/tmp/ici-capability-reporting-final.html` is **3,627,583 bytes**, contains the **Support &
  Capabilities** and **Capability health** text, and has zero external `src`/`href` references.
- The initial self verify exposed a core import cycle,
  `capabilities -> redaction -> models -> capabilities`. Extracting the model-independent
  `redaction_values` module fixed it; the final focused cycle rerun reports only the single
  pre-existing `test/test_interpreter` cycle.

These are local results only. Remote CI, PR checks, sticky comments, and Pages HTTP/Zero-CDN
evidence are not claimed in this workthrough.

## Next Steps

- Record remote CI, PR, sticky-comment, and Pages HTTP/Zero-CDN evidence after main integration.
- Retain the optional field when consuming legacy `ici.result/v3` reports.
