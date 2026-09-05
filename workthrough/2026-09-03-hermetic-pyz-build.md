# Hermetic ZipApp build — 2026-09-03

## Overview

This workthrough records the hermetic/reproducible `ici.pyz` packaging slice on
`fix/hermetic-pyz-build`. It separates the locked shipped runtime from the locked packaging
tools, removes filesystem and build-machine drift, and verifies that hostile caller settings do
not change the resulting ZipApp. The work is documentation for the implementation already
present in the branch; ici remains at `v0.10.2` and this slice does not create a release.

## Context

The previous packaging path installed the project and `shiv` through a shared environment. That
left dependency selection and packaging-tool provenance less explicit, and archive output could
inherit caller-controlled timestamps, hash randomization, timezone, or permission masks. A
reproducible artifact needs separate lock scopes for what ships and what builds it, plus a stable
filesystem boundary before archive creation.

## Changes Made

### 1. Separate locked runtime and package groups

`pyproject.toml` now declares a `package` dependency group containing `hatchling>=1.27,<2` and
`shiv==1.0.8`. `uv.lock` records the resolved versions and wheel/sdist hashes. The dev group
continues to serve repository development; it is not copied into the artifact.

`build-pyz.sh` creates two requirements files from the frozen lock:

```text
runtime-requirements.txt  ← uv export --frozen --no-dev --no-emit-project
package-requirements.txt  ← uv export --frozen --only-group package --no-emit-project
```

Both installations use `--require-hashes`, `--only-binary :all:`, and `--link-mode copy`, so a
locked sdist cannot execute an unmodeled build backend. The build entrypoint and packaging
workflows require uv `0.12.5`. The package group is installed
into `build/package-tools`, while the runtime graph is installed into
`build/site-packages`. Hatchling builds exactly one Python 3.10-targeted project wheel in
`build/wheels`; that wheel is then installed into the runtime tree with `--no-deps`. The
packaging tools therefore execute from an explicit, locked location without becoming shipped
runtime dependencies.

### 2. Canonical environment and archive inputs

The build exports the repository's canonical packaging environment:

| Input | Canonical value | Purpose |
|---|---|---|
| `SOURCE_DATE_EPOCH` | `1700000000` (`2023-11-14 22:13:20` UTC) | archive/member and shiv timestamps |
| `PYTHONHASHSEED` | `0` | stable hash-dependent ordering |
| `PYTHONUTF8` / locale | `1` / `C` | stable Python text decoding and process locale |
| `TZ` | `UTC` | stable time formatting |
| `umask` | `022` | stable default permissions |

The epoch is deliberately fixed rather than derived from the commit or wall clock. Before
`shiv` runs, machine-specific `direct_url.json`, `uv_cache.json`, and `uv_build.json` metadata,
the target `.lock`, and installed `bin/` launchers are removed. `RECORD` is updated after those
removals. The runtime and packaging-tool trees are traversed afterward: symlinks and unsupported
filesystem entries fail closed; regular files become `0644` and directories `0755`.
Shiv-generated `environment.json` and `__main__.py` keep its deterministic synthetic `0600`
mode. The build resolves one selected Python 3.10+ helper interpreter and uses it for every package,
cleanup, and assembly helper. `scripts/assemble_pyz.py` pre-checks inputs with nonblocking
`lstat`/open semantics, so FIFO and other special inputs are rejected without blocking; regular
inputs are then read without following symlinks. It anchors the output directory by descriptor,
rejects existing symlink/special outputs, writes same-directory temporary files, and makes
hard-link backups for every existing output before publication. Each name is replaced atomically
with `os.replace`; a mid-publication or post-check failure restores the previous consistent
output set (or removes a name that did not exist before the build). Write/flush/`fsync` failures
clean their temporary files. The final `dist/ici.pyz` and `dist/ici` are checked for byte identity
and mode `0755`.

### 3. Pure-Python and package contract checks

The existing package checks remain part of the pipeline. Any native extension, non-`py3-none-any`
wheel tag, `certifi` directory, or missing public JSON schema stops the build. The raw shiv
output must have a ZIP signature, and the final output is formed only by prepending the checked-in
`scripts/launcher.sh` preamble. The artifact is therefore still a single polyglot ZipApp that
uses the normal Python 3.10+ discovery path.

## Adversarial reproducibility verification

`scripts/verify-reproducibility.sh` takes a source-status snapshot, performs two complete builds,
and compares the resulting `dist/ici.pyz` SHA-256 values. Each invocation deliberately supplies
different values to every common source of packaging drift:

| Build | `umask` | `SOURCE_DATE_EPOCH` | `PYTHONHASHSEED` / `PYTHONUTF8` | locale | `TZ` |
|---|---:|---:|---|---|---|
| First | `077` | `1` | `random` / `0` | `C.utf8` | `Pacific/Honolulu` |
| Second | `002` | `4102444800` | `123` / `1` | `POSIX` | `Asia/Seoul` |

