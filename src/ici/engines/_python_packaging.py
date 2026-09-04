"""Bounded source-package and wheel contract inspection.

This module never imports project code and never extracts or builds a wheel.
It treats both ``pyproject.toml`` and wheel archives as untrusted inputs and
returns native v3 findings so packaging defects have stable rule identities.
"""

from __future__ import annotations

import ast
import csv
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tomli
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core.findings import finding_fingerprint
from ici.core.models import (
    EngineStatus,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
    InspectionTarget,
    SourceLocation,
)

MAX_PYPROJECT_BYTES = 2 * 1024 * 1024
MAX_WHEEL_METADATA_BYTES = 1024 * 1024
MAX_WHEEL_GLOBS = 32
MAX_WHEELS = 32
MAX_WHEEL_MEMBERS = 8192
MAX_WHEEL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_ENTRYPOINT_RE = re.compile(
    r"^(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):"
    r"(?P<attribute>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)$"
)
_NATIVE_SUFFIXES = (".so", ".pyd", ".dll", ".dylib")


class PythonPackagingError(ValueError):
    """Raised when packaging evidence cannot be inspected safely."""


@dataclass(frozen=True)
class PackagingPolicy:
    wheel_globs: tuple[str, ...] = ()
    wheel_required: bool = False
    wheel_policy: str = "allow-native"
    check_entrypoints: bool = True
    check_package_files: bool = True
    max_wheels: int = MAX_WHEELS
    max_wheel_members: int = MAX_WHEEL_MEMBERS
    max_wheel_uncompressed_bytes: int = MAX_WHEEL_UNCOMPRESSED_BYTES


@dataclass(frozen=True)
class PackagingAnalysis:
    targets: tuple[InspectionTarget, ...]
    findings: tuple[Finding, ...]
    failures: int
    warnings: int
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _ProjectPackage:
    name: str
    version: str
    modules: dict[str, Path]
    package_files: tuple[str, ...]
    entrypoints: tuple[tuple[str, str], ...]
    pyproject_text: str


@dataclass(frozen=True)
class _WheelEvidence:
    path: str
    members: int
    uncompressed_bytes: int
    pure: bool
    native_members: tuple[str, ...]
    direct_url: bool
    build_details: bool


