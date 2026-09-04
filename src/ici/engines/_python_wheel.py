"""Bounded inspection of Python wheel archives.

Wheel files are untrusted ZIP inputs.  This module validates their structure,
metadata, RECORD integrity, and package contract without importing project
code or extracting archive members.
"""

from __future__ import annotations

import base64
import configparser
import csv
import hashlib
import io
import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from ici.core.findings import finding_fingerprint
from ici.core.models import (
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    SourceLocation,
)

MAX_WHEEL_METADATA_BYTES = 1024 * 1024
MAX_WHEEL_MEMBERS = 8192
MAX_WHEEL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")
_RECORD_HASH_RE = re.compile(r"^(sha256|sha384|sha512)=([A-Za-z0-9_-]+)$")


class PythonPackagingError(ValueError):
    """Raised when packaging evidence cannot be inspected safely."""


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    """Preserve entry-point names without mutating ConfigParser methods."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


class _WheelPackage(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def package_files(self) -> tuple[str, ...]: ...

    @property
    def entrypoints(self) -> tuple[tuple[str, str], ...]: ...


class _WheelPolicy(Protocol):
    @property
    def wheel_policy(self) -> str: ...

    @property
    def check_entrypoints(self) -> bool: ...

    @property
    def check_package_files(self) -> bool: ...

    @property
    def max_wheel_members(self) -> int: ...

    @property
    def max_wheel_uncompressed_bytes(self) -> int: ...


@dataclass(frozen=True)
class _WheelEvidence:
    path: str
    members: int
    uncompressed_bytes: int
    pure: bool
    native_members: tuple[str, ...]
    direct_url: bool
    build_details: bool


@dataclass(frozen=True)
class _WheelStructure:
    """Validated archive members and the wheel's required metadata files."""

    infos: list[zipfile.ZipInfo]
    names: list[str]
    total_bytes: int
    dist_info_directory: str
    wheel_info: zipfile.ZipInfo
    metadata_info: zipfile.ZipInfo
    record_info: zipfile.ZipInfo


def _finding(
    rule_id: str,
    path: str,
    line: int,
    message: str,
    *,
    severity: FindingSeverity,
    tool_rule_id: str,
) -> Finding:
    location = SourceLocation(path=path, start_line=max(1, line))
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.COMPATIBILITY,
        severity=severity,
        confidence=FindingConfidence.EXACT,
        fingerprint=finding_fingerprint(rule_id, location),
        primary_location=location,
        message=message,
        explanation="The declared package contract does not match the inspected source or wheel.",
        remediation="Correct the package metadata or rebuild the wheel from the intended source tree.",
        tool_rule_id=tool_rule_id,
        tool_name="ici packaging inspector",
    )


def _safe_member_name(value: str) -> bool:
    """Return whether a wheel member uses one unambiguous POSIX spelling.

    ZIP archives permit spellings such as ``pkg//module.py`` and
    ``pkg/./module.py`` even though extraction APIs usually normalize them to
    the same filesystem path as ``pkg/module.py``.  Treating those spellings
    as distinct lets an archive contain two logical files with different
    RECORD rows.  A single trailing slash is retained only for an explicit
    directory member; all other members must be regular canonical paths.
    """

    if not value or "\\" in value:
        return False
    is_directory = value.endswith("/")
    path_text = value[:-1] if is_directory else value
    if not path_text:
        return False
    path = PurePosixPath(path_text)
    if path.is_absolute() or ".." in path.parts:
        return False
    canonical = path.as_posix()
    if not canonical or canonical == ".":
        return False
    return value == (canonical + "/" if is_directory else canonical)


def _dist_info_directories(names: list[str]) -> set[str]:
    """Return every archive path containing a ``.dist-info`` directory."""

    directories: set[str] = set()
    for name in names:
        parts = name.split("/")
        for index, component in enumerate(parts):
            if component.endswith(".dist-info"):
                directories.add("/".join(parts[: index + 1]))
    return directories


def _dist_info_identity_matches(
    directory: str, filename_name: str, filename_version: Version
) -> bool:
    """Check a dist-info directory against normalized wheel filename identity."""

    if "/" in directory or not directory.endswith(".dist-info"):
        return False
    stem = directory[: -len(".dist-info")]
    if "-" not in stem:
        return False
    directory_name, directory_version = stem.rsplit("-", 1)
    try:
        parsed_version = Version(directory_version)
    except InvalidVersion:
        return False
    return (
        canonicalize_name(directory_name) == canonicalize_name(filename_name)
        and parsed_version == filename_version
    )


