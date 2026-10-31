# Config/discovery Rust acceptance fixtures

This is a config milestone, not whole-product parity. Frozen legacy/v1 inputs
come from v0.13.0 source `6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`.
The runtime contains native Rust/TOML parsing and declarative JSON defaults;
it does not import or execute Python, a shell, a compiler, or a package manager.

`crates/ici-config/tests/resolution.rs` builds fresh project trees and checks:

- No-config, sparse v2 and generated init use the same resolver and include root sources.
- VCS/subdirectory boundaries, disjoint package ownership, and independent nested workspaces.
- ici.toml over pyproject `[tool.ici]`, isolated explicit config, scalar/map/list rules,
  full exclude replacement versus extend-exclude, and unchanged default excludes on a partial config.
- Legacy/v1 settings and explicit scope, child-file path anchors, standalone scope,
  runtime environment references, local path overrides, required-check exemptions,
  native mypy/ty selection and conflict diagnosis.
- 322 frozen concrete key declarations (including tool/output/suppression path keys),
  unknown/future keys, source line diagnostics, bounded direct argv, path containment,
  and source symlink refusal with a visible limitation.
- Comment-preserving migration preview, a new output only, refusal to overwrite,
  and detection of source edits between preview and write.

The v1-init-no-excludes fixture records an intentional correctness correction:
old init omitted dependency/generated exclusions even though no-config supplied
them. Rust retains defaults when excludes are omitted. An explicitly supplied
exclude list still replaces defaults and is treated as user intent.

Actual verification on Linux/WSL, rustc/cargo 1.99.1: 28 focused regression tests passed;
Clippy with `-D warnings` and the scoped rustfmt check passed.

```sh
flock /tmp/paseo-products-20261028-heavy.lock \
  env CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/config-target \
  cargo test --locked --offline -p ici-config
flock /tmp/paseo-products-20261028-heavy.lock \
  env CARGO_BUILD_JOBS=2 CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/config-target \
  cargo clippy --locked --offline -p ici-config --all-targets -- -D warnings
rustfmt --edition 2024 --check crates/ici-config/src/*.rs crates/ici-config/tests/*.rs
```

The first offline check could not fetch the previously uncached locked
`toml_datetime 1.1.2`; a normal locked development build fetched it. Subsequent
checks run offline. This is a development dependency fetch, never a product
resolution/analysis download.

Remaining integration boundaries: actual provider/runtime execution and native
build variant selection belong to R3/R5/R6; new binaries/installer/cross-platform
acceptance belong to R8/R9. CLI `init --force` compatibility needs an explicit
backup/replacement workflow and is not implemented by the strict write_scaffold
API. Existing comments/config/result files are never automatically rewritten.
The inventory status must not be bulk-closed merely because syntax accepts all
322 keys; provider effects and behavior fixtures remain independent evidence.

Legacy migration follow-up: the preview now emits an explicit project component
that preserves source_dirs, language filtering and project interpreter.
`legacy-migration-scope.json` records an actual v0.13.0 reader observation from
CPython 3.10.21 after asserting the current Python product tree has no diff from
the frozen source. Rust legacy resolution and migrated resolution both match
that file set, including a nested package. Global v2 source_dirs apply relative
to the workspace, so package partitioning cannot accidentally narrow them.

Native type-provider intent is now a single shared exported selector bounded
by the workspace. It includes .mypy.ini and ancestor pyproject metadata;
conflicting mypy/ty declarations require an explicit choice. setup.cfg section
names are case-sensitive. `mypy-case-observation.json` records the actual
Mypy command/output establishing that `[MYPY]` is not the `[mypy]` section.
