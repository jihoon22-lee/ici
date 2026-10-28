# Native diagnostic providers

These fixtures cover Rust planning and interpretation for Ruff lint/format, ty,
mypy, Cargo Clippy, ESLint, TypeScript, GCC/Clang, clang-tidy, and Clazy. The
frozen product source remains v0.13.0 (`6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188`).
This provider milestone does not close the full migration inventory.

## Reproducible gates

Run ordinary replay/resolution/planning checks with the pinned workspace toolchain:

```sh
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  cargo test --locked -p ici-providers -- --test-threads=2
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  cargo clippy --locked -p ici-providers --all-targets -- -D warnings
cargo fmt --all -- --check
```

Measured on 2026-10-29: diagnostic replay 10, planning/configuration 6, resolver 6
passed; Clippy and formatting passed. The default test command explicitly lists
13 ignored native cases. Those cases were separately run with all tools supplied:

```sh
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  cargo test --locked -p ici-providers --test native -- --ignored --test-threads=1
```

That invocation requires `ICI_TEST_RUFF`, `ICI_TEST_TY`, `ICI_TEST_MYPY`,
`ICI_TEST_CARGO`, `ICI_TEST_ESLINT`, `ICI_TEST_TSC`, `ICI_TEST_GXX`, `ICI_TEST_CLANG`,
`ICI_TEST_TIDY`, `ICI_TEST_CLAZY`, and `ICI_TEST_QT_HEADERS`. Rootless LLVM extraction
also supplies `ICI_TEST_CPP_LIBRARY_PATH`. Missing variables/tools fail the native
gate; no installation or conditional success occurs. All 13 native cases passed
in 18.25 seconds. Tested versions are Ruff 0.16.4, ty 0.0.93, mypy 2.3.1,
Cargo/rustc 1.99.1, Node 24.18.0, ESLint 10.13.0, TypeScript 7.0.2, GCC 15.2.0,
LLVM/clang-tidy 21.1.8, Clazy 1.17, and Qt headers 5.15.18.

## Preserved and corrected behavior

- Plan performs no subprocess or output creation. Resolution preserves executable
  invocation aliases, validates explicit failures and manifest bytes, and does not
  install tools. Nested component input references use the workspace source root
  and the component configuration root correctly.
- Actual native configuration controls diagnostics and excludes. Ruff's fix=true
  cannot write source; mypy creates no cache; TypeScript emits no JavaScript or
  declaration files. Clippy build scripts and TypeScript build metadata are declared
  preparation effects, with isolated owned output paths and bounded jobs.
- Malformed/empty/truncated output, invalid configuration, missing native tools,
  unmapped SARIF URI bases, global TypeScript errors, and missing Cargo terminal
  records cannot become an empty clean observation. Notes and related locations
  remain visible. Source targets remain PASS/FAIL/SKIP according to actual evidence.
- Native Unicode columns are converted using a bounded shared index of immutable
  captured source. The two adjacent JSON files retain actual astral/BOM producer
  observations (only temporary executable/root paths normalized). ESLint/TSC use
  UTF-16, mypy/Clang use byte offsets, GCC is explicitly requested to emit byte
  columns. Clang includes the UTF-8 BOM in offsets, GCC does not. ty 0.0.93 omits
  SARIF columnKind while emitting Unicode codepoints; this measured exception is
  version pinned. Explicit SARIF columnKind is honored. Missing source/index leaves
  uncertain columns absent with a limitation rather than guessing.
- Native completion and dependency closure are separate facts. Unknown plugins,
  user settings, package imports, headers/stubs, or generated inputs keep cache and
  resolved-baseline proof disabled. Narrow local Ruff F-family configurations can
  qualify only after a fresh explicit `--show-files` probe and unchanged config
  bytes. Excluded files remain SKIP. This does not claim complete arbitrary Ruff,
  ESLint, TypeScript, Rust, Python, or C++ dependency closure.

Build/context ownership, test/coverage/runtime providers, complete CLI wiring,
installation, and full inventory closure have their own later evidence.
