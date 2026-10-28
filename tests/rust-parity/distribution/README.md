# Native distribution evidence

The distribution library owns bounded HTTPS, explicit downloads, canonical native
archives and rootless version selection. These library tests do not close the
full R8 feature ledger or authorize a stable release.

`ici_distribution::transport::send(&Request)` preserves all HTTP statuses for its
caller. It uses reqwest 0.12.28 with native system roots, the existing proxy and
`NO_PROXY` semantics, no redirects/retries, explicit timeouts and response bounds.
Its errors carry no URL, credentials or response body. `SSL_CERT_FILE` and
`SSL_CERT_DIR` retain native-roots replacement semantics. The product-backed
`transport_fixture` example passed all twelve original actual CA/proxy fixtures,
including the public system-CA request:

```sh
cargo build -p ici-distribution --example transport_fixture --locked
.venv/bin/python tests/rust-parity/transport/verify_transport.py \
  target/debug/examples/transport_fixture --public-system-ca
```

Only explicit `download_verified` follows redirects: at most five HTTPS hops,
without URL userinfo, with caller headers removed when the origin changes. It
checks the final payload against an independently supplied SHA-256. Analysis,
planning, init, doctor and report do not call setup or download implicitly.

The `ici.native-distribution` v1 manifest records the source revision/tree digest,
Cargo lock digest, fixed toolchain, target/profile/flags, measured platform
evidence and their combined input digest. Every file has a digest, size and mode.
Canonical USTAR/gzip omits timestamps, owners, archive paths and source file
metadata which could make identical input bytes differ. Archive readers reject
duplicate JSON keys, links/sparse/special files, unsafe or duplicate paths,
unlisted/missing files, size/hash/mode mismatches, gzip corruption, concatenated
streams and trailing content. Limits are 256 MiB compressed, 512 MiB expanded,
8,192 files and 2 MiB per manifest.

Installation validates an entire private staging directory before changing
`current`. Version directories retain their trusted archive receipt. An owned
relative entrypoint and atomic relative selectors prevent overwriting a foreign
`bin/ici`. Concurrent transactions use a nonblocking filesystem lock. Existing
config, result and baseline data are outside version selection and survive
rollback. The core and both tools must pass bounded, pinned version probes with
an empty PATH; static tool ELF files cannot contain a runtime interpreter or
shared-library requirement. There is no `ICI_PYTHON` fallback.

Seven Rust tests passed, along with scoped strict Clippy and formatting. The
five archive/installation tests compile two small static GNU ELF fixtures with
the fixed Rust compiler. They cover byte reproducibility, install/update/rollback,
reattachment, user data preservation, archive tampering, execution failure,
symlink/foreign-prefix/entrypoint refusal, concurrent transactions and nine
malformed archive cases. Two license tests verify dependency roles, deliberate
upstream gaps, credential redaction and escaped license-file refusal. Synthetic
fixtures do not establish real-product GNU/musl, libc or CPU support.

```sh
cargo test -p ici-distribution --locked -- --test-threads=2
cargo clippy -p ici-distribution --all-targets --locked -- -D warnings
cargo fmt --package ici-distribution -- --check
```

The source tool inputs retain the R0 binary/archive/license pins. Source archives
for Ruff 0.16.4 and ty 0.0.93 were independently checked against immutable GitHub
release-asset digests; exact normal/build dependency metadata was then resolved
with their own frozen Cargo locks. The notice collector records proc-macro and
host dependencies separately from target dependencies, retaining actual source
texts and hashes. Missing upstream notices remain `missing_upstream_notice` with
the actual declaration, authors if declared, source revision and a clearly
labeled SPDX MIT reference. No copyright holder/year is invented.

`packaging/native/upstream-notices.json` retains omitted notices recovered from
exact upstream revisions. The MPL-2.0 `version-ranges` source package is supplied
byte-for-byte, with its exact upstream license alongside it. This evidence is
separate from the pending real-product archive, target-toolchain notice and
bootstrap acceptance measurements.