def _duplicate_headers(headers: dict[str, list[str]], names: tuple[str, ...]) -> tuple[str, ...]:
    """Return duplicate singleton header names in deterministic order."""

    return tuple(sorted(name for name in names if len(headers.get(name, [])) > 1))


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_WHEEL_METADATA_BYTES:
        raise PythonPackagingError(f"wheel metadata member is too large: {info.filename}")
    try:
        payload = archive.read(info)
    except (OSError, RuntimeError, zipfile.BadZipFile) as err:
        raise PythonPackagingError(f"wheel member could not be read: {info.filename}") from err
    if len(payload) != info.file_size:
        raise PythonPackagingError(f"wheel member size changed while reading: {info.filename}")
    return payload


def _record_rows(payload: bytes, member: str) -> dict[str, tuple[str, str]]:
    try:
        text = payload.decode("utf-8", errors="strict")
        rows = list(csv.reader(io.StringIO(text), strict=True))
    except (UnicodeDecodeError, csv.Error) as err:
        raise PythonPackagingError(f"wheel RECORD could not be parsed: {member}") from err
    result: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or not _safe_member_name(row[0]):
            raise PythonPackagingError(f"wheel RECORD contains a malformed row: {member}")
        if row[0] in result:
            raise PythonPackagingError(f"wheel RECORD contains a duplicate path: {member}")
        result[row[0]] = (row[1], row[2])
    return result


