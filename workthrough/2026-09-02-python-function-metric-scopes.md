# Python per-function complexity scope boundaries

## Overview

Python cyclomatic and cognitive complexity now measure each named function in its own executable
scope. Nested function, class, and lambda bodies no longer inflate the enclosing function, while
nested named functions and methods remain independently discoverable with their own file and line
targets.

This is an analyzer-correctness fix under `Unreleased`; it does not change ici's public `0.10.2`
version or create a release.

## Context

Both engines discovered every `FunctionDef` and `AsyncFunctionDef` independently, but their metric
walkers also recursively entered the same nested bodies from the outer function. This double-counted
branches and let an enclosing loop's cognitive `in_loop` state leak into a nested function.

Focused red tests demonstrated the overcount:

- nested function/class/lambda fixture: outer CC `9` instead of `4`, cognitive `9` instead of `1`;
- nested async loop fixture: outer CC/nesting `5/3` instead of `2/1`, cognitive/nesting `11/3`
  instead of `1/1`;
- nested default-expression fixture: outer CC `4` instead of `3`, cognitive/nesting `5/2`
  instead of `3/1`.

## Changes Made

Changed files:

- `/home/jihoon/projects/ici/src/ici/engines/_python_metrics.py`
- `/home/jihoon/projects/ici/src/ici/engines/complexity.py`
- `/home/jihoon/projects/ici/src/ici/engines/cognitive.py`
- `/home/jihoon/projects/ici/tests/test_complexity.py`
- `/home/jihoon/projects/ici/tests/test_cognitive.py`
- `/home/jihoon/projects/ici/CHANGELOG.md`
- `/home/jihoon/projects/ici/docs/engine-reference.md`
- `/home/jihoon/projects/ici/docs/superpowers/plans/2026-08-30-python-cpp-qt-quality-analyzer-master-plan.md`
- `/home/jihoon/projects/ici/workthrough/2026-09-02-python-function-metric-scopes.md`

### Shared AST boundary

`src/ici/engines/_python_metrics.py` defines one deterministic child iterator and scope walker used
by both engines. When traversal reaches a non-root function, async function, class, or lambda, its
`body` field is pruned.

Definition-time expressions intentionally remain visible to the enclosing metric:

- function decorators, defaults, annotations, and return annotations;
- class decorators, bases, and keywords;
- lambda defaults;
- comprehensions, including their guards.

This preserves existing policy for expressions evaluated when a nested scope is created while
preventing executable body ownership from being counted twice.

### Complexity and cognitive integration

- `ComplexityEngine._calc_ast_cc()` uses the bounded scope walker.
- `ComplexityEngine._calc_ast_nesting()` recurses through the same bounded children.
- `_cognitive_for_function()` shares the boundary, so nested `break`/`continue` cannot inherit an
  enclosing loop state.
- Module-level discovery still walks the complete file AST, so named nested functions and methods
  continue to produce independent targets.

### Regression matrix

The tests cover nested sync/async functions, class bodies and methods, lambdas, decorator/default/
base expressions, comprehensions, boolean operators, nested loop state, independent target lines,
and unchanged definition-expression policy.

## Key Code

```python
for field_name, value in ast.iter_fields(node):
    if node is not root and isinstance(node, _NESTED_SCOPE_TYPES) and field_name == "body":
        continue
```

## Verification Results

```text
focused complexity/cognitive/project-layout tests  41 passed
Python 3.10 full suite                           1571 passed, 4 skipped
Ruff check                                      passed
Ruff format                                     160 files already formatted
standalone pyz build                            passed
smoke / Python 3.10 direct execution            passed
artifact integrity / Zero-CDN HTML              passed
```

The four skips are the established local missing-tool cases; CI installs the required Qt/C++
analysis tools and must exercise those paths before merge.

## Next Steps

Keep the remaining maintainability work separate: compiler-backed C++ function boundaries,
confidence correction for heuristic evidence, compiler-backed unused symbols, generated/vendor
clone exclusion, and stable clone identities.
