#!/usr/bin/env python3
"""Create a bounded, checksum-addressed ici candidate artifact bundle.

The candidate bundle is intentionally smaller and shorter-lived than a stable
release.  All provenance values are supplied by the trusted workflow; this
helper never tries to infer a version or commit from the executable.  The
source and destination boundaries are kept descriptor based so a source file
replacement or a pre-existing destination cannot silently change what gets
published.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# A pyz is normally only a few megabytes.  Keeping this bound explicit makes a
# bad workflow input fail before an unbounded read or upload can occur.
MAX_PYZ_BYTES = 64 * 1024 * 1024
MAX_GITHUB_ID = (1 << 63) - 1
MAX_METADATA_BYTES = 64 * 1024
COPY_CHUNK_BYTES = 1024 * 1024

_SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SEMVER_CORE_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
_SEMVER_IDENTIFIER_PATTERN = re.compile(r"^[0-9A-Za-z-]+$")
_URL_ID_PATTERN = r"[1-9][0-9]{0,18}"
_MAX_JSON_NODES = 100_000
_MAX_JSON_DEPTH = 100

CANDIDATE_WORKFLOW = ".github/workflows/candidate-artifact.yml"
_ALLOWED_FILES = frozenset({"ici.pyz", "ici.pyz.sha256", "candidate-provenance.json"})
_PROVENANCE_FIELDS = (
    "schema",
    "channel",
    "stable",
    "repository",
    "target_sha",
    "package_version",
    "candidate_workflow",
    "workflow_definition_sha",
    "candidate_run_id",
    "candidate_run_attempt",
    "merge_gate_check_run_id",
    "merge_gate_run_id",
    "merge_gate_job_url",
    "merge_gate_url",
    "artifact_file",
    "artifact_file_sha256",
    "artifact_file_size",
    "retention_days",
)


class CandidateBundleError(ValueError):
    """Raised when a candidate bundle would violate its filesystem contract."""


def _as_absolute_path(value: str | os.PathLike[str], label: str) -> str:
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise CandidateBundleError(f"{label} must be a filesystem path") from exc
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise CandidateBundleError(f"{label} must be a non-empty text path")
    return os.path.abspath(raw)


def _node_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _source_signature(info: os.stat_result) -> tuple[int, ...]:
    """Return metadata which detects replacement and ordinary in-place edits."""

    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_chain(path: str, label: str) -> list[tuple[str, tuple[int, int, int]]]:
    """Validate an existing directory path and retain identities for rechecking."""

    chain: list[tuple[str, tuple[int, int, int]]] = []
    current = os.path.sep
    parts = Path(path).parts
    if not parts or parts[0] != os.path.sep:
        raise CandidateBundleError(f"{label} must be absolute")
    for component in parts[1:]:
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise CandidateBundleError(f"{label} cannot be inspected: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise CandidateBundleError(f"{label} cannot contain a symlink: {current}")
        if not stat.S_ISDIR(info.st_mode):
            raise CandidateBundleError(f"{label} component is not a directory: {current}")
        chain.append((current, _node_identity(info)))
    return chain


def _recheck_directory_chain(chain: list[tuple[str, tuple[int, int, int]]], label: str) -> None:
    for path, expected in chain:
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise CandidateBundleError(f"{label} changed while it was used: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or _node_identity(info) != expected:
            raise CandidateBundleError(f"{label} changed while it was used: {path}")


def _open_source(
    source_path: str,
) -> tuple[int, os.stat_result, list[tuple[str, tuple[int, int, int]]]]:
    """Open a source pyz without following its final path component."""

    parent_chain = _directory_chain(os.path.dirname(source_path), "source parent")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CandidateBundleError("O_NOFOLLOW is required for candidate bundles")
    flags = os.O_RDONLY | nofollow
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(source_path, flags)
    except OSError as exc:
        raise CandidateBundleError(f"source pyz cannot be opened safely: {exc}") from exc

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise CandidateBundleError("source pyz must be a regular file")
        if not 0 < info.st_size <= MAX_PYZ_BYTES:
            raise CandidateBundleError(
                f"source pyz size is outside the accepted bound: {info.st_size}"
            )
        named = os.lstat(source_path)
        if not stat.S_ISREG(named.st_mode) or _source_signature(named) != _source_signature(info):
            raise CandidateBundleError("source pyz changed while it was opened")
        return descriptor, info, parent_chain
    except CandidateBundleError:
        os.close(descriptor)
        raise
    except OSError as exc:
        os.close(descriptor)
        raise CandidateBundleError(f"source pyz cannot be inspected safely: {exc}") from exc


def _check_source_unchanged(
    source_path: str,
    descriptor: int,
    initial: os.stat_result,
    parent_chain: list[tuple[str, tuple[int, int, int]]],
) -> None:
    try:
        final = os.fstat(descriptor)
        named = os.lstat(source_path)
    except OSError as exc:
        raise CandidateBundleError(f"source pyz cannot be rechecked: {exc}") from exc
    if (
        not stat.S_ISREG(final.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or _source_signature(final) != _source_signature(initial)
        or _source_signature(named) != _source_signature(initial)
    ):
        raise CandidateBundleError("source pyz changed while it was copied")
    _recheck_directory_chain(parent_chain, "source parent")


def _open_new_file(directory_fd: int, name: str, mode: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CandidateBundleError("O_NOFOLLOW is required for candidate bundles")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(name, flags, mode, dir_fd=directory_fd)
        os.fchmod(descriptor, mode)
        return descriptor
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise CandidateBundleError(f"cannot create bundle file {name}: {exc}") from exc


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as exc:
            raise CandidateBundleError(f"bundle file cannot be written: {exc}") from exc
        if written <= 0:
            raise CandidateBundleError("bundle file write made no progress")
        offset += written


def _copy_source(
    source_descriptor: int,
    source_initial: os.stat_result,
    source_path: str,
    source_parent_chain: list[tuple[str, tuple[int, int, int]]],
    output_directory_fd: int,
) -> tuple[str, int]:
    output_descriptor = _open_new_file(output_directory_fd, "ici.pyz", 0o755)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            remaining = MAX_PYZ_BYTES - total
            if remaining < 0:
                raise CandidateBundleError("source pyz exceeds the accepted bound")
            try:
                chunk = os.read(source_descriptor, min(COPY_CHUNK_BYTES, remaining + 1))
            except OSError as exc:
                raise CandidateBundleError(f"source pyz cannot be read safely: {exc}") from exc
            if not chunk:
                break
            if len(chunk) > remaining:
                raise CandidateBundleError("source pyz exceeds the accepted bound")
            _write_all(output_descriptor, chunk)
            digest.update(chunk)
            total += len(chunk)
        if total != source_initial.st_size:
            raise CandidateBundleError("source pyz changed size while it was copied")
        _check_source_unchanged(source_path, source_descriptor, source_initial, source_parent_chain)
        try:
            os.fsync(output_descriptor)
        except OSError as exc:
            raise CandidateBundleError(f"ici.pyz cannot be flushed: {exc}") from exc
    finally:
        os.close(output_descriptor)
    return digest.hexdigest(), total


def _validate_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA1_PATTERN.fullmatch(value) is None:
        raise CandidateBundleError(f"{label} must be a lowercase 40-character SHA")
    return value


def _validate_repository(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or len(value.encode("utf-8")) > 200
        or _REPOSITORY_PATTERN.fullmatch(value) is None
    ):
        raise CandidateBundleError("repository must be an owner/name pair")
    return value


def _validate_semver(value: object) -> str:
    if not isinstance(value, str) or not value.isascii() or len(value) > 256:
        raise CandidateBundleError("package_version must be a semver without a leading v")
    match = _SEMVER_CORE_PATTERN.match(value)
    if match is None:
        raise CandidateBundleError("package_version must be a semver without a leading v")
    remainder = value[match.end() :]
    if remainder:
        prerelease, separator, build = remainder.partition("+")
        if separator and not build:
            raise CandidateBundleError("package_version must be a valid semver")
        if prerelease.startswith("-"):
            identifiers = prerelease[1:].split(".")
            if not identifiers or any(
                _SEMVER_IDENTIFIER_PATTERN.fullmatch(item) is None
                or (item.isdigit() and len(item) > 1 and item.startswith("0"))
                for item in identifiers
            ):
                raise CandidateBundleError("package_version must be a valid semver")
        elif prerelease:
            raise CandidateBundleError("package_version must be a valid semver")
        if separator:
            build_identifiers = build.split(".")
            if not build_identifiers or any(
                _SEMVER_IDENTIFIER_PATTERN.fullmatch(item) is None for item in build_identifiers
            ):
                raise CandidateBundleError("package_version must be a valid semver")
    return value


def _positive_id(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > MAX_GITHUB_ID:
        raise CandidateBundleError(f"{label} must be a positive GitHub ID")
    return value


def _validate_merge_gate_url(repository: str, run_id: int, value: object) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 512:
        raise CandidateBundleError("merge_gate_url must be the canonical GitHub Actions URL")
    pattern = re.compile(
        rf"^https://github\.com/{re.escape(repository)}/actions/runs/({_URL_ID_PATTERN})$"
    )
    match = pattern.fullmatch(value)
    if match is None or int(match.group(1)) != run_id:
        raise CandidateBundleError("merge_gate_url must be the canonical GitHub Actions URL")
    return value


def _validate_merge_gate_job_url(repository: str, run_id: int, value: object) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 512:
        raise CandidateBundleError(
            "merge_gate_job_url must be the canonical GitHub Actions job URL"
        )
    pattern = re.compile(
        rf"^https://github\.com/{re.escape(repository)}/actions/runs/"
        rf"({_URL_ID_PATTERN})/job/({_URL_ID_PATTERN})$"
    )
    match = pattern.fullmatch(value)
    if match is None or int(match.group(1)) != run_id:
        raise CandidateBundleError(
            "merge_gate_job_url must be the canonical GitHub Actions job URL"
        )
    _positive_id(int(match.group(2)), "merge_gate_job_id")
    return value


def _validate_arguments(
    repository: object,
    target_sha: object,
    package_version: object,
    workflow_definition_sha: object,
    candidate_run_id: object,
    candidate_run_attempt: object,
    merge_gate_check_run_id: object,
    merge_gate_run_id: object,
    merge_gate_job_url: object,
    merge_gate_url: object,
) -> tuple[str, str, str, str, int, int, int, int, str, str]:
    valid_repository = _validate_repository(repository)
    valid_target_sha = _validate_sha(target_sha, "target_sha")
    valid_package_version = _validate_semver(package_version)
    valid_workflow_sha = _validate_sha(workflow_definition_sha, "workflow_definition_sha")
    valid_candidate_run_id = _positive_id(candidate_run_id, "candidate_run_id")
    valid_candidate_run_attempt = _positive_id(candidate_run_attempt, "candidate_run_attempt")
    valid_merge_gate_check_run_id = _positive_id(merge_gate_check_run_id, "merge_gate_check_run_id")
    valid_merge_gate_run_id = _positive_id(merge_gate_run_id, "merge_gate_run_id")
    valid_job_url = _validate_merge_gate_job_url(
        valid_repository, valid_merge_gate_run_id, merge_gate_job_url
    )
    valid_url = _validate_merge_gate_url(valid_repository, valid_merge_gate_run_id, merge_gate_url)
    return (
        valid_repository,
        valid_target_sha,
        valid_package_version,
        valid_workflow_sha,
        valid_candidate_run_id,
        valid_candidate_run_attempt,
        valid_merge_gate_check_run_id,
        valid_merge_gate_run_id,
        valid_job_url,
        valid_url,
    )


def _json_bytes(value: dict[str, Any]) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload = (text + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise CandidateBundleError(f"candidate provenance is not serializable: {exc}") from exc
    if len(payload) > MAX_METADATA_BYTES:
        raise CandidateBundleError("candidate provenance exceeds the metadata bound")
    return payload


def _bounded_json_int(raw: str) -> int:
    if len(raw.lstrip("-")) > 19:
        raise ValueError("JSON integer exceeds 19 decimal digits")
    return int(raw)


def _bounded_json_float(raw: str) -> float:
    if len(raw) > 100:
        raise ValueError("JSON float exceeds 100 characters")
    value = float(raw)
    if not value == value or value in (float("inf"), float("-inf")):
        raise ValueError("JSON float must be finite")
    return value


def _reject_json_constant(raw: str) -> None:
    raise ValueError(f"non-standard JSON constant: {raw}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _bound_json_shape(value: Any) -> None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise CandidateBundleError("candidate provenance contains too many values")
        if depth > _MAX_JSON_DEPTH:
            raise CandidateBundleError("candidate provenance is nested too deeply")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _read_bundle_file(directory_fd: int, name: str, maximum: int, expected_mode: int) -> bytes:
    """Read a bounded regular file through a directory descriptor."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CandidateBundleError("O_NOFOLLOW is required for candidate bundles")
    flags = os.O_RDONLY | nofollow
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise CandidateBundleError(f"bundle file {name} cannot be opened safely: {exc}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise CandidateBundleError(f"bundle file {name} must be regular")
        if not 0 <= initial.st_size <= maximum:
            raise CandidateBundleError(f"bundle file {name} exceeds its size bound")
        parts: list[bytes] = []
        total = 0
        while True:
            remaining = maximum - total
            if remaining < 0:
                raise CandidateBundleError(f"bundle file {name} exceeds its size bound")
            try:
                chunk = os.read(descriptor, min(COPY_CHUNK_BYTES, remaining + 1))
            except OSError as exc:
                raise CandidateBundleError(f"bundle file {name} cannot be read: {exc}") from exc
            if not chunk:
                break
            if len(chunk) > remaining:
                raise CandidateBundleError(f"bundle file {name} exceeds its size bound")
            parts.append(chunk)
            total += len(chunk)
        final = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _source_signature(final) != _source_signature(initial) or _source_signature(
            named
        ) != _source_signature(initial):
            raise CandidateBundleError(f"bundle file {name} changed while it was read")
        if stat.S_IMODE(initial.st_mode) != expected_mode:
            raise CandidateBundleError(f"bundle file {name} has an unexpected mode")
        return b"".join(parts)
    except CandidateBundleError:
        raise
    except OSError as exc:
        raise CandidateBundleError(f"bundle file {name} cannot be verified: {exc}") from exc
    finally:
        os.close(descriptor)


def _open_existing_directory(
    path: str,
) -> tuple[int, tuple[int, int, int], list[tuple[str, tuple[int, int, int]]]]:
    parent_chain = _directory_chain(os.path.dirname(path), "bundle parent")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CandidateBundleError("O_NOFOLLOW is required for candidate bundles")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        named = os.lstat(path)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise CandidateBundleError(f"bundle directory cannot be opened safely: {exc}") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or _node_identity(info) != _node_identity(named)
    ):
        os.close(descriptor)
        raise CandidateBundleError("bundle directory must be a regular non-symlink directory")
    return descriptor, _node_identity(info), parent_chain