def _member_digest(archive: zipfile.ZipFile, info: zipfile.ZipInfo, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    size = 0
    try:
        with archive.open(info) as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > info.file_size:
                    raise PythonPackagingError(
                        f"wheel member expanded beyond its declared size: {info.filename}"
                    )
                digest.update(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile) as err:
        raise PythonPackagingError(f"wheel member could not be hashed: {info.filename}") from err
    if size != info.file_size:
        raise PythonPackagingError(f"wheel member size changed while hashing: {info.filename}")
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")


def _record_violations(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    record_info: zipfile.ZipInfo,
    rows: dict[str, tuple[str, str]],
) -> list[str]:
    names = {info.filename for info in infos}
    signatures = {f"{record_info.filename}.jws", f"{record_info.filename}.p7s"}
    violations: list[str] = []
    missing = sorted(names - signatures - set(rows))
    extra = sorted(set(rows) - names)
    if missing:
        violations.append(f"omits {len(missing)} member(s)")
    if extra:
        violations.append(f"references {len(extra)} absent member(s)")
    for info in infos:
        if info.filename in signatures:
            continue
        record = rows.get(info.filename)
        if record is None:
            continue
        hash_value, size_value = record
        if info.filename == record_info.filename:
            if hash_value or size_value:
                violations.append("hashes or sizes RECORD itself")
            continue
        match = _RECORD_HASH_RE.fullmatch(hash_value)
        try:
            declared_size = int(size_value)
        except ValueError:
            declared_size = -1
        if match is None or declared_size != info.file_size:
            violations.append(f"has invalid hash/size metadata for {info.filename}")
            continue
        algorithm, declared_digest = match.groups()
        if _member_digest(archive, info, algorithm) != declared_digest:
            violations.append(f"has a digest mismatch for {info.filename}")
    return violations


def _archive_entrypoints(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo
) -> tuple[tuple[str, str], ...]:
    try:
        text = _read_member(archive, info).decode("utf-8", errors="strict")
        parser = _CaseSensitiveConfigParser(interpolation=None, strict=True)
        parser.read_string(text)
    except (UnicodeDecodeError, configparser.Error) as err:
        raise PythonPackagingError(
            f"wheel entry_points.txt could not be parsed: {info.filename}"
        ) from err
    return tuple(
        sorted(
            (f"{section}:{name}", value.strip())
            for section in parser.sections()
            for name, value in parser.items(section)
        )
    )


def _headers(payload: bytes, member: str) -> dict[str, list[str]]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as err:
        raise PythonPackagingError(f"wheel metadata is not UTF-8: {member}") from err
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line:
            break
        if line[0].isspace() or ":" not in line:
            continue
        name, value = line.split(":", 1)
        result.setdefault(name.casefold(), []).append(value.strip())
    return result


def _portable_member_name(name: str) -> str:
    """Return the normalized collision key for one archive member."""

    if name.endswith("/"):
        name = name[:-1]
    return unicodedata.normalize("NFC", name).casefold()


def _validate_wheel_members(
    infos: list[zipfile.ZipInfo], relative: str, policy: _WheelPolicy
) -> tuple[list[str], int]:
    """Validate bounded ZIP member structure and return names plus total size."""

    if len(infos) > policy.max_wheel_members:
        raise PythonPackagingError(f"wheel member count exceeds the configured bound: {relative}")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise PythonPackagingError(f"wheel contains duplicate member names: {relative}")
    portable_names = [_portable_member_name(name) for name in names]
    if len(portable_names) != len(set(portable_names)):
        raise PythonPackagingError(
            f"wheel contains names that collide on portable filesystems: {relative}"
        )
    if any(not _safe_member_name(name) for name in names):
        raise PythonPackagingError(f"wheel contains an unsafe member path: {relative}")
    for info in infos:
        if info.is_dir() and info.file_size:
            raise PythonPackagingError(f"wheel contains a non-empty directory member: {relative}")
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        if stat.S_ISLNK(mode) or (file_type and not stat.S_ISREG(mode) and not stat.S_ISDIR(mode)):
            raise PythonPackagingError(f"wheel contains a symlink or special member: {relative}")
    if any(info.flag_bits & 0x1 for info in infos):
        raise PythonPackagingError(f"encrypted wheel members are unsupported: {relative}")
    total_bytes = sum(info.file_size for info in infos)
    if total_bytes > policy.max_wheel_uncompressed_bytes:
        raise PythonPackagingError(
            f"wheel uncompressed size exceeds the configured bound: {relative}"
        )
    return names, total_bytes


def _locate_wheel_structure(
    infos: list[zipfile.ZipInfo],
    names: list[str],
    total_bytes: int,
    relative: str,
    filename_name: str,
    filename_version: Version,
) -> tuple[_WheelStructure, bool]:
    """Locate required wheel metadata and validate its directory identity."""

    wheel_infos = [info for info in infos if info.filename.endswith(".dist-info/WHEEL")]
    metadata_infos = [info for info in infos if info.filename.endswith(".dist-info/METADATA")]
    record_infos = [info for info in infos if info.filename.endswith(".dist-info/RECORD")]
    if len(wheel_infos) != 1 or len(metadata_infos) != 1 or len(record_infos) != 1:
        raise PythonPackagingError(
            f"wheel must contain exactly one WHEEL, METADATA, and RECORD file: {relative}"
        )
    dist_info_directories = _dist_info_directories(names)
    if len(dist_info_directories) != 1:
        raise PythonPackagingError(
            f"wheel must contain exactly one .dist-info directory: {relative}"
        )
    dist_info_directory = next(iter(dist_info_directories))
    if "/" in dist_info_directory:
        raise PythonPackagingError(
            f"wheel .dist-info directory must be at the archive root: {relative}"
        )
    identity_matches = _dist_info_identity_matches(
        dist_info_directory, filename_name, filename_version
    )
    if any(
        info.filename != f"{dist_info_directory}/{required_name}"
        for info, required_name in (
            (wheel_infos[0], "WHEEL"),
            (metadata_infos[0], "METADATA"),
            (record_infos[0], "RECORD"),
        )
    ):
        raise PythonPackagingError(
            f"wheel must contain exactly one WHEEL, METADATA, and RECORD file: {relative}"
        )
    return (
        _WheelStructure(
            infos=infos,
            names=names,
            total_bytes=total_bytes,
            dist_info_directory=dist_info_directory,
            wheel_info=wheel_infos[0],
            metadata_info=metadata_infos[0],
            record_info=record_infos[0],
        ),
        identity_matches,
    )


def _read_wheel_contents(
    archive: zipfile.ZipFile, structure: _WheelStructure
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, tuple[str, str]]]:
    """Read required metadata and reject duplicate singleton headers."""

    wheel_headers = _headers(
        _read_member(archive, structure.wheel_info), structure.wheel_info.filename
    )
    metadata_headers = _headers(
        _read_member(archive, structure.metadata_info), structure.metadata_info.filename
    )
    duplicate_wheel_headers = _duplicate_headers(
        wheel_headers, ("wheel-version", "generator", "root-is-purelib")
    )
    duplicate_metadata_headers = _duplicate_headers(metadata_headers, ("name", "version"))
    duplicate_headers = duplicate_metadata_headers + duplicate_wheel_headers
    if duplicate_headers:
        raise PythonPackagingError(
            "wheel contains duplicate singleton headers: " + ", ".join(duplicate_headers)
        )
    record_rows = _record_rows(
        _read_member(archive, structure.record_info), structure.record_info.filename
    )
    return wheel_headers, metadata_headers, record_rows


