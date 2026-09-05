# Combined maintainability slices — GNU ELF linker evidence and Python AST shapes

## Overview

This workthrough records the two related maintainability slices combined on
`feat/maintainability-analysis-completion` for one planned PR:

- `6f2ba70` — `feat(dead): add exact GNU ELF discarded-function evidence`
- `5c0224e` — `feat(dup): add bounded Python AST-shape clone analysis`
- `e6b20a6` — `test(dead): align disabled-scope CLI contract`
- `36f30cc` — `fix(dead): enforce the Linux GNU ELF platform boundary`
- `161b844` — `refactor(analysis): satisfy self-verification quality gates`

The first slice adds target-local GNU ELF section-GC evidence to the `dead` engine. The second
adds a bounded Python AST-shape grouping path to the `dup` engine. Both implementations have local
Python 3.10 evidence. PR #151 exists, but this document does not claim successful PR CI,
exact-main, Pages, or cross-repository acceptance for the combined branch; those fields remain
pending until the corrected head is independently accepted.

The public version remains `0.10.2`. No version bump, tag, or release is part of this work.

## Context

The existing `dead` engine already retained Python AST reachability/name-reference findings as
`ESTIMATED`/`python-ast-heuristic`, and the existing `dup` engine provided language-aware lexical
token-region matching. These signals do not answer the larger questions of whole-program C/C++
deadness or behavioral equivalence. The combined slice therefore adds two deliberately bounded
signals while keeping their evidence and limitations visible.

## Changes Made

### 1. GNU ELF target-local discarded-function evidence

`src/ici/engines/_cpp_linker_dead_symbols.py` provides an independent C++ `dead` scope. Its
configuration is separate from compiler `-Wunused-function` evidence:

```toml
[engines.dead]
cpp_unused = "auto"       # existing compiler diagnostic scope
cpp_linker = "required"   # auto | required | off
```

The default `cpp_linker` policy is `off`. `auto` reports an unavailable exact native scope as
`SKIP`/`NOT_RUN`, `required` escalates unavailable context/tools to `ERROR`/`NOT_RUN`, and `off`
does not request the linker scope or its capability probes. Existing invalid context, coverage,
configuration, replay, and prevalidated command/tool-evidence failures still fail closed once the
scope is enabled.

The adapter makes the following narrow claim:

1. The project has a root CMake backend and an immutable compilation context.
2. An isolated Release shadow is configured with `Unix Makefiles`, `-ffunction-sections`,
   `-fno-lto`, `-fno-pie`, `-Wl,--gc-sections`, `-Wl,--print-gc-sections`, and `-no-pie`.
3. A discovered direct-object executable link command uses a capability-approved GCC driver.
4. The driver is independently observed to delegate to GNU `ld`; LLD and unsupported linkers are
   rejected.
5. GNU `ld` explicitly reports a discarded function section. `readelf` must map that section to
   one positive-size `FUNC` symbol with local binding or hidden/internal visibility, not a
   COMDAT/grouped or compiler clone symbol.
6. `addr2line` maps the section to one project-owned source path and a line inside the immutable
   source snapshot.

Only that uniquely mapped, source-located observation becomes an `EXACT`/`MEASURED` target with
`cpp-gnu-elf-section-gc` provenance. The result records link target, symbol, section, object,
tool identity, and link-command digest in `cpp_linker_details`. Every selected source is retained
as a located PASS target when it participated in the supported target-local link without an
accepted discarded function.

The link-command parser is shell-free. It accepts one bounded UTF-8 line, rejects shell operators
and response files, resolves direct objects/output inside the owned shadow, and rejects shared
objects, archives as direct evidence, LTO, export/dynamic-list/undefined roots, linker scripts,
`--whole-archive`, and other unsafe flags. Dynamic lookup, exported/default-visible symbols,
archives, shared objects, linker scripts, LTO, COMDAT groups, plugins, Qt meta-object reachability,
and whole-program reachability are outside the contract.

Safety limits are explicit and fail closed:

- 256 discovered link command files, with each file bounded to 4 MiB;
- 32,768 arguments and 1 MiB of total link-command argument characters;
- 4,096 direct objects per target and 16,384 discarded sections;
- 4 MiB per tool output;
- 180 seconds per link command and 900 seconds for the complete linker scope.

The adapter uses an owned analysis shadow, validates pre-recorded command/tool evidence, and
rechecks source bytes and path-containment boundaries before accepting the result. Any replay
failure, malformed diagnostic, timeout, truncation, unsafe path, incomplete object coverage, or
source mutation discards partial symbols and returns an error.

### 2. Bounded Python AST-shape duplicate analysis

`src/ici/engines/_python_dup_semantics.py` and the `DuplicateEngine` integration add a separate
bounded shape signal:

```toml
[engines.dup]
python_semantic = "auto"   # auto | required | off
```

