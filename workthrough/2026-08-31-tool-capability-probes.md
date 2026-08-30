# Tool Capability Probe Contract Tests

## Overview

This work adds a focused contract suite for the I2-1 tool capability inventory surface. The
tests exercise parsing, bounded subprocess evidence, redaction, immutable metadata, registry
resolution, and the distinction between an installed tool and a complete capability record.
The production implementation provides that contract in `src/ici/core/toolchain.py`; the
focused tests were written independently and exposed one target-triple validation defect before
the implementation was finalized.

## Context

I2-1 separates local tool discovery from the existing project-specific build adapters and from
the I1-2 engine support matrix. The requested inventory covers Python, compilers, CMake, qmake,
make, Ninja, gcov, clang tools, Qt, and binutils, while retaining enough evidence for later
doctor/verify inventory sharing. These tests are the contract/probe half of that split; they do
not claim that the full inventory is wired into every reporter or verification path.

## Changes Made

### Capability contract tests

File: `tests/test_toolchain_capabilities.py`

- Covers GCC, Ubuntu/Apple clang, Python `-VV`, CMake, qmake, GNU make, plain numeric Ninja,
  and binutils version output across multiline stdout/stderr combinations.
- Ensures target triples are not mistaken for display versions.
- Verifies qmake Qt version/major, spec, prefix, normalized feature metadata, and credential
  redaction.
- Verifies deterministic CMake generator sorting/deduplication, count/character bounds, and
  malformed JSON handling.
- Mocks `shutil.which` and the imported `run_process` symbol with deterministic
  `ici.core.runner.ProcessResult` values for missing executables, nonzero exits, timeouts,
  truncation, output-limit forwarding, parse-incomplete success, and secret argv evidence.
- Verifies immutable `ToolCapability.details`, qmake6-first/qmake fallback resolution,
  compiler target metadata, metadata failure degradation, malformed target handling, CMake
  metadata, and deterministic default registry coverage.

### Representative contract

```python
capability, result = collect_tool_capability("mystery", ["mystery", "--version"])

assert result.returncode == 0
assert capability.available is True
assert capability.complete is False  # executable exists, but version is not parseable
```

## Verification Results

```text
uv run --python 3.10 pytest -q tests/test_toolchain_capabilities.py
35 passed

uvx ruff check src/ici/core/toolchain.py tests/test_toolchain_capabilities.py
All checks passed!

uvx ruff format --check src/ici/core/toolchain.py tests/test_toolchain_capabilities.py
2 files already formatted
```

The independent suite initially found that a lexical hyphen-only target check accepted
`not-a-triple`. The implementation now requires both a recognized architecture family and a
platform/ABI marker, while covering ordinary GNU/Linux and bare-metal ARM triples. The retained
regression test passes.

The full pytest/build/smoke quality gate, integration wiring, commit, push, and PR evidence are
not claimed by this workthrough and were not run in this focused test task.

## Next Steps

- Integrate the registry with the intended I2-1 doctor/verify shared inventory only after that
  contract is green; then run the full repository quality gates in the implementation change.
