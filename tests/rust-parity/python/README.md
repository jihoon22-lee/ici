# Native Python rule parity evidence

R4 is in progress. Tests are native Rust except explicit development-only oracle
comparison scripts. No `.py` oracle or Python worker is part of the product.

## Implemented first unit

`ici-analysis` reads immutable `AnalysisRequest` inputs and emits one shared
`AnalysisOutput` for modern findings and legacy inspection targets. `AnalysisSession`
reuses bounded parsed AST/scope/metric data by source bytes, path, target, options and
parser identity. No filesystem or process fallback is used.

- Strict UTF-8/BOM, ASCII, Latin-1, CP1252 source decoding with original-byte mapping.
  Unknown codecs, malformed data, target grammar violations and semantic syntax
  errors produce incomplete evidence and an inspected error target.
- Domain spans use 1-based Unicode character columns, correcting the old Python
  byte-column display for non-ASCII text. Raw byte offsets stay internal.
- Lexical module/function/class/lambda/comprehension graph with imported aliases,
  parameters, shadowing, global/nonlocal, async scopes and native semantic checks.
- Line counts preserve explicit `options.legacy_projection`, resolved `source_paths`
  and `gated_paths`, nullable limits, all inspected targets and separate all/source
  aggregates. Modern checks gate selected source files.
- Cyclomatic and cognitive formulas use one cached scan. The 15 source fixtures in
  `metrics.json` were measured from public source `6fe3e405` on CPython 3.14.7, and
  preserve nested-scope pruning, signature expressions, elif/sole-else-if AST
  semantics, async-with versus old block-depth rules and decorator-independent
  function locations. Legacy inclusive versus modern strict cognitive thresholds
  remain distinct projections of the same measured numbers.

The crate tests additionally exercise decoder byte maps, Unicode, explicit legacy
line scope, semantic errors, target mismatch, delimiter budget, scope shadowing and
cache reuse. The fixed-source oracle can be checked without regenerating goldens:

```bash
/path/to/python3.14 tests/rust-parity/python/metrics_oracle.py /path/to/frozen-oracle
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/target \
  cargo test --locked -p ici-analysis
```

Security and resource rules now have a native implementation and 42 frozen-source
fixtures, with explicit corrected expectations separate from the original oracle.
Dead/duplicate/packaging compatibility rule units remain unfinished. This partial evidence does not satisfy the Rust cutover gate; R8 must close every
functionality row first.


## Security and resource unit

- Native security uses AST literal/call shapes and shared lexical alias resolution;
  secret values never enter target snippets/messages. `nosec` is read only from
  comment tokens. Entropy, private-key markers, exact-name allowlists, crypto
  intent (`usedforsecurity=False`), pickle, randomness and shell-call rules remain.
- Resource analysis retains bounded branch/loop/try/finally ownership states,
  aliases, context managers, explicit close/aclose, return/attribute/subscript
  transfer, ExitStack, mutable defaults and explanatory legacy metadata in
  `extra.resource_issues`. Acquisitions, defaults and CPython-projection AST counts
  match the frozen oracle where behavior is preserved.
- Corrected rows explicitly name false-positive closure/comprehension alias
  handling, defaults evaluated before parameters bind, break skipping loop else,
  and previously skipped except-star resource bodies. No golden is silently
  regenerated to hide these differences.
- Vendor/generated exclusions retain inspected SKIP targets, reason counts and
  limitations; explicit include overrides work. Unanalyzed files remain incomplete.
- Both domain and legacy end columns are 1-based Unicode **inclusive**, preserving
  ASCII legacy endpoints and correcting UTF-8 byte-column display. Parser ranges
  stay half-open bytes. Multiline/zero-width/Unicode endpoint fixtures cover the
  conversion.

`hygiene_oracle.py <frozen-oracle>` verifies the original evidence only. Native tests
compare the explicit corrected expectation when present, so old defects stay visible.


## Exception unit

46 fixed-source handler fixtures preserve alias binding order, conditional paths,
module/class/function lookup, aliases introduced after a deferred function definition,
local future bindings, transient handler names, matching captures, deletes, boolean
and conditional-expression assignments, BaseException/bare handlers, swallowing and
handler-local traceback resets. Handler query work is bounded; an exceeded bound is
incomplete evidence. `exception_oracle.py` verifies original expectations without
rewriting them. Current native tests also cover all prior units and ownership checks.

## Input budget and cancellation unit

R0's input bounds are 16 MiB per file and 512 MiB per snapshot. The strict decoder
accepts the exact per-file boundary and explicitly rejects a larger file. The
inherited 2,048-file source-inventory bound is applied only to the rule families
that already used that bounded inventory; it is not imposed on line/metrics.
Encoding cookies are scanned from raw first-two-line slices and single-byte codec
origin maps use checkpoints. Line iteration does not materialize every line.

`AnalysisSession::analyze_cancellable(request, &AtomicBool)` polls intake, per-file
rules, metric/handler loops, security AST walks and resource statements. Cancellation
keeps completed targets/findings and returns `TaskState::Cancelled`; partial resource
flow does not invent leak findings or a file PASS. A single parser call is not
interruptible, so full parser/rule stress and CLI descendant cleanup remain R8 work.

The development `cancellation_probe` runs a small file followed by a 16 MiB input,
sends cancellation after 20 ms, and checks that the first file's completed target is
retained while the last file is not inspected. The measured WSL debug run returned
CANCELLED in 580 ms with one retained target; this is recorded in
`cancellation-observation.json`. It does not establish the complete CLI/child-process
cancellation budget or the aggregate 512 MiB stress acceptance.

## Import graph unit

`cycle.json` records 16 graph cases from the frozen public implementation, with
corrected edges kept separately. Ordered declared source roots, package-relative
imports, package attributes, deferred/conditional imports, stdlib-name collisions,
self-cycles and the legacy unique-suffix fallback remain visible. Source-root
precedence no longer silently takes the last conflicting module file. Ambiguous
suffixes and relative imports leaving a declared package are incomplete instead
of invented edges. Unique-suffix cycles carry ESTIMATED evidence because the
runtime import search path was not proven. The displayed cycle follows real
graph edges rather than joining the SCC member order as a path.

Every successfully inspected file has a target, shadowed files have SKIP targets,
and graph failures preserve location-bearing error targets. An iterative SCC
fixture traverses 5,001 nodes without recursive graph walking. Twenty native tests
(including the earlier units) passed after this unit. `cycle_oracle.py` verifies
the recorded original graph and does not overwrite corrected expectations.

## Private symbols and unreachable statements

`dead.json` preserves 21 frozen-source cases and the complete original targets.
The native test compares every target field after sorting presentation order;
private-definition targets precede unreachable targets, whose native presentation
is source order rather than Python's breadth-first AST order. The shared lexical
scope graph prevents parameter/local/import shadowing from falsely marking a
module private function used. Declared package attributes take precedence over
same-named child modules, and Unicode columns count characters. Those changes
have explicit corrected fixture fields. Decorators, `__all__`, async/private-only
module definitions, source-root precedence, aliases, relative imports, repeated
function definitions, match bodies and the first unreachable statement per body
remain covered. Incomplete parsing produces ERROR with inspected healthy-file
SKIP targets and no guessed unused-symbol findings. All private-symbol findings
remain ESTIMATED. Twenty-one native tests and scoped Clippy/fmt passed; the
`dead_oracle.py` script verifies the original evidence without regenerating it.