The default is `auto`. The helper parses the Python 3.10 grammar (`feature_version=10`) and emits
source-linked regions for named functions, async functions, methods, and classes. The engine groups
only leaf functions and methods: a parent named scope prunes nested named-scope bodies from its own
shape, nested regions are emitted separately, and callable parents are excluded from grouping.
This prevents a parent callable from claiming lines that were not part of its canonical shape;
class regions remain helper evidence rather than reported clone groups.

Canonicalization removes source locations and the outer function/class name, alpha-normalizes local
bindings, and preserves the syntactic anchors that matter to this bounded comparison:
source-spelled imported names and attributes, operators, control flow, literals, defaults,
annotations, decorators, load/store/del contexts, and recursion references. Canonical JSON is
fingerprinted with the versioned
`sha256/semantic-shape-v1` algorithm. Lexical groups remain available; a semantic-shape group is
suppressed only when its complete occurrence set is identical to an existing lexical group.

The helper excludes `eval`/`exec` calls and literal `getattr` lookups of those names, `global`,
`nonlocal`, star imports, lambda and
comprehension scopes, malformed source, unsupported AST nodes, duplicate source paths, and any
resource-budget exhaustion. Exclusions retain a file/region and reason. The engine exposes shape
mode, exclusion counts, observed/eligible region counts, node counts, serialized-character counts,
and reported/suppressed group counts in `extra`.

The resource envelope is:

- 256 Python source files;
- 20,000 named regions;
- 500,000 AST nodes;
- 16 MiB of serialized canonical shapes.

The outcome and regions are immutable and deterministically ordered. `auto` can report a partial
bounded scope when safe regions coexist with conservatively excluded constructs; `required` fails
closed on any exclusion; `off` skips the shape pass while leaving lexical duplicate analysis
enabled. A collection budget or canonical-serialization failure discards partial semantic regions;
ordinary unsafe constructs can be excluded per region while safe regions remain available in
`auto` mode.

The duplicate aggregate remains the existing conservative `ESTIMATED` result. The shape fingerprint
means canonical AST-shape equality under this policy; it is not a proof of C++ semantic identity,
near-clone edit equivalence, runtime behavior, or behavioral equivalence.

### 3. Combined boundaries and release policy

The two scopes are intentionally independent:

```text
Python AST shape ──► bounded duplicate group ──► ESTIMATED duplicate result
GNU ld discard  ──► target-local C++ finding ──► language-scoped MEASURED evidence
```

The following remain open and are not marked complete by this work:

- whole-program deadness or general linker-backed reachability across objects/libraries;
- dynamic lookup, plugin entry points, exported symbols, archives, shared objects, LTO, linker
  scripts, COMDAT, or Qt meta-object paths;
- C++ AST/semantic duplicate analysis;
- behavioral equivalence or a general semantic-clone proof;
- the I4-3 aggregate, I4 checkpoint, or any release decision.

Generated/moc/vendor source ownership and the existing bounded strict-UTF-8 intake policies remain
in force. The `dead` cache remains disabled because external/generated include closure and compiler
or linker binary identity are not yet fully represented in its cache key.

### 4. Self-verification-driven module boundaries

The first PR run correctly rejected the branch because the combined implementation exposed one
overlong `dead.py` module, three new high-complexity functions, and nine mypy diagnostics. The fix
remains inside the same cohesive PR:

- existing Python dead-code traversal moved from `dead.py` to `_python_dead_code.py`, retaining the
  same immutable source snapshot and target contract;
- linker collection, inspection, and success projection became small orchestration helpers;
- Python shape field encoding became typed helpers without changing canonical payload semantics;
- support-matrix optional/required tool promotion became shared policy helpers;
- process-success narrowing uses Python 3.10's `TypeGuard`, and the semantic source mapping is
  passed through its iterable input contract.

This is a code-boundary and type-safety correction, not an expansion of either analysis claim.

## Code Examples

This linker-only `dead` result (`cpp_unused = "off"`) keeps native scopes distinguishable rather
than upgrading the aggregate:

```json
{
  "analysis_provenance": "cpp-gnu-elf-section-gc",
  "language_evidence": {"python": "NOT_APPLICABLE", "cpp": "MEASURED"},
  "cpp_linker_policy": "required",
  "cpp_linker_mode": "exact",
  "cpp_linker_details": [
    {
      "file_path": "src/reachability.cpp",
      "start_line": 8,
      "link_target": "<fixture-target>",
      "symbol": "<local-function>",
      "section": ".text.<function-section>"
    }
  ]
}
```

The duplicate shape metadata is versioned and explicit:

```json
{
  "python_semantic_policy": "auto",
  "python_semantic_mode": "bounded",
  "python_semantic_shape_policy": "python-bounded-ast-shape-v1",
  "python_semantic_fingerprint_algorithm": "sha256/semantic-shape-v1",
  "python_semantic_groups_reported": 1
}
```

## Fixtures and Tests

The real-tool fixture is `examples/cpp-fixtures/cmake_elf_dead`:

