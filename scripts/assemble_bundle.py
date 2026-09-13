"""Turn a copied tree into a reproducible ici bundle (WP04 #202, PR A).

The WP01 spike proved a standalone bundle runs; it did not produce the same
bytes twice. Two builds from identical input were measured and differed in
1,040 files:

    1033  .pyc under app/ and runtime/   mtime-based invalidation (PEP 552)
       3  app/vendor/bin/*               the build output path in the shebang
       3  *.dist-info/RECORD             carrying the .pyc digests
       1  manifest.json                  carrying the tree digest

Those are exactly the two sources #202 step 4 names — timestamps and build
paths — so this module removes them at the source rather than masking them in
the digest:

``normalize_bytecode``  recompiles every module with hash-based invalidation and
                        rewrites the recorded source path. Invalidation alone was
                        not enough: with headers then matching, the bodies still
                        differed, because every code object carries co_filename —
                        the absolute path it was compiled at. Both builds sat at
                        paths of equal length, so even the file sizes matched. The
                        build path is baked into all 1,000-odd .pyc, not only into
                        the three scripts that show it in plain text.
                        Precompiling matters beyond determinism: WP01 showed the
                        bundle runs from a read-only mount, where a missing .pyc
                        can never be written.
``drop_vendor_scripts`` deletes the dependency console scripts. They are the only
                        files carrying an absolute build path, the launcher does
                        not use them, and ici runs without them — measured, not
                        assumed.

What is left is content-addressed, so the manifest's digests describe the
artifact rather than the machine that produced it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SCHEMA_ID = "ici.next.bundle"
SCHEMA_VERSION = 1

# Dependency entry points. ici is launched through bin/ici, which runs the
# interpreter directly, so nothing in the bundle reads these.
VENDOR_SCRIPT_DIR = ("app", "vendor", "bin")

# The path a bundled module reports as its source. Any fixed string works; this
# one reads as a location rather than as a leftover from someone's machine.
STABLE_SOURCE_ROOT = "/ici-bundle"

# Where a wheel keeps its licence. Checked in this order; several projects ship
# more than one file, so every match is copied.
LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE")


class AssemblyError(RuntimeError):
    """The tree cannot be made into a bundle, and why."""


@dataclass(frozen=True)
class NormalisationReport:
    """What normalisation actually did, so a build log can show it."""

    recompiled_roots: tuple[str, ...]
    removed_scripts: tuple[str, ...]
    collected_licenses: tuple[str, ...]


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def tree_digest(root: Path) -> str:
    """Digest a directory by relative path and content, order-independent.

    Symlinks are digested by their target rather than followed, so a bundle that
    replaced a file with a link to the same content is not reported identical.
    """

    digest = hashlib.sha256()
    for parent, directories, names in os.walk(root):
        directories.sort()
        for name in sorted(names):
            full = Path(parent) / name
            relative = full.relative_to(root).as_posix().encode()
            if full.is_symlink():
                digest.update(relative + b"\0L" + os.readlink(full).encode())
                continue
            digest.update(relative + b"\0F")
            with full.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def normalize_bytecode(root: Path, interpreter: Path) -> None:
    """Recompile every module under ``root`` with hash-based invalidation.

    Existing .pyc are deleted first: the ones shipped in a python-build-standalone
    runtime are mtime-based, and copying the runtime changes those mtimes, which
    is why the runtime tree differed between builds as much as the application
    did.

    ``unchecked-hash`` rather than ``checked-hash`` because the bundle is
    immutable — re-hashing the source on every import would pay for a check whose
    answer cannot change.

    ``-s``/``-p`` rewrite co_filename from the build directory to
    ``STABLE_SOURCE_ROOT``. That is what makes the bodies match, and it also means
    a traceback from a bundled module names a bundle-relative path instead of
    whichever directory the release happened to be built in.
    """

    if not root.is_dir():
        raise AssemblyError(f"cannot normalise bytecode under a missing tree: {root}")
    for cache in sorted(root.rglob("__pycache__"), reverse=True):
        shutil.rmtree(cache, ignore_errors=True)
    completed = subprocess.run(
        [
            str(interpreter),
            "-m",
            "compileall",
            "--invalidation-mode",
            "unchecked-hash",
            # Force. Without it the pass is self-defeating: the bundled
            # interpreter imports argparse, enum, functools and friends while
            # starting compileall, writing fresh mtime-based .pyc for them, and
            # compileall then sees those as current and leaves them alone. 41
            # stdlib modules survived normalisation that way.
            "-f",
            "-s",
            str(root),
            "-p",
            f"{STABLE_SOURCE_ROOT}/{root.name}",
            "-q",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # compileall reports a non-zero status for a module it could not compile.
    # Some vendored packages ship Python 2 or template files that never import;
    # those are not fatal, but silence about them would be.
    if completed.returncode != 0 and completed.stdout.strip():
        sys.stderr.write(f"[assemble] compileall notes:\n{completed.stdout}\n")


def drop_vendor_scripts(bundle: Path) -> tuple[str, ...]:
    """Remove dependency console scripts, returning what was removed.

    These are the only files in the bundle that embed the absolute path the
    bundle was built at. Nothing launches ici through them.
    """

    directory = bundle.joinpath(*VENDOR_SCRIPT_DIR)
    if not directory.is_dir():
        return ()
    removed = tuple(sorted(item.name for item in directory.iterdir()))
    shutil.rmtree(directory)
    return removed


def prune_record_entries(bundle: Path, removed: tuple[str, ...]) -> tuple[str, ...]:
    """Drop RECORD rows for files the bundle no longer ships.

    A wheel's RECORD lists its console scripts with a hash, and that hash is over
    a file whose shebang holds the build path — which is why the three RECORDs
    kept differing after every .pyc had been made deterministic.

    Removing the rows is not only about determinism. #202 asks that the shipped
    checksums match the shipped files, and a RECORD describing a script that was
    deleted is a checksum for something nobody can verify. The .pyc rows need no
    attention: a wheel already writes those with an empty hash and size.
    """

    if not removed:
        return ()
    vendor = bundle / "app" / "vendor"
    if not vendor.is_dir():
        return ()
    suffixes = tuple(f"bin/{name}" for name in removed)
    pruned: list[str] = []
    for record in sorted(vendor.glob("*.dist-info/RECORD")):
        lines = record.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if not line.split(",", 1)[0].endswith(suffixes)]
        if len(kept) != len(lines):
            record.write_text("\n".join(kept) + "\n", encoding="utf-8")
            pruned.append(record.parent.name)
    return tuple(pruned)


def collect_licenses(bundle: Path) -> tuple[str, ...]:
    """Copy every dependency's licence next to the runtime's own.

    #202's acceptance criterion asks that the shipped licences match the shipped
    files. The spike copied CPython's licence and nothing else, so every bundled
    dependency shipped without one.
    """

    vendor = bundle / "app" / "vendor"
    target = bundle / "licenses"
    target.mkdir(parents=True, exist_ok=True)
    collected: list[str] = []
    if not vendor.is_dir():
        return ()
    for dist_info in sorted(vendor.glob("*.dist-info")):
        package = dist_info.name.removesuffix(".dist-info")
        for candidate in sorted(dist_info.rglob("*")):
            if not candidate.is_file():
                continue
            stem = candidate.name.upper()
            if not stem.startswith(LICENSE_NAMES):
                continue
            destination = target / f"{package}-{candidate.name}"
            shutil.copy2(candidate, destination)
            collected.append(destination.name)
    return tuple(sorted(collected))


def normalize(bundle: Path, interpreter: Path) -> NormalisationReport:
    """Apply every normalisation, in the order that keeps them independent.

    Scripts are dropped before bytecode is recompiled so that no .pyc is
    generated for a file that is about to be deleted, and licences are collected
    last because dropping scripts can remove a dist-info's only executable but
    never its licence.
    """

    removed = drop_vendor_scripts(bundle)
    prune_record_entries(bundle, removed)
    for subtree in ("app", "runtime"):
        path = bundle / subtree
        if path.is_dir():
            normalize_bytecode(path, interpreter)
    return NormalisationReport(
        recompiled_roots=tuple(name for name in ("app", "runtime") if (bundle / name).is_dir()),
        removed_scripts=removed,
        collected_licenses=collect_licenses(bundle),
    )


def _command_output(*argv: str) -> str:
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"<unavailable: {err}>"
    return completed.stdout.strip()


def build_manifest(
    bundle: Path,
    *,
    repo: Path,
    runtime_source: str,
    report: NormalisationReport,
) -> dict[str, object]:
    """Describe the artifact: what is in it, where it came from, what it needs."""

    interpreter = bundle / "runtime" / "python" / "bin" / "python3"
    tools_dir = bundle / "tools" / "python-static"
    tools = {}
    if tools_dir.is_dir():
        for tool in sorted(tools_dir.iterdir()):
            if tool.is_file():
                tools[tool.name] = {
                    "digest": file_digest(tool),
                    "version": _command_output(str(tool), "--version"),
                }
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "source_commit": _command_output("git", "-C", str(repo), "rev-parse", "HEAD"),
        "source_dirty": bool(_command_output("git", "-C", str(repo), "status", "--porcelain")),
        "runtime": {
            "source": runtime_source,
            "version": _command_output(
                str(interpreter), "-c", "import sys;print(sys.version.split()[0])"
            ),
            "tree_digest": tree_digest(bundle / "runtime"),
        },
        "application": {"tree_digest": tree_digest(bundle / "app")},
        "tools": tools,
        "licenses": list(report.collected_licenses),
        "normalisation": {
            "bytecode_invalidation": "unchecked-hash",
            "recompiled": list(report.recompiled_roots),
            "removed_vendor_scripts": list(report.removed_scripts),
        },
    }


def write_manifest(bundle: Path, manifest: dict[str, object]) -> Path:
    """Write the manifest deterministically, excluding itself from its digests."""

    path = bundle / "manifest.json"
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        sys.stderr.write("usage: assemble_bundle.py <bundle-dir> <repo-dir> <runtime-source>\n")
        return 2
    bundle, repo, runtime_source = Path(argv[1]), Path(argv[2]), argv[3]
    interpreter = bundle / "runtime" / "python" / "bin" / "python3"
    if not interpreter.exists():
        raise AssemblyError(f"no bundled interpreter at {interpreter}")
    report = normalize(bundle, interpreter)
    manifest = build_manifest(bundle, repo=repo, runtime_source=runtime_source, report=report)
    write_manifest(bundle, manifest)
    for section in ("runtime", "application"):
        digests = manifest[section]
        if isinstance(digests, dict):
            sys.stdout.write(f"{section} digest: {digests['tree_digest']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
