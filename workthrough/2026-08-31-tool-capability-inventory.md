# Shared Policy-Aware Capability Inventory

## Overview

I2-1 Slice 2 connects the bounded tool probe registry to `ici doctor`. The result is an
immutable, machine-readable capability snapshot that explains not only whether a tool is
installed, but also which active engine/language scope or explicit doctor policy requires it.
The implementation deliberately keeps verify/report inventory sharing and profile-level policy
out of this slice.

## Design

### Immutable domain objects

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

## Tests

The focused contract tests cover deterministic registry order, duplicate probe rejection,
unknown required tools, required incomplete and optional missing health semantics, immutable
mapping surfaces, JSON round-tripping, evidence argv redaction, and doctor policy/source
projection. They use fake `which()` and process results, so the contract suite does not depend on
the host toolchain.

## Verification Results

```text
uv run --python 3.10 pytest -q tests/test_toolchain_inventory.py tests/test_doctor.py
14 passed

uvx ruff check tests/test_toolchain_inventory.py tests/test_doctor.py
All checks passed!

uvx ruff format --check tests/test_toolchain_inventory.py tests/test_doctor.py
2 files already formatted

git diff --check
clean
```

These are focused Slice 2 checks only. The full repository pytest/Ruff/pyz/smoke gate and remote
PR evidence are intentionally not claimed by this workthrough.

## Next Steps

- Add the same inventory to verify/report outputs without probing the toolchain twice.
- Define complete engine/profile requirement policy and its reporter/schema compatibility before
  closing I2-1 Slice 3.