def _line_for(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return 1


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


def _target(finding: Finding) -> InspectionTarget:
    return InspectionTarget(
        file_path=finding.primary_location.path,
        start_line=finding.primary_location.start_line,
        target_name=f"Package:{finding.tool_rule_id}",
        status=(
            EngineStatus.FAIL
            if finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
            else EngineStatus.WARN
        ),
        message=finding.message,
    )


def _read_pyproject(root: Path) -> tuple[dict[str, Any], str]:
    path = root / "pyproject.toml"
    try:
        payload = _read_bounded_regular(path, MAX_PYPROJECT_BYTES, containment_root=root)
        text = payload.decode("utf-8", errors="strict")
        document = tomli.loads(text)
    except (FileNotFoundError, UnicodeDecodeError, _ReadError, tomli.TOMLDecodeError) as err:
        raise PythonPackagingError("pyproject.toml could not be read and parsed safely") from err
    if not isinstance(document, dict):
        raise PythonPackagingError("pyproject.toml must contain a table")
    return document, text


def _module_index(root: Path, source_files: list[Path]) -> tuple[dict[str, Path], tuple[str, ...]]:
    modules: dict[str, Path] = {}
    package_files: set[str] = set()
    for source in sorted(source_files):
        if source.suffix not in {".py", ".pyi"}:
            continue
        try:
            relative = source.resolve(strict=True).relative_to(root)
        except (OSError, RuntimeError, ValueError) as err:
            raise PythonPackagingError(
                f"Python package source escapes the project: {source}"
            ) from err
        parts = list(relative.parts)
        if parts and parts[0] in {"src", "lib", "python", "packages"}:
            parts = parts[1:]
        if not parts:
            continue
        archive_path = PurePosixPath(*parts).as_posix()
        package_files.add(archive_path)
        module_parts = list(PurePosixPath(*parts).with_suffix("").parts)
        if module_parts[-1] == "__init__":
            module_parts.pop()
        if module_parts and all(part.isidentifier() for part in module_parts):
            modules[".".join(module_parts)] = source
    return modules, tuple(sorted(package_files))


def _entrypoints(project: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for table_name in ("scripts", "gui-scripts"):
        table = project.get(table_name, {})
        if isinstance(table, dict):
            result.extend(
                (str(name), value)
                for name, value in table.items()
                if isinstance(name, str) and isinstance(value, str)
            )
    groups = project.get("entry-points", {})
    if isinstance(groups, dict):
        for group, table in groups.items():
            if not isinstance(group, str) or not isinstance(table, dict):
                continue
            result.extend(
                (f"{group}:{name}", value)
                for name, value in table.items()
                if isinstance(name, str) and isinstance(value, str)
            )
    return tuple(sorted(result))


def _project_package(root: Path, source_files: list[Path]) -> _ProjectPackage:
    document, text = _read_pyproject(root)
    project = document.get("project")
    if not isinstance(project, dict):
        raise PythonPackagingError("pyproject.toml [project] must be a table")
    name = project.get("name")
    version = project.get("version", "")
    dynamic = project.get("dynamic", [])
    if not isinstance(name, str) or not name.strip():
        raise PythonPackagingError("project.name must be a non-empty string")
    if not isinstance(version, str):
        raise PythonPackagingError("project.version must be a string")
    if not version and not (isinstance(dynamic, list) and "version" in dynamic):
        raise PythonPackagingError("project.version must be declared or listed as dynamic")
    if version:
        try:
            Version(version)
        except InvalidVersion as err:
            raise PythonPackagingError(f"project.version is invalid: {version!r}") from err
    modules, package_files = _module_index(root, source_files)
    return _ProjectPackage(
        name=name,
        version=version,
        modules=modules,
        package_files=package_files,
        entrypoints=_entrypoints(project),
        pyproject_text=text,
    )


def _module_attribute_exists(path: Path, attribute: str) -> bool:
    try:
        payload = path.read_text(encoding="utf-8")
        tree = ast.parse(payload, filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return False
    nodes: list[ast.AST] = list(tree.body)
    for part in attribute.split("."):
        match: ast.AST | None = None
        for node in nodes:
            names: set[str] = set()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                values = node.targets if isinstance(node, ast.Assign) else [node.target]
                names.update(item.id for item in values if isinstance(item, ast.Name))
            if part in names:
                match = node
                break
        if match is None:
            return False
        nodes = list(match.body) if isinstance(match, ast.ClassDef) else []
    return True


def _source_findings(package: _ProjectPackage) -> list[Finding]:
    findings: list[Finding] = []
    top_level = {name.split(".", 1)[0] for name in package.modules}
    expected = canonicalize_name(package.name).replace("-", "_")
    if package.modules and expected not in top_level and len(top_level) == 1:
        actual = next(iter(top_level))
        findings.append(
            _finding(
                "ici.package.import-distribution-mismatch",
                "pyproject.toml",
                _line_for(package.pyproject_text, "name"),
                f"Distribution {package.name!r} exposes import package {actual!r}",
                severity=FindingSeverity.MEDIUM,
                tool_rule_id="import-distribution-name",
            )
        )
    for name, value in package.entrypoints:
        match = _ENTRYPOINT_RE.fullmatch(value.strip())
        module = match.group("module") if match else ""
        attribute = match.group("attribute") if match else ""
        source = package.modules.get(module)
        if source is not None and _module_attribute_exists(source, attribute):
            continue
        findings.append(
            _finding(
                "ici.package.entrypoint-missing",
                "pyproject.toml",
                _line_for(package.pyproject_text, value),
                f"Entry point {name!r} target {value!r} does not resolve to a declared callable",
                severity=FindingSeverity.HIGH,
                tool_rule_id="entrypoint-target",
            )
        )
    if not package.modules:
        findings.append(
            _finding(
                "ici.package.discovery-empty",
                "pyproject.toml",
                1,
                "Project metadata is present but no Python package modules were discovered",
                severity=FindingSeverity.HIGH,
                tool_rule_id="package-discovery",
            )
        )
    return findings


def _wheel_paths(root: Path, policy: PackagingPolicy) -> list[Path]:
    found: set[Path] = set()
    for pattern in policy.wheel_globs:
        candidate = PurePosixPath(pattern)
        if candidate.is_absolute() or ".." in candidate.parts or "\\" in pattern:
            raise PythonPackagingError(
                f"wheel glob must be project-relative and contained: {pattern!r}"
            )
        try:
            matches = root.glob(pattern)
            for path in matches:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                if path.is_symlink() or not resolved.is_file():
                    raise PythonPackagingError(
                        f"wheel candidate is not a regular contained file: {path}"
                    )
                if resolved.suffix == ".whl":
                    found.add(resolved)
                if len(found) > policy.max_wheels:
                    raise PythonPackagingError("wheel match count exceeds the configured bound")
        except (OSError, RuntimeError, ValueError) as err:
            if isinstance(err, PythonPackagingError):
                raise
            raise PythonPackagingError(f"wheel glob could not be evaluated: {pattern!r}") from err
    return sorted(found)


def _safe_member_name(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


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


def _headers(payload: bytes, member: str) -> dict[str, list[str]]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as err:
        raise PythonPackagingError(f"wheel metadata is not UTF-8: {member}") from err
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        name, value = line.split(":", 1)
        result.setdefault(name.casefold(), []).append(value.strip())
    return result


def _wheel_findings(
    root: Path,
    path: Path,
    package: _ProjectPackage,
    policy: PackagingPolicy,
) -> tuple[list[Finding], _WheelEvidence]:
    relative = path.relative_to(root).as_posix()
    findings: list[Finding] = []
    try:
        filename_name, filename_version, _build, filename_tags = parse_wheel_filename(path.name)
    except InvalidWheelFilename as err:
        raise PythonPackagingError(f"invalid wheel filename: {relative}") from err
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as err:
        raise PythonPackagingError(f"wheel is not a readable ZIP archive: {relative}") from err
    with archive:
        infos = archive.infolist()
        if len(infos) > policy.max_wheel_members:
            raise PythonPackagingError(
                f"wheel member count exceeds the configured bound: {relative}"
            )
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise PythonPackagingError(f"wheel contains duplicate member names: {relative}")
        if any(not _safe_member_name(name) for name in names):
            raise PythonPackagingError(f"wheel contains an unsafe member path: {relative}")
        if any(info.flag_bits & 0x1 for info in infos):
            raise PythonPackagingError(f"encrypted wheel members are unsupported: {relative}")
        total_bytes = sum(info.file_size for info in infos)
        if total_bytes > policy.max_wheel_uncompressed_bytes:
            raise PythonPackagingError(
                f"wheel uncompressed size exceeds the configured bound: {relative}"
            )
        wheel_infos = [info for info in infos if info.filename.endswith(".dist-info/WHEEL")]
        metadata_infos = [info for info in infos if info.filename.endswith(".dist-info/METADATA")]
        record_infos = [info for info in infos if info.filename.endswith(".dist-info/RECORD")]
        if len(wheel_infos) != 1 or len(metadata_infos) != 1 or len(record_infos) != 1:
            raise PythonPackagingError(
                f"wheel must contain exactly one WHEEL, METADATA, and RECORD file: {relative}"
            )
        wheel_headers = _headers(_read_member(archive, wheel_infos[0]), wheel_infos[0].filename)
        metadata_headers = _headers(
            _read_member(archive, metadata_infos[0]), metadata_infos[0].filename
        )
        try:
            record_text = _read_member(archive, record_infos[0]).decode("utf-8", errors="strict")
            record_names = {row[0] for row in csv.reader(io.StringIO(record_text)) if row}
        except (UnicodeDecodeError, csv.Error) as err:
            raise PythonPackagingError(f"wheel RECORD could not be parsed: {relative}") from err
        missing_record = sorted(set(names) - record_names)
        if missing_record:
            findings.append(
                _finding(
                    "ici.package.wheel-record-incomplete",
                    relative,
                    1,
                    f"Wheel RECORD omits {len(missing_record)} member(s)",
                    severity=FindingSeverity.HIGH,
                    tool_rule_id="wheel.record",
                )
            )
        metadata_name = next(iter(metadata_headers.get("name", [])), "")
        metadata_version = next(iter(metadata_headers.get("version", [])), "")
        if (
            canonicalize_name(str(filename_name)) != canonicalize_name(package.name)
            or (
                package.version
                and (
                    filename_version != Version(package.version)
                    or metadata_version != package.version
                )
            )
            or (
                metadata_name
                and canonicalize_name(metadata_name) != canonicalize_name(package.name)
            )
        ):
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
        declared_tags = set(wheel_headers.get("tag", []))
        filename_tag_strings = {str(tag) for tag in filename_tags}
        if not declared_tags or declared_tags != filename_tag_strings:
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
        native = tuple(sorted(name for name in names if name.casefold().endswith(_NATIVE_SUFFIXES)))
        root_pure = next(iter(wheel_headers.get("root-is-purelib", [])), "").casefold() == "true"
        pure_tags = bool(filename_tags) and all(
            tag.abi == "none" and tag.platform == "any" for tag in filename_tags
        )
        pure = root_pure and pure_tags and not native
        if policy.wheel_policy == "pure" and not pure:
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
        if policy.check_package_files:
            missing = [name for name in package.package_files if name not in names]
            if missing:
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
        entrypoint_files = [
            info for info in infos if info.filename.endswith(".dist-info/entry_points.txt")
        ]
        if policy.check_entrypoints and package.entrypoints and len(entrypoint_files) != 1:
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


def analyze_python_packaging(
    root: Path,
    source_files: list[Path],
    policy: PackagingPolicy,
) -> PackagingAnalysis:
    """Inspect source metadata and configured wheels without executing project code."""

    canonical_root = root.resolve(strict=True)
    package = _project_package(canonical_root, source_files)
    findings = _source_findings(package) if policy.check_entrypoints else []
    wheels = _wheel_paths(canonical_root, policy)
    if policy.wheel_globs and not wheels:
        if policy.wheel_required:
            raise PythonPackagingError("configured wheel globs matched no wheel artifacts")
        findings.append(
            _finding(
                "ici.package.wheel-missing",
                "pyproject.toml",
                1,
                "Configured wheel globs matched no wheel artifacts",
                severity=FindingSeverity.MEDIUM,
                tool_rule_id="wheel.discovery",
            )
        )
    evidence: list[_WheelEvidence] = []
    for wheel in wheels:
        wheel_findings, wheel_evidence = _wheel_findings(canonical_root, wheel, package, policy)
        findings.extend(wheel_findings)
        evidence.append(wheel_evidence)
    targets = tuple(_target(finding) for finding in findings)
    failures = sum(
        item.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL} for item in findings
    )
    warnings = len(findings) - failures
    return PackagingAnalysis(
        targets=targets,
        findings=tuple(findings),
        failures=failures,
        warnings=warnings,
        metadata={
            "state": "MEASURED",
            "policy": policy.wheel_policy,
            "requested": list(policy.wheel_globs),
            "checked": len(evidence),
            "members": sum(item.members for item in evidence),
            "uncompressed_bytes": sum(item.uncompressed_bytes for item in evidence),
            "pure": bool(evidence) and all(item.pure for item in evidence),
            "wheels": [
                {
                    "path": item.path,
                    "members": item.members,
                    "uncompressed_bytes": item.uncompressed_bytes,
                    "pure": item.pure,
                    "native_members": list(item.native_members),
                    "direct_url": item.direct_url,
                    "build_details": item.build_details,
                }
                for item in evidence
            ],
            "source_modules": len(package.modules),
            "source_package_files": len(package.package_files),
            "entrypoints": len(package.entrypoints),
            "findings": [item.rule_id for item in findings],
        },
    )