def _validate_provenance(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(_PROVENANCE_FIELDS):
        raise CandidateBundleError("candidate provenance fields are not exact")
    if value["schema"] != "ici.candidate/v1":
        raise CandidateBundleError("candidate provenance schema is invalid")
    if value["channel"] != "candidate":
        raise CandidateBundleError("candidate provenance channel is invalid")
    if value["stable"] is not False:
        raise CandidateBundleError("candidate provenance stable must be false")
    repository = _validate_repository(value["repository"])
    _validate_sha(value["target_sha"], "target_sha")
    _validate_semver(value["package_version"])
    if value["candidate_workflow"] != CANDIDATE_WORKFLOW:
        raise CandidateBundleError("candidate workflow path is invalid")
    _validate_sha(value["workflow_definition_sha"], "workflow_definition_sha")
    _positive_id(value["candidate_run_id"], "candidate_run_id")
    _positive_id(value["candidate_run_attempt"], "candidate_run_attempt")
    _positive_id(value["merge_gate_check_run_id"], "merge_gate_check_run_id")
    merge_gate_run_id = _positive_id(value["merge_gate_run_id"], "merge_gate_run_id")
    _validate_merge_gate_job_url(repository, merge_gate_run_id, value["merge_gate_job_url"])
    _validate_merge_gate_url(repository, merge_gate_run_id, value["merge_gate_url"])
    if value["artifact_file"] != "ici.pyz":
        raise CandidateBundleError("candidate artifact filename is invalid")
    if (
        not isinstance(value["artifact_file_sha256"], str)
        or _SHA256_PATTERN.fullmatch(value["artifact_file_sha256"]) is None
    ):
        raise CandidateBundleError("artifact_file_sha256 must be a lowercase SHA-256")
    artifact_size = value["artifact_file_size"]
    if (
        isinstance(artifact_size, bool)
        or not isinstance(artifact_size, int)
        or not 0 < artifact_size <= MAX_PYZ_BYTES
    ):
        raise CandidateBundleError("artifact_file_size is outside the accepted bound")
    if (
        isinstance(value["retention_days"], bool)
        or not isinstance(value["retention_days"], int)
        or value["retention_days"] != 14
    ):
        raise CandidateBundleError("retention_days must be exactly 14")
    return value


def verify_bundle(bundle_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify the exact three-file candidate bundle and return its provenance."""

    bundle_path = _as_absolute_path(bundle_dir, "bundle directory")
    descriptor, directory_identity, parent_chain = _open_existing_directory(bundle_path)
    try:
        try:
            names = set(os.listdir(descriptor))
        except OSError as exc:
            raise CandidateBundleError(f"bundle directory cannot be listed: {exc}") from exc
        if names != set(_ALLOWED_FILES):
            raise CandidateBundleError("candidate bundle contains an unexpected file")
        pyz = _read_bundle_file(descriptor, "ici.pyz", MAX_PYZ_BYTES, 0o755)
        sidecar = _read_bundle_file(descriptor, "ici.pyz.sha256", MAX_METADATA_BYTES, 0o644)
        manifest_payload = _read_bundle_file(
            descriptor, "candidate-provenance.json", MAX_METADATA_BYTES, 0o644
        )
        try:
            value = json.loads(
                manifest_payload.decode("utf-8"),
                parse_int=_bounded_json_int,
                parse_float=_bounded_json_float,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_unique_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise CandidateBundleError(f"candidate provenance is not valid JSON: {exc}") from exc
        _bound_json_shape(value)
        provenance = _validate_provenance(value)
        if _json_bytes(provenance) != manifest_payload:
            raise CandidateBundleError("candidate provenance JSON is not deterministic")
        artifact_sha = hashlib.sha256(pyz).hexdigest()
        if provenance["artifact_file_sha256"] != artifact_sha:
            raise CandidateBundleError("candidate artifact SHA-256 does not match provenance")
        if provenance["artifact_file_size"] != len(pyz):
            raise CandidateBundleError("candidate artifact size does not match provenance")
        expected_sidecar = f"{artifact_sha}  ici.pyz\n".encode("ascii")
        if sidecar != expected_sidecar:
            raise CandidateBundleError("candidate checksum sidecar is invalid")
        current_directory = os.fstat(descriptor)
        named_directory = os.lstat(bundle_path)
        if (
            _node_identity(current_directory) != directory_identity
            or _node_identity(named_directory) != directory_identity
            or not stat.S_ISDIR(named_directory.st_mode)
        ):
            raise CandidateBundleError("bundle directory changed while it was verified")
        _recheck_directory_chain(parent_chain, "bundle parent")
        try:
            final_names = set(os.listdir(descriptor))
        except OSError as exc:
            raise CandidateBundleError(f"bundle directory cannot be re-listed: {exc}") from exc
        if final_names != set(_ALLOWED_FILES):
            raise CandidateBundleError("candidate bundle contains an unexpected file")
        return provenance
    finally:
        os.close(descriptor)


def _write_bundle_file(directory_fd: int, name: str, payload: bytes, mode: int) -> None:
    descriptor = _open_new_file(directory_fd, name, mode)
    try:
        _write_all(descriptor, payload)
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise CandidateBundleError(f"bundle file {name} cannot be flushed: {exc}") from exc
    finally:
        os.close(descriptor)


def _remove_empty_created_directory(
    path: str,
    identity: tuple[int, int, int],
    parent_chain: list[tuple[str, tuple[int, int, int]]],
) -> None:
    """Remove a newly-created empty directory only if its identity is unchanged."""

    try:
        _recheck_directory_chain(parent_chain, "output parent")
        named = os.lstat(path)
        if not stat.S_ISDIR(named.st_mode) or _node_identity(named) != identity:
            return
        os.rmdir(path)
    except (CandidateBundleError, OSError):
        return


def _open_output_directory(
    output_path: str,
) -> tuple[int, tuple[int, int, int], list[tuple[str, tuple[int, int, int]]]]:
    parent_chain = _directory_chain(os.path.dirname(output_path), "output parent")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise CandidateBundleError("O_NOFOLLOW is required for candidate bundles")
    try:
        os.mkdir(output_path, 0o700)
    except FileExistsError as exc:
        raise CandidateBundleError("output directory must not pre-exist") from exc
    except OSError as exc:
        raise CandidateBundleError(f"output directory cannot be created: {exc}") from exc

    try:
        created = os.lstat(output_path)
    except OSError as exc:
        raise CandidateBundleError(f"output directory cannot be inspected: {exc}") from exc
    if not stat.S_ISDIR(created.st_mode):
        raise CandidateBundleError("output directory is not a directory")
    created_identity = _node_identity(created)
    flags = os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(output_path, flags)
        info = os.fstat(descriptor)
        named = os.lstat(output_path)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        _remove_empty_created_directory(output_path, created_identity, parent_chain)
        raise CandidateBundleError(f"output directory cannot be opened safely: {exc}") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or _node_identity(info) != _node_identity(named)
    ):
        os.close(descriptor)
        _remove_empty_created_directory(output_path, created_identity, parent_chain)
        raise CandidateBundleError("output directory changed while it was created")
    return descriptor, _node_identity(info), parent_chain


def _assert_output_directory(
    output_path: str,
    directory_fd: int,
    directory_identity: tuple[int, int, int],
    parent_chain: list[tuple[str, tuple[int, int, int]]],
) -> None:
    try:
        directory_info = os.fstat(directory_fd)
        named_info = os.lstat(output_path)
        names = set(os.listdir(directory_fd))
    except OSError as exc:
        raise CandidateBundleError(f"output directory cannot be verified: {exc}") from exc
    if (
        _node_identity(directory_info) != directory_identity
        or not stat.S_ISDIR(named_info.st_mode)
        or _node_identity(named_info) != directory_identity
    ):
        raise CandidateBundleError("output directory changed while it was written")
    _recheck_directory_chain(parent_chain, "output parent")
    if names != set(_ALLOWED_FILES):
        raise CandidateBundleError("candidate bundle contains an unexpected file")

    expected_modes = {"ici.pyz": 0o755, "ici.pyz.sha256": 0o644, "candidate-provenance.json": 0o644}
    for name in _ALLOWED_FILES:
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise CandidateBundleError(f"bundle file {name} cannot be verified: {exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise CandidateBundleError(f"bundle file {name} must be regular")
        if stat.S_IMODE(info.st_mode) != expected_modes[name]:
            raise CandidateBundleError(f"bundle file {name} has an unexpected mode")


def _cleanup_created_output(
    output_path: str,
    directory_identity: tuple[int, int, int] | None,
    parent_chain: list[tuple[str, tuple[int, int, int]]] | None,
) -> None:
    """Remove only a directory this invocation created and only its known files."""

    if directory_identity is None or parent_chain is None:
        return
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        return
    try:
        _recheck_directory_chain(parent_chain, "output parent")
        named = os.lstat(output_path)
        if stat.S_ISLNK(named.st_mode) or _node_identity(named) != directory_identity:
            return
        descriptor = os.open(
            output_path,
            os.O_RDONLY | nofollow | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        return
    try:
        if _node_identity(os.fstat(descriptor)) != directory_identity:
            return
        try:
            names = set(os.listdir(descriptor))
        except OSError:
            return
        if not names.issubset(_ALLOWED_FILES):
            return
        for name in names:
            try:
                os.unlink(name, dir_fd=descriptor)
            except OSError:
                return
    finally:
        os.close(descriptor)
    with contextlib.suppress(OSError):
        os.rmdir(output_path)


def create_bundle(
    source_pyz: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    repository: str,
    target_sha: str,
    package_version: str,
    workflow_definition_sha: str,
    candidate_run_id: int,
    candidate_run_attempt: int,
    merge_gate_check_run_id: int,
    merge_gate_run_id: int,
    merge_gate_job_url: str,
    merge_gate_url: str,
) -> dict[str, Any]:
    """Create and return the exact provenance manifest for a candidate bundle."""

    valid = _validate_arguments(
        repository,
        target_sha,
        package_version,
        workflow_definition_sha,
        candidate_run_id,
        candidate_run_attempt,
        merge_gate_check_run_id,
        merge_gate_run_id,
        merge_gate_job_url,
        merge_gate_url,
    )
    (
        valid_repository,
        valid_target_sha,
        valid_package_version,
        valid_workflow_sha,
        valid_candidate_run_id,
        valid_candidate_run_attempt,
        valid_merge_gate_check_run_id,
        valid_merge_gate_run_id,
        valid_merge_gate_job_url,
        valid_merge_gate_url,
    ) = valid
    source_path = _as_absolute_path(source_pyz, "source pyz")
    output_path = _as_absolute_path(output_dir, "output directory")
    if source_path == output_path:
        raise CandidateBundleError("source pyz and output directory must be different")

    source_descriptor, source_initial, source_parent_chain = _open_source(source_path)
    output_descriptor: int | None = None
    output_identity: tuple[int, int, int] | None = None
    output_parent_chain: list[tuple[str, tuple[int, int, int]]] | None = None
    try:
        output_descriptor, output_identity, output_parent_chain = _open_output_directory(
            output_path
        )
        artifact_sha, artifact_size = _copy_source(
            source_descriptor,
            source_initial,
            source_path,
            source_parent_chain,
            output_descriptor,
        )
        sidecar = f"{artifact_sha}  ici.pyz\n".encode("ascii")
        provenance: dict[str, Any] = {
            "schema": "ici.candidate/v1",
            "channel": "candidate",
            "stable": False,
            "repository": valid_repository,
            "target_sha": valid_target_sha,
            "package_version": valid_package_version,
            "candidate_workflow": CANDIDATE_WORKFLOW,
            "workflow_definition_sha": valid_workflow_sha,
            "candidate_run_id": valid_candidate_run_id,
            "candidate_run_attempt": valid_candidate_run_attempt,
            "merge_gate_check_run_id": valid_merge_gate_check_run_id,
            "merge_gate_run_id": valid_merge_gate_run_id,
            "merge_gate_job_url": valid_merge_gate_job_url,
            "merge_gate_url": valid_merge_gate_url,
            "artifact_file": "ici.pyz",
            "artifact_file_sha256": artifact_sha,
            "artifact_file_size": artifact_size,
            "retention_days": 14,
        }
        if tuple(provenance) != _PROVENANCE_FIELDS:
            raise CandidateBundleError("candidate provenance fields are not exact")
        _write_bundle_file(output_descriptor, "ici.pyz.sha256", sidecar, 0o644)
        _write_bundle_file(
            output_descriptor,
            "candidate-provenance.json",
            _json_bytes(provenance),
            0o644,
        )
        _check_source_unchanged(source_path, source_descriptor, source_initial, source_parent_chain)
        _assert_output_directory(
            output_path, output_descriptor, output_identity, output_parent_chain
        )
        return provenance
    except CandidateBundleError:
        if output_descriptor is not None:
            os.close(output_descriptor)
            output_descriptor = None
        _cleanup_created_output(output_path, output_identity, output_parent_chain)
        raise
    except OSError as exc:
        if output_descriptor is not None:
            os.close(output_descriptor)
            output_descriptor = None
        _cleanup_created_output(output_path, output_identity, output_parent_chain)
        raise CandidateBundleError(f"candidate bundle failed: {exc}") from exc
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        os.close(source_descriptor)


def _cli_id(raw: str) -> int:
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 19:
        raise argparse.ArgumentTypeError("ID must contain at most 19 ASCII decimal digits")
    value = int(raw)
    if value <= 0 or value > MAX_GITHUB_ID:
        raise argparse.ArgumentTypeError("ID must be positive and within GitHub's range")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a candidate artifact bundle")
    create.add_argument("--source-pyz", "--source", dest="source_pyz", required=True, type=Path)
    create.add_argument("--output-dir", "--output", dest="output_dir", required=True, type=Path)
    create.add_argument("--repository", required=True)
    create.add_argument("--target-sha", required=True)
    create.add_argument("--package-version", required=True)
    create.add_argument("--workflow-definition-sha", required=True)
    create.add_argument("--candidate-run-id", required=True, type=_cli_id)
    create.add_argument("--candidate-run-attempt", required=True, type=_cli_id)
    create.add_argument("--merge-gate-check-run-id", required=True, type=_cli_id)
    create.add_argument("--merge-gate-run-id", required=True, type=_cli_id)
    create.add_argument("--merge-gate-job-url", required=True)
    create.add_argument("--merge-gate-url", required=True)
    verify = commands.add_parser("verify", help="verify a candidate artifact bundle")
    verify.add_argument("bundle_dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            provenance = create_bundle(
                args.source_pyz,
                args.output_dir,
                repository=args.repository,
                target_sha=args.target_sha,
                package_version=args.package_version,
                workflow_definition_sha=args.workflow_definition_sha,
                candidate_run_id=args.candidate_run_id,
                candidate_run_attempt=args.candidate_run_attempt,
                merge_gate_check_run_id=args.merge_gate_check_run_id,
                merge_gate_run_id=args.merge_gate_run_id,
                merge_gate_job_url=args.merge_gate_job_url,
                merge_gate_url=args.merge_gate_url,
            )
        else:
            provenance = verify_bundle(args.bundle_dir)
    except CandidateBundleError as exc:
        print(f"candidate bundle failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(provenance, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
