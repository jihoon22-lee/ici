# Sanitizer clean-result message compatibility

## Overview

Restored the published generic C++ sanitizer clean-result wording after the
ThreadSanitizer refactor changed it unintentionally. The compatibility fix keeps
the existing Quality Zoo expectation stable while giving the new TSan engine its
own explicit clean message.

## Context and root cause

The candidate Quality Zoo run exercised a clean generic C++ sanitizer target and
exposed a contract mismatch: the target still passed, but its projected finding
message no longer matched the released expectation. A shared sanitizer class
refactor replaced the generic literal with a label-derived message:

```python
# Accidental refactor result
message = f"{self.CPP_LABEL} completed without diagnostics"
```

That expression is correct for a newly introduced sanitizer subclass only if the
message is allowed to change. For the published generic `SanitizeEngine`, however,
`CPP_LABEL == "ASan/UBSan"`, so the refactor changed the established wording from
the full sanitizer names to an abbreviated label.

## Compatibility boundary

`SanitizeEngine` now owns the historical generic message as an explicit class
constant, and the generic clean target uses it unchanged:

```python
CPP_CLEAN_MESSAGE = "AddressSanitizer and UndefinedBehaviorSanitizer completed"
```

`ThreadSanitizeEngine` overrides only that constant with its TSan-specific wording:

```python
CPP_CLEAN_MESSAGE = "ThreadSanitizer completed without diagnostics"
```

This preserves the released generic sanitizer report contract without weakening
the TSan result contract. The regression test asserts both boundaries: generic
clean output retains the published text, and the TSan path keeps its own text.

## Changes recorded by the fix

- `src/ici/engines/sanitize.py`: introduced the generic compatibility constant and
  routed clean C++ targets through it.
- `src/ici/engines/thread_sanitize.py`: supplied the TSan-specific override.
- `tests/test_sanitize_engine.py`: added a regression test for the published
  generic clean message.
- `tests/test_thread_sanitize_engine.py`: asserted the TSan clean message remains
  distinct.
- `CHANGELOG.md`: recorded the compatibility boundary under `Fixed`.

## Verification results

Focused sanitizer regression suite:

```text
uv run --python 3.10 pytest tests/test_sanitize_engine.py tests/test_thread_sanitize_engine.py
52 passed in 1.56s
```

Changed-file formatting check:

```text
uvx ruff format --check \
  src/ici/engines/sanitize.py \
  src/ici/engines/thread_sanitize.py \
  tests/test_sanitize_engine.py \
  tests/test_thread_sanitize_engine.py
4 files already formatted
```

No version was changed and no stable release was created. The fix remains a
compatibility correction on the existing `v0.10.2` line.
