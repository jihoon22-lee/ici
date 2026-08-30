# C++ Include Path-Suffix Resolution

## Overview

The `cycle` engine now keeps directory information from quoted C++ includes instead of reducing every include to a basename. This closes a false-negative where `core/format.hpp` and `gui/format.hpp` existed together, while preserving the rule that genuinely ambiguous includes are never guessed.

## Context

The previous graph builder indexed files only by basename. Consequently, `#include "core/format.hpp"` was discarded whenever any other `format.hpp` existed, even though the source had already named a unique path. It also provided no evidence for dropped edges, so a clean cycle result could hide an incomplete graph.

The intermediate resolver is intentionally not compiler-exact. Generated headers and actual `-I` ordering require the compilation-context milestone in I3.

## Changes Made

### `/home/jihoon/projects/ici/src/ici/engines/cycle.py`

- Added safe include component normalization and unique full-suffix matching.
- Connected an edge only when exactly one project file matches.
- Recorded unresolved and ambiguous occurrences with source path, line, snippet, candidate paths, and resolution mode.
- Kept aggregate cycle counts separate from include-resolution diagnostics.

The central rule is:

```python
matches = _matching_includes(inc_name, files)
return matches[0] if len(matches) == 1 else None
```

### `/home/jihoon/projects/ici/tests/test_cycle.py`

- Covered directory-qualified includes with colliding basenames.
- Covered bare ambiguous basenames without a guessed edge.
- Covered unresolved include locations and candidate metrics.
- Preserved the Python graph and deep iterative Tarjan contracts.

### Documentation

- Updated `CHANGELOG.md`, `README.md`, `docs/engine-reference.md`, and the I0 master-plan checklist.
- Documented `unique_project_path_suffix` as a heuristic pending compiler context.

## Verification Results

```text
uv run --python 3.10 pytest: 629 passed
tests/test_cycle.py: 17 passed
tests/test_cpp_e2e.py: 16 passed
ruff check/format: passed
build-pyz.sh and smoke.sh: passed
self verify: Pass 7, Warn 5, Fail 0, Error 0, TEM 4.78
GitHub PR #79: every check passed; ici/viewer HTML links returned HTTP success
```

## Next Steps

- Replace this heuristic with compilation-database and compiler include-search semantics in I3.
- Preserve these diagnostics as lower-confidence evidence when compiler context is unavailable.