- `src/main.cpp` calls `live_entry`;
- `src/reachability.cpp` contains the live `live_leaf`, live `live_entry`, internal
  `dead_leaf`, and hidden `dead_entry`;
- `ici.toml` sets both `cpp_unused = "required"` and `cpp_linker = "required"`.

`tests/test_cpp_linker_dead_e2e.py::test_cmake_elf_fixture_reports_only_the_two_discarded_dead_functions`
uses the real GNU toolchain when available and observes only `src/reachability.cpp:8` and
`src/reachability.cpp:17`; it does not report the live functions or `src/main.cpp` as discarded.
`tests/test_cpp_linker_dead_symbols.py` covers bounded command parsing, unsafe-link rejection,
GNU `ld` identity, symbol/section filtering, source mapping, unavailable/off policy, and atomic
relink failure.

`tests/test_dup_python_semantics.py` covers alpha-renaming, API/literal/control-flow anchors,
named-scope pruning, unsafe exclusions, malformed AST, deterministic immutable outcomes, and each
resource limit. `tests/test_dup_semantic_integration.py` covers line-layout-insensitive grouping,
negative API/literal cases, partial/required/off policies, and parent-region exclusion.

Observed local verification on this worktree:

```text
uv run --python 3.10 pytest -o addopts='' \
  tests/test_cpp_linker_dead_symbols.py tests/test_cpp_linker_dead_e2e.py \
  tests/test_dup_python_semantics.py tests/test_dup_semantic_integration.py \
  tests/test_dead_engine.py tests/test_config.py tests/test_support_matrix.py \
  tests/test_build_variants.py tests/test_toolchain_capabilities.py
362 passed in 2.69s

uv run --python 3.10 pytest -o addopts=''
2295 passed, 7 skipped in 77.77s

uvx ruff check .
All checks passed!

uvx ruff format --check .
205 files already formatted

uv run --python 3.10 mypy src
Success: no issues found in 111 source files

dist/ici.pyz verify --report --html <temporary-report>
Suite WARN; 13 engines; 8 PASS, 4 WARN, 0 FAIL, 0 ERROR, 1 SKIP;
2295/2302 tests passed; TEM 4.82/5.0; line/function/branch 89.3%/96.8%/82.1%

./scripts/build-pyz.sh
ici 0.10.2 pure-Python reproducible zipapp built

./scripts/smoke.sh
all launcher, Python 3.10, artifact-integrity, report, and Zero-CDN checks passed
```

The seven full-suite skips are existing environment/tool-dependent skips; no skip is treated as a
pass by this workthrough. The self-verification WARN state contains the accepted repository policy
warnings for line, cycle, complexity, and duplication; it contains no FAIL or ERROR. No PR-CI
success is inferred from these local commands.

## Separate TSan candidate acceptance evidence

The following evidence was already completed for the separate TSan Quality Zoo candidate path and
is recorded here for continuity. It is not evidence for the combined maintainability branch:

| Item | Exact value |
|---|---|
| ici candidate source SHA | `6ee08b14fa598a19074af7afed4368fd79b19b2b` |
| candidate artifact | `9884927798` |
| candidate raw ZIP SHA-256 | `9a50972a5cb4ad96b2b0cf912e27c17a600fc19d6d899c6e33028d4449b1122d` |
| toy-projects source SHA | `d0b84d376d3f736da86308a49d21d8600297eb27` |
| Quality Zoo workflow | [`33737405098`](https://github.com/jihoon22-lee/ici/actions/runs/33737405098), `success` |
| scenario contracts | `8/8 PASS` |
| runner errors | `0` |
| acceptance artifact | `9886336618` |
| acceptance ZIP SHA-256 | `70f298a33a251241033882a5bd1eea1a7f863dd86c1939321d531cee39b32bf3` |

This closes that exact candidate contract and complements the already-accepted TSan PR #146 and
exact-main run `33718399268`. It does not close the broader I4-4 checkpoint, the combined branch
PR/CI validation, or any release boundary.

## Verification and status boundary

The combined branch is PR #151. Its first workflow run `33744139992` passed Qt5, Qt6, tests, build,
reproducibility, and smoke checks but failed the self-verification job on the line, complexity, and
mypy findings listed above; the report publisher consequently had no viewer report to publish.
That run is failure evidence, not acceptance. A corrected PR workflow, exact-main run, Pages copy,
and cross-repository acceptance identifier remain pending. The public version remains `0.10.2`;
no version bump, tag, or release was made.

## Next Steps

- Push the corrected combined PR head, require every check to pass, and audit its single sticky HTML
  report comment before merge.
- Record exact-main CI and Pages evidence only after the corrected PR merges.
- Keep broader whole-program deadness, C++ AST/semantic duplicate analysis, and behavioral
  equivalence as explicit follow-up scopes.
- Do not reuse the separate TSan candidate acceptance as acceptance for this combined branch.
