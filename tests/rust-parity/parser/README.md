# Python parser evaluation corpus

This is development-only evidence for the Rust replacement, not a product worker or
an analysis parity claim. Invalid/new syntax is JSON data so existing Python gates
never collect it as a Python module. `oracle.py` uses CPython only to record a
comparison; it is not included in the Rust product.

`cases.json` has handwritten grammar/decode expectations. `check_results.py` also
asserts specific Unicode offsets, newline mappings, normalized identifiers, aliases,
async/scope AST forms, retained trivia, recovery nodes and rejected old target syntax.
`observations.json` records the actual pinned native execution and five independent
CPython versions. `grammar_complete` means no parser/version errors in this probe;
it does **not** mean binding/semantic checks or ici analysis are complete.

The source-size limit is an explicit probe policy. The decoder demonstrates strict
UTF-8/BOM, ASCII and Latin-1; CP1252 is explicitly unsupported here. The decoder is
not a full PEP 263 codec implementation. Original byte-to-decoded byte mapping beyond
the BOM prefix still belongs to the production adapter. AST scope shapes are tested;
name resolution, alias shadowing and global/nonlocal resolution still require a
shared semantic implementation and rule-specific fixtures.

## Reproduce

Use a task-owned directory outside this repository. The following layout is required
by the captured manifest: `<task>/ruff` and `<task>/probe`. Clone upstream tag `0.16.4`
and verify its commit is `11c76bf48fdac06b2f240cba502eda96da4dce77`; a moving branch is
not an acceptable substitute. Copy `probe.Cargo.toml`, `probe.Cargo.lock`, `probe.rs`
to `<task>/probe/Cargo.toml`, `<task>/probe/Cargo.lock`, `<task>/probe/src/main.rs`.

```bash
flock /tmp/paseo-products-20261028-heavy.lock env \
  CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=<task>/target-static \
  RUSTFLAGS='-C target-feature=+crt-static' \
  cargo build --locked --release --target x86_64-unknown-linux-gnu \
  --manifest-path <task>/probe/Cargo.toml

env -i PATH=/nonexistent \
  <task>/target-static/x86_64-unknown-linux-gnu/release/ici-parser-spike \
  < tests/rust-parity/parser/cases.json > <task>/results.json
python3 tests/rust-parity/parser/check_results.py \
  tests/rust-parity/parser/cases.json <task>/results.json

/path/to/pinned-python tests/rust-parity/parser/oracle.py \
  tests/rust-parity/parser/cases.json > <task>/oracle.json
```

The manifest includes the AST serde feature from the API experiment. It does not make
`Mod` serializable; the probe uses explicit visitors and emits its own observation
JSON. Production should expose an owned AST/source-map adapter, not Ruff internals as
wire JSON. The captured lock's path dependencies are pinned by the verified Ruff
checkout; a production Cargo git dependency must use the same full revision.

Only the tested WSL2 host is evidenced. Static parser linkage does not establish the
full product's target/libc/CPU floor, TLS portability, musl support, or cancellation
behavior. See [the evaluation](../../../docs/design/rust-replacement/parser-evaluation.md).
