"""Bounded source-package and wheel contract inspection.

This module never imports project code and never extracts or builds a wheel.
It treats both ``pyproject.toml`` and wheel archives as untrusted inputs and
returns native v3 findings so packaging defects have stable rule identities.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import tomli
from packaging.utils import canonicalize_name
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
from ici.engines._python_wheel import (
    MAX_WHEEL_MEMBERS,
    MAX_WHEEL_UNCOMPRESSED_BYTES,
    PythonPackagingError,
    _WheelEvidence,
    inspect_wheel,
)

MAX_PYPROJECT_BYTES = 2 * 1024 * 1024
MAX_WHEEL_GLOBS = 32
MAX_WHEELS = 32
MAX_PACKAGE_SOURCE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_AST_NODES = 200_000
_ENTRYPOINT_RE = re.compile(
    r"^(?P<module>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):"
    r"(?P<attribute>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)$"
)


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


def _summary_target(path: str, label: str, findings: list[Finding]) -> InspectionTarget:
    relevant = [item for item in findings if item.primary_location.path == path]
    if any(item.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL} for item in relevant):
        status = EngineStatus.FAIL
    elif relevant:
        status = EngineStatus.WARN
    else:
        status = EngineStatus.PASS
    return InspectionTarget(
        file_path=path,
        start_line=1,
        target_name=f"Package:{label}",
        status=status,
        message=(
            f"Package inspection found {len(relevant)} issue(s)"
            if relevant
            else "Package contract inspection completed"
        ),
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
                (
                    f"{'console_scripts' if table_name == 'scripts' else 'gui_scripts'}:{name}",
                    value,
                )
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
        payload = _read_bounded_regular(path, MAX_PACKAGE_SOURCE_BYTES)
        tree = ast.parse(payload.decode("utf-8", errors="strict"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError, _ReadError):
        return False
    if sum(1 for _node in ast.walk(tree)) > MAX_PACKAGE_AST_NODES:
        return False
    nodes: list[ast.AST] = list(tree.body)
    parts = attribute.split(".")
    for index, part in enumerate(parts):
        match: ast.AST | None = None
        for node in nodes:
            names: set[str] = set()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
                names.update(item.id for item in node.targets if isinstance(item, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Lambda):
                if isinstance(node.target, ast.Name):
                    names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".", 1)[0])
            if part in names:
                match = node
                break
        if match is None:
            return False
        final = index == len(parts) - 1
        if final:
            return isinstance(
                match,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom),
            ) or (
                isinstance(match, (ast.Assign, ast.AnnAssign))
                and isinstance(match.value, ast.Lambda)
            )
        nodes = list(match.body) if isinstance(match, ast.ClassDef) else []
    return False


def _source_findings(package: _ProjectPackage, policy: PackagingPolicy) -> list[Finding]:
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
    if policy.check_entrypoints:
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
                    f"Entry point {name!r} target {value!r} does not resolve to a callable or imported symbol",
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


def analyze_python_packaging(
    root: Path,
    source_files: list[Path],
    policy: PackagingPolicy,
) -> PackagingAnalysis:
    """Inspect source metadata and configured wheels without executing project code."""

    canonical_root = root.resolve(strict=True)
    package = _project_package(canonical_root, source_files)
    findings = _source_findings(package, policy)
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
        relative = wheel.relative_to(canonical_root).as_posix()
        try:
            wheel_findings, wheel_evidence = inspect_wheel(canonical_root, wheel, package, policy)
        except PythonPackagingError as err:
            findings.append(
                _finding(
                    "ici.package.wheel-invalid",
                    relative,
                    1,
                    f"Wheel could not be inspected safely: {err}",
                    severity=FindingSeverity.HIGH,
                    tool_rule_id="wheel.structure",
                )
            )
            continue
        findings.extend(wheel_findings)
        evidence.append(wheel_evidence)
    inspected_paths = [
        "pyproject.toml",
        *(wheel.relative_to(canonical_root).as_posix() for wheel in wheels),
    ]
    targets = tuple(
        _summary_target(
            path,
            "SourceMetadata" if path == "pyproject.toml" else "Wheel",
            findings,
        )
        for path in inspected_paths
    )
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
            "attempted": len(wheels),
            "checked": len(evidence),
            "invalid": len(wheels) - len(evidence),
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