def _append_record_findings(
    findings: list[Finding],
    archive: zipfile.ZipFile,
    structure: _WheelStructure,
    record_rows: dict[str, tuple[str, str]],
    relative: str,
) -> None:
    """Append one native finding when RECORD integrity has violations."""

    record_violations = _record_violations(
        archive, structure.infos, structure.record_info, record_rows
    )
    if not record_violations:
        return
    findings.append(
        _finding(
            "ici.package.wheel-record-integrity",
            relative,
            1,
            f"Wheel RECORD integrity failed: {'; '.join(record_violations[:3])}",
            severity=FindingSeverity.HIGH,
            tool_rule_id="wheel.record",
        )
    )


def _wheel_metadata_matches(
    package: _WheelPackage,
    filename_name: str,
    filename_version: Version,
    metadata_name: str,
    metadata_version: str,
) -> bool:
    """Return whether filename and METADATA identity match the source project."""

    if not metadata_name or not metadata_version:
        return False
    if canonicalize_name(filename_name) != canonicalize_name(package.name):
        return False
    if package.version and (
        filename_version != Version(package.version) or metadata_version != package.version
    ):
        return False
    return canonicalize_name(metadata_name) == canonicalize_name(package.name)


def _append_metadata_findings(
    findings: list[Finding],
    package: _WheelPackage,
    filename_name: str,
    filename_version: Version,
    metadata_headers: dict[str, list[str]],
    relative: str,
) -> None:
    """Append a native finding when wheel identity disagrees with pyproject."""

    metadata_name = next(iter(metadata_headers.get("name", [])), "")
    metadata_version = next(iter(metadata_headers.get("version", [])), "")
    if _wheel_metadata_matches(
        package, filename_name, filename_version, metadata_name, metadata_version
    ):
        return
    findings.append(
        _finding(
            "ici.package.wheel-metadata-mismatch",
            relative,
            1,
            "Wheel filename/METADATA identity does not match pyproject.toml",
            severity=FindingSeverity.HIGH,
            tool_rule_id="wheel.metadata-identity",
        )
    )


def _append_tag_findings(
    findings: list[Finding],
    wheel_headers: dict[str, list[str]],
    filename_tags: frozenset[Any],
    relative: str,
) -> None:
    """Append a native finding when WHEEL tags disagree with its filename."""

    declared_tags = set(wheel_headers.get("tag", []))
    filename_tag_strings = {str(tag) for tag in filename_tags}
    if declared_tags and declared_tags == filename_tag_strings:
        return
    findings.append(
        _finding(
            "ici.package.wheel-tag-mismatch",
            relative,
            1,
            "Wheel filename tags do not exactly match WHEEL Tag headers",
            severity=FindingSeverity.HIGH,
            tool_rule_id="wheel.tags",
        )
    )


def _wheel_purity(
    names: list[str], wheel_headers: dict[str, list[str]], filename_tags: frozenset[Any]
) -> tuple[tuple[str, ...], bool]:
    """Return native members and the aggregate pure-wheel classification."""

    native = tuple(sorted(name for name in names if name.casefold().endswith(_NATIVE_SUFFIXES)))
    root_pure = next(iter(wheel_headers.get("root-is-purelib", [])), "").casefold() == "true"
    pure_tags = bool(filename_tags) and all(
        tag.abi == "none" and tag.platform == "any" for tag in filename_tags
    )
    return native, root_pure and pure_tags and not native


def _append_pure_policy_finding(
    findings: list[Finding], policy: _WheelPolicy, pure: bool, relative: str
) -> None:
    """Append a native finding when a configured pure policy is violated."""

    if policy.wheel_policy != "pure" or pure:
        return
    findings.append(
        _finding(
            "ici.package.native-wheel-forbidden",
            relative,
            1,
            "Wheel violates the configured pure-Python policy",
            severity=FindingSeverity.HIGH,
            tool_rule_id="wheel.pure-policy",
        )
    )


