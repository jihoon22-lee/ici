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
Exception/dead/cycle/duplicate/packaging compatibility rule units remain unfinished. This partial evidence does not satisfy the Rust cutover gate; R8 must close every
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
