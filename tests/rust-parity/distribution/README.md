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

The follow-up corpus adds fourteen actual product HTTP method, redirect,
credential, timeout and response-bound checks:

```sh
cargo build -p ici-distribution --example transport_contract --locked
.venv/bin/python tests/rust-parity/distribution/verify_transport_contract.py \
  target/debug/examples/transport_contract
```

The native build wrapper captures and checks an immutable, normalized source
inventory. It requires Rust 1.99.1, verifies the official target std file tree and
notice catalogue, records the compiler/linker/archiver identities and keeps all
compilation outputs under `target/distribution/`. The default target is musl;
GNU candidates require an explicit target and retain measured libc requirements.
For the isolated SDK used in the actual Linux/WSL measurements:

```sh
CC_x86_64_unknown_linux_musl="$PWD/target/distribution/toolchain-musl/musl/bin/musl-gcc" \
AR_x86_64_unknown_linux_musl=/usr/bin/ar \
CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=/usr/bin/cc \
  scripts/build-native.sh --tools-dir PREPARED_TOOL_INPUTS \
  --sysroot "$PWD/target/distribution/toolchain-musl/rust-sysroot" \
  --sysroot-sha256 1dd241eaeb19ff3b5180461c77bdee93d6cafc669eda9de834e7c06b0ec3ca95 \
  --output CANDIDATE.tar.gz
```

The C compiler uses the isolated musl headers. The final Rust link uses the
verified official self-contained CRT/libc/libunwind with explicit static flags.
The builder refuses `PT_INTERP` or `DT_NEEDED` before executing a musl core. A
`musl-gcc` final linker injected incompatible startup objects in the measured SDK;
using it is not a substitute for the verified self-contained link. `--build-id`
selects an independent compilation directory, with source/cache/build/SDK paths
normalized. It can be used to compare independent builds from frozen inputs.
`--allow-dirty` records a development source snapshot and is not a release claim.
The wrapper installs or downloads no compiler/tool implicitly.

Native Rust notices include the exact source catalogues for the observed Ruff
Rust 1.97.1 and ty Rust 1.99.0 compilers, their referenced terms, musl's full
copyright text, and the pinned LLVM libunwind/compiler-rt licenses and contributor
credits. Core notices are generated from the actual build's Cargo metadata and
Rust 1.99.1 catalogue, with the same native-runtime supplements. Source URLs,
revisions, archive digests and retained file digests live in
`packaging/native/upstream-source-lock.json`. Retained upstream whitespace is
preserved because those file bytes are hashed. Catalogue terms identify their
own applicability; an included compiler catalogue does not assert every component
was linked into each executable.

Eight Rust distribution tests now pass, including rejection of dynamic ELF
inputs before a static bundle is created. The actual musl development candidate
with source tree `04b220851cc1fcc0b83804df8777716d81f566bc785cf3714e9e09b0677548ca`
was a static PIE without an interpreter or shared-library dependencies. Its
SHA-256 was `023bda385c0321aacb0c819d9830b31817ea121c7895900c37e9ee29bb825ed5`
(34,405,115 bytes). Twenty-eight artifact cases completed on the measured WSL
host: archive re-creation from fixed compiled bytes, bootstrap with no Python,
no HOME and a filtered PATH, foreign PATH command preservation, atomic update
failure, update/rollback/reactivation, user data preservation, corrupt/unsafe
archive refusal, offline plan/analysis/report, and explicit private-CA HTTPS
setup. The offline cases made no network connections and used only adjacent
native Ruff/ty tools. The lifecycle variant changed an explicit epoch input at
the same version; it did not represent a second public release.

That candidate's full artifact corpus stopped at its missing native `next doctor`
command. It predates the final native-runtime notice supplements. Those completed
cases are development evidence, not full R8/R9 or final-source acceptance. The
permanent corpus retains the doctor requirement and records completed cases in
`progress.json`, failures in `failed-case.json`, and writes `report.json` only
when every case passes. Its archive re-creation check does not claim a second
compiler rebuild; that is a separate frozen-input comparison.

```sh
.venv/bin/python tests/rust-parity/distribution/verify_artifact.py \
  --archive CANDIDATE.tar.gz --sha256 TRUSTED_SHA256 \
  --builder target/distribution/compiled/release/ici-dist \
  --installer scripts/install-native.sh --evidence EVIDENCE_DIRECTORY
```

The corpus also checks retained native-runtime inventories and actual ELF
headers, creates an offline scaffold, and exercises untrusted CA/HTTP failure and
trusted redirected setup without replacing the current selection on failures.
It uses Python only as a development test driver. The shipped archive and all
observed product subprocesses remain native executables. Static compilation and
these host measurements do not establish untested kernels, older CPUs or other
architectures.

The local publication corpus runs the native CLI against an ephemeral HTTPS
GitHub/GHES fixture. Its six actual cases passed: rejection of the private CA
without trust, trusted GHES API-prefix upload/comment creation, GitHub-style
API-root upload/comment update, HTTP 403, malformed API JSON and a comment failure
after a successful page upload. It verifies the named token and API headers,
base64 page bytes and sticky-comment metadata. Every case preserves the exact
saved analysis and page bytes; failures write a separate `FAILED` publication
record. The saved failing gate remains failing when publication succeeds.

```sh
cargo build -p ici-cli --bin ici --locked
.venv/bin/python tests/rust-parity/distribution/verify_publish.py \
  target/debug/ici --evidence EVIDENCE_DIRECTORY
```

The measured development executable SHA-256 was
`4abb81d9f95b602e87f98f1258801dca28c8bebd4bff4a37a639d0a9116e04cb`.
Every trace contained only that native executable and loopback connections. A
local deny proxy prevented external destinations and received no requests.
Credentials and private response bodies were absent from command output and
publication records. The temporary CA private keys were removed after the run.
This is actual development-binary publication evidence, separate from final
frozen-source artifact acceptance and the complete feature ledger.

A seventh actual publication case exposed a regression: a valid saved finding
with a 100 KB message made the native comment exceed 64 KiB after the page had
already uploaded. The untouched frozen publisher succeeded on the identical
result with a 238-byte count summary. `publish-large-oracle.json` retains those
observations, source digests and the exact input transformation;
`publish_oracle.py FROZEN_CHECKOUT` verifies them without any network request.
The native caller now bounds its comment summary and keeps the full saved result
and page untouched. All seven HTTPS cases passed with native executable
SHA-256 `f8c9df9a628199145ab02a5ecc0c346c019c64f6f45910d3def7cdcebfcb0d15`.

The default installation path is also covered for XDG-only environments without
HOME and for XDG_DATA_HOME set alongside a conflicting HOME. Twelve actual
current-CLI install/inspect/update/rollback operations selected the XDG root,
preserved user data and never created the HOME fallback. That CLI measurement
used SHA-256 `8466942511522a1724aa11aee6ff610c2dca914685293b6ed1a9bc887a193769`;
the older installed candidate was only version-probed. The full artifact corpus
contains corresponding bootstrap and installed-CLI assertions for the next
frozen candidate. Those assertions still require final-artifact execution.