def _append_package_file_findings(
    findings: list[Finding],
    package: _WheelPackage,
    names: list[str],
    policy: _WheelPolicy,
    relative: str,
) -> None:
    """Append a native finding when discovered Python files are absent."""

    if not policy.check_package_files:
        return
    missing = [name for name in package.package_files if name not in names]
    if not missing:
        return
    findings.append(
        _finding(
            "ici.package.package-files-missing",
            relative,
            1,
            f"Wheel omits {len(missing)} discovered Python package file(s)",
            severity=FindingSeverity.HIGH,
            tool_rule_id="wheel.package-files",
        )
    )


def _append_entrypoint_findings(
    findings: list[Finding],
    archive: zipfile.ZipFile,
    structure: _WheelStructure,
    package: _WheelPackage,
    policy: _WheelPolicy,
    relative: str,
) -> None:
    """Append a native finding when wheel entry points are absent or differ."""

    if not policy.check_entrypoints or not package.entrypoints:
        return
    entrypoint = next(
        (
            info
            for info in structure.infos
            if info.filename == f"{structure.dist_info_directory}/entry_points.txt"
        ),
        None,
    )
    if entrypoint is None:
        findings.append(
            _finding(
                "ici.package.wheel-entrypoints-missing",
                relative,
                1,
                "Wheel omits entry_points.txt for declared project entry points",
                severity=FindingSeverity.HIGH,
                tool_rule_id="wheel.entry-points",
            )
        )
        return
    if _archive_entrypoints(archive, entrypoint) == package.entrypoints:
        return
    findings.append(
        _finding(
            "ici.package.wheel-entrypoints-mismatch",
            relative,
            1,
            "Wheel entry_points.txt does not match declared project entry points",
            severity=FindingSeverity.HIGH,
            tool_rule_id="wheel.entry-points",
        )
    )


def inspect_wheel(
    root: Path,
    path: Path,
    package: _WheelPackage,
    policy: _WheelPolicy,
) -> tuple[list[Finding], _WheelEvidence]:
    """Inspect one wheel and compose findings from focused validation helpers."""

    relative = path.relative_to(root).as_posix()
    try:
        filename_name, filename_version, _build, filename_tags = parse_wheel_filename(path.name)
    except InvalidWheelFilename as err:
        raise PythonPackagingError(f"invalid wheel filename: {relative}") from err
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as err:
        raise PythonPackagingError(f"wheel is not a readable ZIP archive: {relative}") from err
    findings: list[Finding] = []
    with archive:
        infos = archive.infolist()
        names, total_bytes = _validate_wheel_members(infos, relative, policy)
        structure, dist_info_matches = _locate_wheel_structure(
            infos,
            names,
            total_bytes,
            relative,
            str(filename_name),
            filename_version,
        )
        if not dist_info_matches:
            findings.append(
                _finding(
                    "ici.package.wheel-metadata-mismatch",
                    relative,
                    1,
                    "Wheel .dist-info directory identity does not match its filename",
                    severity=FindingSeverity.HIGH,
                    tool_rule_id="wheel.dist-info-identity",
                )
            )
        wheel_headers, metadata_headers, record_rows = _read_wheel_contents(archive, structure)
        _append_record_findings(findings, archive, structure, record_rows, relative)
        _append_metadata_findings(
            findings,
            package,
            str(filename_name),
            filename_version,
            metadata_headers,
            relative,
        )
        _append_tag_findings(findings, wheel_headers, filename_tags, relative)
        native, pure = _wheel_purity(names, wheel_headers, filename_tags)
        _append_pure_policy_finding(findings, policy, pure, relative)
        _append_package_file_findings(findings, package, names, policy, relative)
        _append_entrypoint_findings(findings, archive, structure, package, policy, relative)
        evidence = _WheelEvidence(
            path=relative,
            members=len(infos),
            uncompressed_bytes=total_bytes,
            pure=pure,
            native_members=native,
            direct_url=any(name.endswith(".dist-info/direct_url.json") for name in names),
            build_details=any(name.endswith(".dist-info/build-details.json") for name in names),
        )
    return findings, evidence