The build script must override these settings with its canonical values. The verifier then opens
the archive and requires every member to use the timestamp for epoch `1700000000`, checks the
canonical installed/bootstrap and synthetic top-level modes, rejects `site-packages/.lock`,
confirms shiv's `environment.json` has `built_at = "2023-11-14 22:13:20"`, and requires the two
final executable names to be byte-identical `0755` files. Finally it compares the source-status
snapshot with the post-build status so generated artifacts cannot mutate the checkout.

## Code Examples

The build's two lock scopes and canonical environment are represented by this compact excerpt:

```bash
readonly CANONICAL_SOURCE_DATE_EPOCH=1700000000
export SOURCE_DATE_EPOCH="$CANONICAL_SOURCE_DATE_EPOCH"
export PYTHONHASHSEED=0
export PYTHONUTF8=1
export LANG=C
export LC_ALL=C
export TZ=UTC
umask 022

uv export --frozen --no-dev --no-emit-project --output-file build/runtime-requirements.txt
uv export --frozen --only-group package --no-emit-project \
  --output-file build/package-requirements.txt
uv pip install --require-hashes --only-binary :all: --link-mode copy \
  --requirements build/package-requirements.txt
```

The reproducibility verifier supplies deliberately hostile inputs around each build:

```bash
( umask 077; SOURCE_DATE_EPOCH=1 PYTHONHASHSEED=random TZ=Pacific/Honolulu \
    ./scripts/build-pyz.sh )
( umask 002; SOURCE_DATE_EPOCH=4102444800 PYTHONHASHSEED=123 TZ=Asia/Seoul \
    ./scripts/build-pyz.sh )
```

## Files and scope

The implementation and documentation boundary for this slice is:

- `scripts/build-pyz.sh` — locked installs, canonical environment, cleanup, mode normalization,
  symlink rejection, and ZipApp assembly.
- `scripts/assemble_pyz.py` — bounded nonblocking no-follow reads, descriptor-anchored output
  publication, hard-link rollback backups, and pre-commit content/mode checks.
- `scripts/verify-reproducibility.sh` — adversarial two-build and archive/source-status checks.
- `pyproject.toml` and `uv.lock` — the separate `package` group and its locked hashes.
- `tests/test_purity.py` — source-level regression assertions for the hermetic build contract.
- `CHANGELOG.md`, `README.md`, `docs/architecture.md`, and this workthrough — synchronized
  documentation.

## Verification Results

| Check | Result |
|---|---|
| Lock separation | Runtime and `package` requirements are exported with `--frozen`; both installs use hashes and wheels only; uv is pinned to `0.12.5`. |
| Packaging isolation | Hatchling and `shiv==1.0.8` run from `build/package-tools`; the shipped tree receives the project wheel and runtime graph only. |
| Hermetic inputs | Canonical epoch `1700000000`, `PYTHONHASHSEED=0`, `TZ=UTC`, `umask 022`, fixed modes, metadata cleanup, and symlink rejection are enforced. |
| Adversarial builds | PASS — both differing umask/epoch/hash-seed/timezone/locale invocations produced a byte-identical 2,291,840-byte artifact with SHA-256 `ca57959e76c6c1421943b99e737190264e3c1c4dfdcfd883caba0873d0fee3c8`. |
| Cross-path/source-mtime audit | PASS — a copied tree at a second absolute path, with all `src/` and `scripts/` mtimes changed to 2001-02-03 and a hostile caller environment, produced the same SHA-256. The temporary tree was removed afterward. |
| Artifact boundary | PASS — native/platform wheels, sdist execution, `certifi`, missing schemas, input/output symlinks, FIFO/special entries, leaked locks, source mutation, rollback, and temporary-file cleanup are covered. |
| Focused regression | `uv run --python 3.10 pytest -q tests/test_pyz_assembly.py tests/test_purity.py` — 47 passed. |
| Full Python 3.10 suite | `uv run --python 3.10 pytest` — 2,185 passed, 7 environment-dependent C++ tool skips (2,192 collected). |
| Static quality | Ruff check and format — 195 files PASS; mypy including the assembler — 108 source files PASS. |
| Script/static checks | `bash -n scripts/build-pyz.sh scripts/verify-reproducibility.sh` and `actionlint .github/workflows/*.yml` — PASS. |
| Packaged smoke | `./scripts/smoke.sh` — direct/3.10 execution, doctor/env, artifact identity, self verification, and Zero-CDN PASS. |
| Documentation check | `git diff --check` — PASS. The current branch's full build/test evidence is recorded above. |
| Release state | No version bump, tag, or release; ici remains at `v0.10.2`. |

## Scope boundary

This slice documents and verifies build determinism. It does not alter analyzer runtime behavior,
release cadence, the plans/handover documents, or the stable artifact contract. The current branch
passed the normal full test, static-quality, ZipApp, and smoke integration gates recorded above.
