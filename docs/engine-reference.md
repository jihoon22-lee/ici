# Check reference

> The v0.11 engine descriptors this page documented were removed with the
> stable shell. Checks in ici-next are declared per language in
> `src/ici/languages/<lang>/checks.py`; each check selects a provider and the
> gate judges *required* checks only (`[checks."<id>"] required = false`
> marks one advisory — it still runs, measures, and reports).

Profiles: `fast` < `standard` < `deep` select which checks a run picks up.

## Python checks (`src/ici/languages/python/checks.py`)

| Check | Measures |
|---|---|
| `python.line` | Line counts (`lines_total`, `lines_code`, …) |
| `python.lint` | `ruff check` findings |
| `python.format` | `ruff format --check` violations |
| `python.type` | `mypy` diagnostics under the declared interpreter |
| `python.test` | `pytest -vv` per-node verdicts → `pytest.cases` |
| `python.coverage` | coverage.py data → `coverage.lines/branches/functions` |
| `python.complexity` | Cyclomatic complexity per function |
| `python.cognitive` | Cognitive complexity per function |
| `python.cycle` | Import cycles (Tarjan on the module graph) |
| `python.dup` | Type-2 clone groups → `duplicated_lines` |
| `python.security` | Bandit-style source patterns |
| `python.resource` | Resource-leak hygiene |
| `python.exception` | Exception-safety patterns |
| `python.dead` | Unreferenced private functions (import-graph aware) |
| `python.compat` | Static API usage vs `requires-python` floor |
| `python.compat-runtime` | The declared interpreter measured + `compileall` |

## C++ checks (`src/ici/languages/cpp/checks.py`)

| Check | Measures |
|---|---|
| `cpp.line` | Line counts |
| `cpp.compile` | Translation-unit coverage of the compile DB |
| `cpp.diagnostics` | Compiler diagnostics replay |
| `cpp.tidy` | `clang-tidy` (blocked when the tool is absent) |
| `cpp.test` | `ctest` suites and QTest binaries from declared builds → `ctest.cases` / `qtest.cases` |
| `cpp.complexity` / `cpp.cognitive` | Function metrics (bounded-token estimate where heuristic) |
| `cpp.cycle` | `#include` cycles |
| `cpp.dup` | Type-2 clones |
| `cpp.exception` | Exception-safety patterns |
| `cpp.coverage` | `gcov` data → `coverage.cpp.*` |
| `cpp.artifact` | Declared `[builds.*] artifacts` exist and satisfy the manifest |
| `cpp.sanitize` / `cpp.tsan` | ASan/UBSan/LSan and TSan instrumented runs |
| `cpp.binary-compat` | ELF/readelf ABI facts. Deployment floors (ELF class/machine, max glibc) are **not declarable yet** — the check reports that limitation instead of judging silently |

## Integration checks

| Check | Measures |
|---|---|
| `integration` | Declared `[[components.integrations]]` cases — `{python:NAME}` resolves declared interpreters or named `python_targets`, `{artifact:BUILD/PATH}` resolves against linked builds' declared artifacts. Offered only when a component declares cases (DEEP profile) |

## Finding → gate rule

A finding fails the run only when it is measured, unsuppressed, and produced
by a check the policy requires. Advisory findings stay in the result — see
[`design/ici-next/spec-04-results-integration.md`](design/ici-next/spec-04-results-integration.md)
for the axis model (`PASS` / `FAIL` / `INCOMPLETE` on the selected gate;
execution, evidence, scope, findings, and publication are separate axes).
