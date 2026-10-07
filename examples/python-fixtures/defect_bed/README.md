# defect_bed

The seeded-defect half of the Python corpus — one planted defect per internal
check, exaggerated past either path's thresholds, under `src/` so the stable
engines' default source discovery sees it. `tests/test_next_differential.py`
asserts that each defect the stable engine names also surfaces under its
next-path check:

- `src/insecure.py` — `eval`, `pickle.loads`, `shell=True` → python.security
- `src/swallow.py` — a swallowed bare `except` → python.exception
- `src/hot.py` — exaggerated cyclomatic/cognitive complexity → python.complexity
- `src/dead.py` — an unreferenced private function → python.dead
- `src/leaky.py` — an `open()` that is never closed → python.resource
- `src/oversized.py` — 1000+ code lines → python.line
- `src/pkg/alpha.py`, `src/pkg/beta.py` — a mutual import cycle → python.cycle
- `src/clone_a.py`, `src/clone_b.py` — an identical-body clone pair → python.dup

Clone pairs are compared at occurrence precision rather than file status —
the stable engine reports clone groups as informational targets, so the
file-status matrix cannot see them. The cycle finding's primary location
differs between paths (stable anchors on alpha, next on beta); the test
compares the union of primary and related locations. Like quality-zoo, the
seeded defects are excluded from lint/format in `pyproject.toml`.
