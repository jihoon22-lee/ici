# Runtime sanitizer diagnostic normalization — 2026-09-03

## Overview

This workthrough records the local sanitizer-normalization slice implemented on
`feat/sanitizer-diagnostic-normalization`. The slice turns bounded AddressSanitizer (ASan),
LeakSanitizer (LSan), and UndefinedBehaviorSanitizer (UBSan) output into deterministic source
findings while preserving the process evidence that produced each finding. It also closes the
stale CTest JUnit-report path and keeps raw adapter diagnostics private and bounded.

The implementation is present in commits `67dc117` (`feat(sanitize): normalize runtime sanitizer
diagnostics`), `5f031ba` (`test(build): reject sanitizer output from passing JUnit cases`),
`9a108dc` (`test(sanitize): model adapter process evidence in state fixtures`), `48831c3`
(`refactor(sanitize): use canonical sanitizer kind names`), and `69559ed` (`test(build): preserve
qmake sanitizer diagnostics`), and `9be72c5` (`fix(sanitize): reject unsafe diagnostic source
paths`). This workthrough documents local branch
evidence only. Remote PR/main/Pages acceptance, Quality Zoo cross-repository scenarios, and
candidate evidence remain pending. ici remains at `v0.10.2`; this slice does not create a release
or a version bump.

## Context

The earlier sanitizer adapter preserved only a bounded generic classifier in the public test
message. That protected reporters from unbounded sanitizer prose, but it also discarded the
source and frame information needed to explain a failure and allowed a reused CTest JUnit file to
look like evidence for a later run. The normalization boundary therefore has two distinct paths:

1. a private, bounded transcript transport from CTest/QtTest adapters to the sanitizer engine; and
2. a structured public finding contract containing validated project locations and redacted
   external-frame metadata.

The existing execution-state contract remains in force: a missing or incomplete sanitizer test
run is not a clean result, and an executed failure is distinct from a collected case that never
ran.

## Changes Made

### 1. Structured ASan/LSan/UBSan records

`src/ici/engines/_sanitizer_diagnostics.py` recognizes only bounded ASan, LSan, and UBSan report
signatures: `ERROR`, `SUMMARY`, and UBSan `runtime error` forms. ThreadSanitizer (TSan) is not
classified by this slice. Each `SanitizerDiagnostic` contains:

- a deterministic sanitizer `kind` (`asan`, `lsan`, or `ubsan`) and normalized `defect`;
- the `tool_name`, `ici.sanitize.<kind>.<defect>` detail rule identity, and bounded message;
- a project-owned primary `SourceLocation` when containment, regular-file status, size, UTF-8/NUL,
  line, and column bounds validate through the shared bounded no-follow descriptor reader and its
  stable double-read check;
- related stack-frame locations, with paths outside the project represented only by the
  `[external]` sentinel; and
- `frames_observed` and `project_frames` counts for the bounded stack that was inspected.

The parser enforces a 1 MiB UTF-8 diagnostic bound, at most 64 diagnostics and 32 observed stack
frames per transcript, and bounded source reads. Invalid UTF-8, NUL bytes, oversized transcripts,
diagnostic counts, or frame counts raise a normalization error instead of producing partial
evidence. A diagnostic without a validated project-owned location is retained as an explicit
location error by the engine; it is never promoted to a clean result.

### 2. Bounded private adapter transport and fresh JUnit evidence

`TestCaseResult` keeps the public `name`, `passed`, `message`, and `executed` contract. The
additional `diagnostic_output` and `diagnostic_output_truncated` fields are private transport for
the sanitizer engine. Adapter output is capped at 65,536 UTF-8 bytes, and truncation is explicit;
the generic test message remains bounded and does not carry the raw transcript.

For CTest versions that support JUnit output, the adapter removes the expected
`ici-ctest.xml` before each run. It reads only the fresh report through the bounded regular-file,
no-follow, containment, and before/after identity checks. Missing, changed, malformed, or
oversized JUnit input falls back to the bounded CTest stdout parser rather than reusing a stale
file or reading unbounded XML. A sanitizer marker in a nominally passing JUnit case is converted
to an executed failure before the sanitizer engine consumes its private transcript.

