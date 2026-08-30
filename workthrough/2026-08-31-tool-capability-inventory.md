# Shared Policy-Aware Capability Inventory

## Overview

I2-1 Slice 2 connects the bounded tool probe registry to `ici doctor`. The result is an
immutable, machine-readable capability snapshot that explains not only whether a tool is
installed, but also which active engine/language scope or explicit doctor policy requires it.
The inventory and policy model now lives in `src/ici/core/capabilities.py`, while bounded probe
execution remains in `src/ici/core/toolchain.py`; the latter is 482 lines after the separation.
The verify/report hand-off is completed in the follow-up
[`2026-08-31-verify-capability-reporting.md`](2026-08-31-verify-capability-reporting.md); this
document remains the Slice 2 doctor-focused history.

## Design

### Immutable domain objects

- `src/ici/core/capabilities.py` owns the inventory, requirement policy, collection orchestration,
  and JSON serialization.
- `src/ici/core/toolchain.py` owns the 482-line bounded probe/parser registry and external-tool
  evidence collection.
- `ToolRequirement(name, required_by, optional_by)` records requirement provenance per tool.
- `CapabilityInventory` stores capability and requirement mappings behind `MappingProxyType`.
- `missing_required` and `incomplete_required` distinguish an absent executable from an
  executable whose bounded probe did not produce complete evidence.
- `healthy` is false for either required condition, while an optional unavailable tool does not
  make the inventory unhealthy.

### Shared doctor collection

`collect_diagnostics()` evaluates the project support matrix first, then converts each applicable
and enabled `required_tools`/`optional_tools` declaration into an `engine:language` policy source.
Explicit `[doctor].required_tools` entries are added as `doctor.config` requirements. The same
`DEFAULT_TOOL_PROBES` tuple is passed to `collect_capability_inventory()` for every doctor mode,
including the JSON and brief views.

The serialized top-level `capability_inventory` uses the `ici.capabilities/v1` shape with health,
counts, missing/incomplete required names, and per-tool state, version, path, details, argv, and
bounded execution evidence. The historical `tools` mapping is projected from those rows so
existing consumers retain their lookup shape.

### Redaction and bounded execution

Probe argv and metadata pass through the existing recursive redaction boundary. Secret flag
values are masked before evidence is retained, and the registry continues to use direct argv
execution with the existing timeout and output-size limits. Unknown policy-only tools become
explicit unavailable capabilities with an error and no `which()` or subprocess call.

## Contract Tests

The contract tests cover deterministic registry order, duplicate probe rejection,
unknown required tools, required incomplete and optional missing health semantics, immutable
mapping surfaces, JSON round-tripping, evidence argv redaction, and doctor policy/source
projection. They use fake `which()` and process results, so the contract suite does not depend on
the host toolchain.

## Verification Results

```text
uv run --python 3.10 pytest
799 passed

uvx ruff check .
All checks passed!

uvx ruff format --check .
101 files already formatted

uv run --python 3.10 mypy src/ici/core/capabilities.py src/ici/core/toolchain.py src/ici/doctor.py
Success: no issues found in 3 source files

./scripts/build-pyz.sh
pure-Python 10 distributions, no certifi, reproducible 2.0 MiB pyz

./scripts/smoke.sh
all launcher, Python 3.10, artifact integrity, self-dogfood, and Zero-CDN checks passed

git diff --check
clean
```

The final self-verify remained a policy WARN with 8 PASS, 4 WARN, 0 FAIL, 0 ERROR, and 0 SKIP
engine results. Its test engine measured 799/799 and TEM 4.83; line/function/branch coverage was
88.0%/96.7%/78.8%. The separated implementation produced 8 line issues (one fewer than the
pre-separation self-analysis), complexity maximum 23 with 66 issues, and duplicate rate 16.14%
across 350 groups. The generated HTML was 3,513,188 bytes with zero external references.

## Follow-up

Slice 3 adds the suite-level verify snapshot, reporter projections, and schema compatibility.
See the follow-up workthrough for the implementation contract and the remaining final-gate
evidence status.
