# Deep ThreadSanitizer profile — 2026-09-03

## Overview

This workthrough records the local `feat/thread-sanitizer-deep-profile` slice for a separated
ThreadSanitizer (TSan) execution profile. It adds a C++/Qt-capable, deep-only engine and a direct
command while keeping the existing ASan/LSan/UBSan `sanitize` path unchanged and isolated. The
implementation has local regression evidence, including a real `g++` race fixture; feature
PR/main and Quality Zoo TSan acceptance remain pending. ici stays at `v0.10.2`, and this slice does
not create a release.

## Context

TSan needs a different runtime and object instrumentation from memory/undefined-behavior
sanitizers. Reusing the ASan shadow or combining the flags would make a result ambiguous and could
leave stale instrumented objects in a later run. The profile therefore has its own build variant,
shadow suffix, engine registration, environment policy, and diagnostic normalization boundary.

## Changes Made

### 1. Isolated build variant and engine selection

`BuildVariant.THREAD_SANITIZE` has the canonical value `thread-sanitize` and uses a `-tsan` shadow.
The deep-only `thread_sanitize` descriptor owns that variant and produces its own build and
diagnostic artifacts. The CLI registers the same engine for the direct command:

```bash
ici verify --profile deep
ici thread-sanitize
```

Python is explicitly unsupported for this engine; it does not run the `sanitize` engine's Python
ResourceWarning check. CMake and qmake projects use their native adapter while descriptor-free
projects use the generic g++ path. The adapter flags are exactly `-fsanitize=thread`,
`-fno-omit-frame-pointer`, and `-g` for C++ compilation, with `-fsanitize=thread` at link time;
the generic path additionally links `-pthread`.

### 2. Runtime option and instrumentation isolation

The TSan run copies the process environment, preserves any existing `TSAN_OPTIONS` entries, and
adds `halt_on_error=1`. The TSan path does not add ASan or UBSan options and does not share the
ASan/UBSan shadow or objects. This keeps a diagnostic tied to the instrumentation that actually
ran the test.

### 3. Bounded diagnostic normalization

Only complete `WARNING: ThreadSanitizer:` and `SUMMARY: ThreadSanitizer:` signatures are accepted
as TSan report starters. The shared normalizer bounds transcript, diagnostic, stack-frame, and
source-file intake; it validates project-owned locations and represents external stack frames as
`[external]` rather than leaking host paths.

Known defect prefixes such as data races, lock-order inversions, thread leaks, and mutex failures
map to deterministic defect and rule IDs. Unknown TSan wording never becomes a public rule ID;
it falls back to `ici.sanitize.tsan.thread-safety-defect`. A complete report with a validated
project location remains a measured failure, while malformed, oversized, incomplete, or unlocated
evidence fails closed instead of becoming a clean result.

### 4. Documentation and status boundary

The user guide, engine reference, architecture guide, README, changelog, master plan, and handover
now describe the same variant, engine, parser, and support-matrix boundary. The plan deliberately
keeps the I4-4 TSan checkbox open. Classification and Qt candidate evidence are separate ongoing
work; no statement here represents remote PR/main, Pages, Quality Zoo, version, or release evidence.

## Code Examples

The normalized shape of a known TSan race is conceptually:

```json
{
  "kind": "tsan",
  "tool_name": "ThreadSanitizer",
  "defect": "data-race",
  "rule_id": "ici.sanitize.tsan.data-race",
  "primary_location": {"path": "src/race.cpp", "start_line": 17},
  "related_locations": [{"path": "[external]", "start_line": 44}]
}
```

An existing runtime option is retained and extended rather than replaced:

```text
TSAN_OPTIONS=history_size=4
→ history_size=4:halt_on_error=1
```

## Verification Results

| Check | Result |
|---|---|
| Engine contract | `thread_sanitize` is deep-only, C++/Qt tool-backed, and Python unsupported. |
| Build isolation | `THREAD_SANITIZE` uses `-tsan` and TSan-only flags; ASan/UBSan flags are absent from the variant. |
| Runtime environment | Existing `TSAN_OPTIONS` is preserved and `halt_on_error=1` is added. |
| Parser contract | Exact TSan `WARNING`/`SUMMARY` signatures, bounded locations, external redaction, known IDs, and stable unknown fallback are covered by regression tests. |
| Real runtime | The real `g++` data-race regression passes locally. |
| Repository quality | Parent session reports the full local test gate passing; this worktree change is documentation-only. |
| Release state | No version or release change; ici remains `v0.10.2`. |

## Next Steps

- Run the feature branch through the normal PR and exact-main CI/Pages gates.
- Produce and independently verify a candidate artifact before any Quality Zoo TSan dispatch.
- Add the separate classification and Qt candidate expectations, then audit their exact rule and
  location contracts.
- Revisit the I4-4 checkpoint and release decision only after the broader safety mappings and
  cross-repository evidence are complete.