### 3. Sanitizer engine and process evidence

`SanitizeEngine` carries each normalized diagnostic into a required inspection target and, when
there is a validated owned primary location, a native finding payload. The native finding preserves
the compatibility rule `ici.legacy.sanitize.target`, plus `tool_name`, the detailed `tool_rule_id`,
and related locations. Frame counts and `process_evidence_index` live in the associated
`extra.sanitizer_diagnostics` detail, where the index points at the recorded CTest, qmake, or direct
sanitizer invocation. External stack paths are therefore not exported as host paths, while the
fact that an external frame was observed remains visible.

The generic C++ path compiles with ASan/UBSan instrumentation and frame/debug retention and runs
with leak detection enabled through the sanitizer environment. The real regression fixtures cover
an ASan heap-use-after-free, a UBSan signed-integer-overflow, and an LSan leak using `g++`.

Timeouts, process-output truncation, parser/normalization errors, oversized or malformed
diagnostics, and unlocated diagnostics fail closed as `ERROR`/`NOT_RUN` or an explicit location
error target. A complete located report accompanying signal termination remains a measured
`FAIL`; a partial report cannot be used to claim a clean run.

### 4. Documentation and roadmap boundary

The following documentation now describes the same private/public boundary and the fresh-JUnit
behavior:

- `README.md`
- `CHANGELOG.md`
- `docs/architecture.md`
- `docs/engine-reference.md`
- `docs/user-guide.md`
- `docs/superpowers/2026-08-30-handover.md`
- `docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`

Only the new I4-4 normalization checkbox is closed in this slice; the earlier execution-state
checkbox was already complete. The TSan profile, broader
resource/lifetime/security mappings, Quality Zoo UAF/leak/UB/Qt-lifetime scenarios, candidate and
cross-repository evidence, the wider I4-4 checkpoint, version changes, and release decision stay
pending.

## Code Examples

A normalized ASan record is conceptually exposed in the engine details as:

```json
{
  "kind": "asan",
  "tool_name": "AddressSanitizer",
  "defect": "heap-use-after-free",
  "rule_id": "ici.sanitize.asan.heap-use-after-free",
  "primary_location": {"path": "src/worker.cpp", "start_line": 17},
  "related_locations": [{"path": "[external]", "start_line": 95}],
  "frames_observed": 3,
  "project_frames": 2,
  "process_evidence_index": 0
}
```

The adapter keeps the corresponding transcript separate from the public message:

```python
TestCaseResult(
    "test_worker",
    False,
    "AddressSanitizer diagnostic",
    diagnostic_output=bounded_transcript,
    diagnostic_output_truncated=False,
)
```

The sanitizer engine parses that private field only after confirming the process was not timed out
or output-truncated and then requires a validated project-owned primary location before publishing
a measured finding.

## Verification Results

| Check | Result |
|---|---|
| Runtime | Python `3.10.21` |
| Focused sanitizer/adapter regression | `uv run --python 3.10 pytest tests/test_sanitizer_diagnostics.py tests/test_sanitize_engine.py tests/test_build_adapter.py -o addopts=''` — `132 passed` |
| Real sanitizer coverage | The focused suite includes real `g++` ASan heap-use-after-free, UBSan signed-integer-overflow, and LSan leak fixtures; all three pass. |
| Full local Python 3.10 suite | `2,088 passed, 7 skipped` (authoritative local result for the current implementation). |
| Documentation hygiene | `git diff --check` passes. |
| Release state | No version or release change; ici remains `v0.10.2`. |

The focused result is local evidence for this branch. It does not stand in for remote sanitizer
PR/main/Pages acceptance or Quality Zoo/candidate cross-repository evidence.

## Next Steps

- Obtain a sanitizer PR and exact-main run, artifact/Pages checks, and any required remote evidence.
- Add the TSan deep profile and broader resource/lifetime/security mappings in their own slices.
- Exercise the Quality Zoo UAF, leak, UB, and Qt-lifetime scenarios and candidate cross-repository
  contract before closing the wider I4-4 checkpoint.
- Revisit versioning and release only after the repository-wide gate and cross-repository evidence
  satisfy the plan; do not infer a release from this local implementation.
