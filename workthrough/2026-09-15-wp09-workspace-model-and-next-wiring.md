# WP09 — `ici next` runs on the workspace model

## Overview

Finished WP09 ([#207](https://github.com/jihoon22-lee/ici/issues/207)) end to end:
merged the two open stacked PRs, then implemented and merged PR C, which wires
`ici next` onto the domain workspace model. The new path now answers "what will
be checked" from declarations and "what was checked" from a real content
inventory — no ad-hoc `rglob`, no hash-of-pathnames snapshot.

## Context

- Milestone #1 `ici-next`: modular, offline-first analysis engine. WP09 is the
  workspace/source-scope layer everything above (WP10 DAG, WP11 cache) stands on.
- On resume: PR A (#256) mergeable but blocked by 4 unresolved Devin Review
  threads + a ruleset requiring resolution; PR B (#257) orphaned when its base
  branch was deleted (auto-recreated as #258); PR C unwritten.

## Changes Made

### 1. PR merges (GitHub, no code)

- **#256** — verified the 4 review findings were already fixed by the follow-up
  commit (declared interpreter paths resolve `${env:}` + anchoring; child config
  paths resolve env before loading; external paths normalised before the
  containment check; builds without `prepare` allowed). Replied to and resolved
  all four threads, then squash-merged.
- **#258** (the re-created PR B) — waited for `Verify & Dogfood` (Qt5/Qt6 were
  already green), confirmed Merge Gate, squash-merged.

### 2. WP09 PR C implementation (`feat/workspace-next-wiring` → #259)

| File | Change |
|---|---|
| `src/ici/workspace/model.py` | `_DEFAULT_SOURCE_GLOBS` + `_sources()`: a component that declares no `sources` claims its languages' conventional globs anchored to its root (`**/*.py`; `**/*.cpp|.cc|.cxx|.hpp|.h`). `ici init` writes no `sources` key — an empty declaration must not mean an empty scope. |
| `src/ici/domain/workspace.py` | `Workspace.scoped(component_ids)`: subset view keeping selected components + their units, all build units, required ids filtered to the selection; unknown ids rejected. |
| `src/ici/workspace/legacy.py` *(new)* | `project_context()` — Workspace → legacy `ProjectModel` projection. Type from component-language union; source lists from the inventory classified by the old model's suffixes; fields the model can't answer (`cpp_include_flags`, headers, compilable sets) stay empty; several build units → `backend=None` with the reason rather than a collapsed label. |
| `src/ici/application/plan.py` | `PlannedCheck.task_id` — identity travels on blocked placeholders too; a task that disagrees with its task_id raises. |
| `src/ici/application/selection.py` | `Planner` signature `(check, executable, task_id)`; `task_prefix` param so `_plan_for` decides ids in one place. |
| `src/ici/application/verify.py` | `verify()` accepts `Plan \| Iterable[Plan]` — several per-component plans judged as one gate; observations/incompleteness keyed by `task_id`. |
| `src/ici/application/report.py` | `assemble()` takes `required_components` (defaults to selected), `omitted_components`, run-level `limitations`. |
| `src/ici/cli/next_path.py` | Rewired: `load` → `build_workspace` → `inventory.take` → `vcs.status` → per-component `_plans` → `run_verification` → post-run `inventory.diff` → `assemble`. `_planner()` factory binds per-component context (no closure late-binding). FULL only when the run covered the workspace *and* completed; an incomplete whole-workspace run is `PARTIAL`. |

### 3. Tests

- `tests/test_cli_next_workspace.py` — multi-component run, qualified task ids
  in findings, `--component` → PARTIAL + omitted, unknown component and `needs`
  cycle → exit 2, real content-digest snapshot, VCS commit/dirty from a real
  `git init`, cpp-component-no-checks → limitation not crash.
- `tests/test_workspace_legacy.py` — projection: python/hybrid type, subset
  scope, unknown component rejected, single qmake build → backend, several
  builds → honest none, explicit build → none, no inventory → empty lists,
  generated files excluded.
- `tests/test_application_selection.py` — planner double updated for the
  `task_id` argument.

### 4. CHANGELOG

WP09 entry covering all three PRs (A/B had merged without one).

## Notable decisions

- **FULL is narrow on purpose.** `ScopeSelection` refuses `FULL` unless the
  required scope was covered and complete — so a failed-to-finish whole-
  workspace run stores `PARTIAL`, and a partial PASS can never read as a
  workspace PASS (R05) as a stored fact, not a consumer's recompute.
- **Suffix classification lives only in the legacy projection.** The new model
  deliberately doesn't attribute files per language (units share component
  scope); the projection keeps the old model's extension buckets because that
  is exactly the question a legacy reader asks.
- **Blocked ≠ unselected.** A check whose scope inventories nothing stays in
  the plan as blocked — the difference between "nobody asked" and "could not
  run" (SPEC-04).

## Verification

```text
uv run --python 3.10 pytest     # full suite green (~4600 tests)
uvx ruff check .                # all checks passed
uvx ruff format --check .       # 374 files formatted
./scripts/build-pyz.sh          # dist/ici.pyz 2.7M, 11 wheels all py3-none-any
./scripts/smoke.sh              # all smoke tests passed
```

PR #259 CI: `Verify & Dogfood ici` ✓, Qt5/Qt6 viewer ✓, Merge Gate ✓,
zero unresolved review threads → squash-merged at 2026-09-15T08:12:43Z.

## Issue housekeeping

- Closed #203 (WP05), #204 (WP06), #205 (WP07) — checklists complete, all PRs
  merged earlier.
- Closed #207 (WP09) — all five acceptance criteria met across #256/#258/#259;
  checklist checked and closing comment maps criteria → PRs.

## Next

WP10 ([#208](https://github.com/jihoon22-lee/ici/issues/208)) — Check/Provider
registry, prerequisite DAG, shared execution. The per-component plan list this
PR produces is the seam the DAG scheduler will consume.
