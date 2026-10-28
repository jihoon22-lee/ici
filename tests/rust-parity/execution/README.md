# Native execution acceptance fixtures

These fixtures exercise `ici-execution` on Linux/WSL. `child.py` is an explicitly
selected external test process, not an ici-owned product backend or a shipped
worker. It imports no ici modules. The runner itself is Rust and invokes argv
without inserting a shell or interpreter.

The 2026-10-28 local run used Rust 1.99.1 and `/usr/bin/python3` only to manufacture
process behaviours. Override that fixture interpreter with `ICI_TEST_PYTHON` when
needed. All test work directories are temporary and removed by the test owner.

## API contract

- `Cancellation::default()` is cloneable; `cancel()` is irreversible and
  `is_cancelled()` observes the same atomic flag. `flag()` lets the CLI install a
  signal handler without the runner changing global signal policy.
- `Runner::run(&TaskSpec, &Cancellation)` returns the original task id and bounded
  raw stdout/stderr bytes. Normal nonzero exits are **completed processes**, not
  automatic code-quality failures. The provider interprets exit codes. Timeout,
  signal, cancellation, truncation, failed start, or interrupted/unverified group
  cleanup cannot be completed observations.
- Output defaults to 8 MiB per stream and refuses a declared limit above 64 MiB.
  `None` timeout uses 300 seconds; it does not disable the bound. Cancellation
  checks also apply while pipes remain open after the leader exits. SIGTERM is
  followed by SIGKILL after bounded grace; direct-child reaping and final capture
  are bounded. Raw binary output is never decoded before a provider sees it.
- The inherited environment is snapshotted through an explicit allowlist, then
  the declared project overlay is applied. ici bundle injection variables and
  paths are removed. Effective environment bytes enter sharing identity.
- A declared `tool_digest` is stream-hashed from the resolved absolute executable
  before scheduling/dedup and again immediately before spawn. A mismatch is a
  failed outcome with no subprocess. A substituted FIFO fails without blocking.
  Missing identity disables sharing. Shebang interpreter/config closure remains
  an additional provider identity input, not something this file hash proves.
- `Scheduler` returns one outcome per original task in original order. Full
  share keys include tool/environment identity and declared execution bounds;
  unknown tool identity is not shared. Cycles/duplicate ids fail before any
  command starts. Missing/failed prerequisites block their consumers; prerequisite
  completion specifically requires exit 0. External `requires` labels are
  provenance, not invented dependency edges.
- Parallelism is capped at the configured 1–64 jobs. Declared resource keys
  serialize tasks within a run. Applications set `Scheduler.lock_directory` to
  the workspace's versioned state directory to enable cross-invocation OS locks.
  Lock files are not deleted on release: the kernel releases the open-descriptor
  lock on close/termination. Waiting for a resource is cancellable and bounded.
- Explicit absolute work directories are supported, preserving existing provider
  plans. Relative directories resolve under cwd. Parent traversal and symlink
  traversal during creation are refused. The application validates which work
  directories belong to the requested plan; the runner does not invent them.

## Storage and redaction

`WriteMode::CreateNew` publishes without replacing any existing destination,
including one created concurrently. `ReplaceRegular` is the explicit atomic
replacement mode. Both use same-directory staging, file fsync, no-follow
ancestor directory descriptors, and reject symlink/non-regular destinations.
Pre-commit serialization/write failures preserve previous bytes and remove the
stage. Directory fsync failure after commit is reported explicitly as committed
but durability-unconfirmed; it is not falsely described as preserving the old
file. ReplaceRegular assumes cooperative destination writers between the final
no-follow check and rename; CreateNew provides the stronger no-overwrite contract.

Event batches are schema/run/sequence validated, messages are redacted, and append
uses a bounded OS lock. Existing malformed/truncated streams are not silently
continued. Write failure attempts rollback to the old length; rollback failure is
reported separately. Saved-result readers are bounded and schema-checked. An
explicit read may follow a symlink to a regular saved result, while writes do not.

Redaction is separate from provider parse bytes. It handles quoted/unquoted secret
assignments, Bearer/CLI tokens, known token prefixes, URL credentials, complete or
truncated private keys, recursive JSON strings and colliding redacted object keys.
Results remain domain data; callers apply reporting/redaction policy at the proper
boundary rather than modifying provider stdout before parsing.

## Actual acceptance coverage

|Group|Actual fixtures|Result|
|---|---|---|
|Process (11)|raw binary and nonzero completion; dual-stream flood; SIGTERM→SIGKILL timeout; pre/during cancellation; descendant holding inherited pipes; signal/start/cwd errors; environment identity/isolation; literal shell syntax and work-dir effects; absolute/no-symlink work dirs; changed executable bytes; FIFO substitution|11 passed|
|Scheduler (8)|complete-key dedup and original ids; missing/failed prerequisite and cycle rejection; resource serialization/jobs bound; partial cancellation; lock timeout/cancel/drop; unknown tool no-sharing; kernel lock release when holder terminated; replaced tool no-sharing/no-spawn|8 passed|
|Storage (9)|injected failed staged write; destination/ancestor symlink; concurrent create; symlink added during staging; event continuity/redaction; recursive credential masking; bounded lock failure; missing/invalid/truncated read; frozen v2 write/read and invalid envelope refusal|9 passed|

Commands (shared host heavy lock; two build/test workers):

```bash
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/tool-spike/execution-target \
  cargo test --locked -p ici-execution -- --test-threads=2
flock /tmp/paseo-products-20261028-heavy.lock env CARGO_BUILD_JOBS=2 \
  CARGO_TARGET_DIR=/tmp/ici-rust-replacement-20261028/tool-spike/execution-target \
  cargo clippy --locked -p ici-execution --all-targets -- -D warnings
cargo fmt -p ici-execution --check
.venv/bin/ruff check tests/rust-parity/execution/child.py
.venv/bin/ruff format --check tests/rust-parity/execution/child.py
```

This is library acceptance, not full R2/product parity closure. Linux `/proc`
proves the owned process group's surviving members, not a security sandbox for
programs that intentionally escape into another session. The application still
owns source snapshot/input closure, provider interpretation, signal wiring,
result/event lifecycle, and final gate/exit mapping. Native Windows/macOS are not
claimed or silently substituted with Python. Full product/corpus CI remains a
separate integration gate.
