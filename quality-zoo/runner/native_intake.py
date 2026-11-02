"""Verify one native ici candidate archive and extract its ``bin/ici`` for the corpus runner.

The archive is the ``scripts/build-native.sh`` output (``ici-<version>-<target>.tar.gz``,
schema ``ici.native-distribution`` v1). Its SHA-256 is trusted only when it equals the digest
given out of band. The member list must be exactly the manifest's files plus ``manifest.json``,
every member a regular file with a canonical relative name; only ``bin/ici`` (and a copy of
the manifest) is written, into a new private directory, and its bytes must equal the manifest's
recorded digest and size. ``bin/ici --version`` must report the manifest version, which is the
producer version the corpus expectations bind. The tar module's own extraction is never used.

The pyz candidate ZIP stays verified by ``runner.candidate_intake`` until the R9 cutover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

from runner.common import (
    SHA256_RE,
    ContractError,
    require_bool,
    require_int,
    require_string,
    sha256_file,
)
from runner.run import MAX_ICI_BIN_BYTES

# The native installer's own bounds (crates/ici-distribution/src/model.rs): the archive, the
# expanded total, one member and the manifest. Measured candidates: 33,679,774 B (gnu) and
# 34,405,115 B (musl) compressed, with bin/ici, tools/bin/ruff (25 MB) and tools/bin/ty (33 MB).
MAX_NATIVE_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_NATIVE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_NATIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_NATIVE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_NATIVE_MEMBERS = 8192
SCHEMA_ID = "ici.native-distribution"
SCHEMA_VERSION = 1
TARGETS = {"x86_64-unknown-linux-gnu", "x86_64-unknown-linux-musl"}
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
EXECUTABLE = "bin/ici"
MANIFEST = "manifest.json"
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class NativeCandidate:
    root: Path
    executable: Path
    archive_sha256: str
    executable_sha256: str
    executable_size: int
    version: str
    target: str
    source_revision: str
    development_snapshot: bool


def _canonical_member(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or path.as_posix() != name
    ):
        raise ContractError("unsafe-archive-path", f"unsafe member {name!r}")
    return name


def _members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    total = 0
    for info in archive:
        if len(members) >= MAX_NATIVE_MEMBERS:
            raise ContractError("archive-too-many-members", "native archive has too many members")
        name = _canonical_member(info.name)
        if name in members:
            raise ContractError("duplicate-archive-member", f"native archive repeats {name!r}")
        if not info.isreg():
            raise ContractError("unsafe-archive-member", f"{name!r} is not a regular file")
        if info.size > MAX_NATIVE_MEMBER_BYTES:
            raise ContractError("archive-member-too-large", f"oversized member {name!r}")
        total += info.size
        if total > MAX_NATIVE_TOTAL_BYTES:
            raise ContractError("archive-too-large", "native archive expands beyond its limit")
        members[name] = info
    return members


def _read_member(archive: tarfile.TarFile, info: tarfile.TarInfo, limit: int) -> bytes:
    if info.size > limit:
        raise ContractError("archive-member-too-large", f"oversized member {info.name!r}")
    stream = archive.extractfile(info)
    if stream is None:
        raise ContractError("unsafe-archive-member", f"{info.name!r} has no data")
    with stream:
        data = stream.read(limit + 1)
    if len(data) != info.size:
        raise ContractError("truncated-archive-member", info.name)
    return data


def _validate_manifest(payload: Any, names: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractError("manifest-schema", "native manifest must be an object")
    if payload.get("schema_id") != SCHEMA_ID or payload.get("schema_version") != SCHEMA_VERSION:
        raise ContractError("manifest-schema", f"expected {SCHEMA_ID} v{SCHEMA_VERSION}")
    version = require_string(payload.get("version"), "version")
    if not VERSION_RE.fullmatch(version):
        raise ContractError("manifest-version", f"invalid version {version!r}")
    target = require_string(payload.get("target"), "target")
    if target not in TARGETS:
        raise ContractError("manifest-target", f"untested target {target!r}")
    inputs = payload.get("build_inputs")
    if not isinstance(inputs, dict):
        raise ContractError("manifest-schema", "build_inputs must be an object")
    if inputs.get("target") != target:
        raise ContractError("manifest-target", "build_inputs.target differs from target")
    revision = require_string(inputs.get("source_revision"), "source_revision")
    if not REVISION_RE.fullmatch(revision):
        raise ContractError("manifest-revision", "source_revision must be a full lowercase SHA")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise ContractError("manifest-schema", "files must be a non-empty object")
    for name, entry in files.items():
        _canonical_member(name)
        if not isinstance(entry, dict) or set(entry) != {"size", "sha256", "executable"}:
            raise ContractError("manifest-schema", f"files[{name!r}] is invalid")
        require_int(entry["size"], f"files[{name!r}].size", minimum=0)
        require_bool(entry["executable"], f"files[{name!r}].executable")
        if not SHA256_RE.fullmatch(require_string(entry["sha256"], "sha256")):
            raise ContractError("manifest-schema", f"files[{name!r}].sha256 is invalid")
    if set(files) | {MANIFEST} != names or MANIFEST in files:
        raise ContractError(
            "unexpected-archive-members",
            f"archive members differ from the manifest: {sorted(names ^ (set(files) | {MANIFEST}))!r}",
        )
    executable = files.get(EXECUTABLE)
    if executable is None or executable["executable"] is not True:
        raise ContractError("manifest-executable", "manifest must record an executable bin/ici")
    if executable["size"] > MAX_ICI_BIN_BYTES:
        raise ContractError("archive-member-too-large", "bin/ici exceeds the runner's bound")
    return payload


def _write_private(path: Path, source: IO[bytes], *, size: int, mode: int) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    written = 0
    try:
        descriptor = os.open(path, flags, mode)
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = source.read(min(CHUNK, size + 1 - written))
                if not chunk:
                    break
                written += len(chunk)
                if written > size:
                    raise ContractError("truncated-archive-member", f"{path.name} is longer")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
    except OSError as error:
        raise ContractError("extract-failed", f"cannot create {path}: {error}") from error
    return digest.hexdigest(), written


def _read_version(executable: Path, *, timeout_seconds: int) -> str:
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError("candidate-execution-failed", str(error)) from error
    if completed.returncode != 0:
        raise ContractError(
            "candidate-version-failed",
            f"exit {completed.returncode}: {completed.stderr.strip()}",
        )
    return completed.stdout.strip()


def verify_native_archive(
    archive_path: Path,
    destination: Path,
    *,
    expected_archive_sha256: str,
    timeout_seconds: int = 30,
) -> NativeCandidate:
    """Verify the trusted digest, the manifest and ``bin/ici`` before any scenario runs."""

    if not SHA256_RE.fullmatch(expected_archive_sha256):
        raise ContractError("invalid-archive-digest", "expected archive SHA-256 is invalid")
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ContractError("unsafe-archive", "native archive must be a regular file")
    archive_sha256, _ = sha256_file(archive_path, max_bytes=MAX_NATIVE_ARCHIVE_BYTES)
    if archive_sha256 != expected_archive_sha256:
        raise ContractError(
            "archive-digest-mismatch",
            f"expected {expected_archive_sha256}, got {archive_sha256}",
        )
    if os.path.lexists(destination):
        raise ContractError("destination-exists", f"refusing to replace {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.mkdir(mode=0o700)
    except OSError as error:
        raise ContractError("extract-failed", f"cannot prepare destination: {error}") from error
    try:
        try:
            with tarfile.open(archive_path, mode="r:gz") as archive:
                members = _members(archive)
                if MANIFEST not in members:
                    raise ContractError("manifest-missing", "native archive has no manifest.json")
                manifest_bytes = _read_member(
                    archive, members[MANIFEST], MAX_NATIVE_MANIFEST_BYTES
                )
                try:
                    payload = json.loads(manifest_bytes.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as error:
                    raise ContractError("manifest-json", f"invalid manifest: {error}") from error
                manifest = _validate_manifest(payload, set(members))
                info = members[EXECUTABLE]
                recorded = manifest["files"][EXECUTABLE]
                if info.size != recorded["size"] or not info.mode & stat.S_IXUSR:
                    raise ContractError("executable-member", "bin/ici size or mode differs")
                stream = archive.extractfile(info)
                if stream is None:
                    raise ContractError("unsafe-archive-member", "bin/ici has no data")
                (destination / "bin").mkdir(mode=0o700)
                executable = destination / EXECUTABLE
                with stream:
                    digest, size = _write_private(
                        executable, stream, size=info.size, mode=0o700
                    )
        except (tarfile.TarError, EOFError, OSError) as error:
            raise ContractError("invalid-archive", f"native archive is invalid: {error}") from error
        if size != recorded["size"] or digest != recorded["sha256"]:
            raise ContractError("executable-digest-mismatch", "bin/ici differs from the manifest")
        (destination / MANIFEST).write_bytes(manifest_bytes)
        version_output = _read_version(executable, timeout_seconds=timeout_seconds)
        if version_output != f"ici {manifest['version']}":
            raise ContractError(
                "version-mismatch",
                f"bin/ici reports {version_output!r}, manifest records {manifest['version']!r}",
            )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    inputs = manifest["build_inputs"]
    snapshot = (inputs.get("platform_evidence") or {}).get("development_snapshot")
    return NativeCandidate(
        root=destination,
        executable=executable,
        archive_sha256=archive_sha256,
        executable_sha256=digest,
        executable_size=size,
        version=manifest["version"],
        target=manifest["target"],
        source_revision=inputs["source_revision"],
        development_snapshot=snapshot not in (None, "0", 0, False),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        candidate = verify_native_archive(
            args.archive,
            args.destination,
            expected_archive_sha256=args.archive_sha256,
            timeout_seconds=require_int(args.timeout_seconds, "timeout_seconds", minimum=1),
        )
    except ContractError as error:
        parser.exit(1, f"native intake ERROR [{error.code}]: {error.message}\n")
    summary = {
        "schema": "quality-zoo.native-intake/v1",
        "archive_sha256": candidate.archive_sha256,
        "development_snapshot": candidate.development_snapshot,
        "executable": str(candidate.executable),
        "executable_sha256": candidate.executable_sha256,
        "executable_size": candidate.executable_size,
        "producer_version": candidate.version,
        "source_revision": candidate.source_revision,
        "target": candidate.target,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
