"""Identity and source-digest helpers for the analysis-result cache."""

from __future__ import annotations

import hashlib
import inspect
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ici import __version__
from ici.core.context import AnalysisContext, BuildVariant, ProjectModel, canonical_digest
from ici.core.models import EngineResult, EngineStatus, EvidenceState

if TYPE_CHECKING:
    from ici.core.pipeline import EngineDescriptor

CACHE_SCHEMA_VERSION = "ici.analysis-cache/v1"
CACHE_KEY_VERSION = "ici.analysis-cache-key/v3"
COMPILATION_IDENTITY_VERSION = "ici.compilation-identity/v2"

_DIGEST_PREFIX = "sha256:"
_EMPTY_COMPILATION_DIGEST = canonical_digest(
    {
        "version": COMPILATION_IDENTITY_VERSION,
        "database_path": None,
        "database_digest": "",
        "origin": "",
        "generator": "",
        "unity_build": None,
        "units": [],
        "diagnostics": [],
    }
)
_SOURCE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cxx",
        ".h",
        ".hh",
        ".hpp",
        ".hxx",
        ".json",
        ".md",
        ".py",
        ".pyi",
        ".sh",
        ".csh",
        ".toml",
        ".yaml",
        ".yml",
    }
)
_INPUT_SUFFIXES = frozenset({".cmake", ".cfg", ".ini", ".pri", ".pro", ".toml"})
_INPUT_NAMES = frozenset(
    {
        ".ruff.toml",
        "CMakeLists.txt",
        "CMakePresets.json",
        "GNUmakefile",
        "Makefile",
        "ici.toml",
        "makefile",
        "mypy.ini",
        "pdm.lock",
        "poetry.lock",
        "pyproject.toml",
        "pytest.ini",
        "ruff.toml",
        "setup.cfg",
        "setup.py",
        "tox.ini",
        "uv.lock",
    }
)
_GENERATED_REPORT_NAMES = frozenset(
    {
        "cognitive_report.json",
        "complexity_report.json",
        "cycle_report.json",
        "dead_report.json",
        "dup_report.json",
        "exception_report.json",
        "line_report.json",
        "lint_report.json",
        "resource_report.json",
        "sanitize_report.json",
        "security_report.json",
        "test_report.json",
        "type_report.json",
        "verify_report.json",
    }
)
_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "venv",
    }
)


class CacheEntryError(ValueError):
    """Raised internally when a cache entry cannot be trusted."""


def _require_digest(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(_DIGEST_PREFIX)
        or len(value) != len(_DIGEST_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise CacheEntryError(f"{context} must be a sha256 digest")
    return value


@dataclass(frozen=True)
class AnalysisCacheKey:
    """Complete identity of one engine execution."""

    engine_name: str
    project_root_digest: str
    source_digest: str
    config_digest: str
    toolchain_digest: str
    build_variant: str
    descriptor_digest: str
    compilation_digest: str = _EMPTY_COMPILATION_DIGEST
    producer_version: str = __version__
    key_version: str = CACHE_KEY_VERSION

    def __post_init__(self) -> None:
        if not self.engine_name or not self.producer_version:
            raise ValueError("cache engine and producer version must not be empty")
        for name in (
            "project_root_digest",
            "source_digest",
            "config_digest",
            "toolchain_digest",
            "descriptor_digest",
            "compilation_digest",
        ):
            _require_digest(getattr(self, name), f"cache key {name}")
        if self.build_variant not in {"none", *(variant.value for variant in BuildVariant)}:
            raise ValueError(f"unsupported cache build variant: {self.build_variant!r}")
        if self.key_version != CACHE_KEY_VERSION:
            raise ValueError(f"unsupported cache key version: {self.key_version!r}")

    def payload(self) -> dict[str, str]:
        return {
            "key_version": self.key_version,
            "producer_version": self.producer_version,
            "engine_name": self.engine_name,
            "project_root_digest": self.project_root_digest,
            "source_digest": self.source_digest,
            "config_digest": self.config_digest,
            "toolchain_digest": self.toolchain_digest,
            "build_variant": self.build_variant,
            "descriptor_digest": self.descriptor_digest,
            "compilation_digest": self.compilation_digest,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())


def default_cache_dir() -> Path:
    """Return the user-local cache root without touching the filesystem."""

    override = os.environ.get("ICI_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve(strict=False)
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
    return (base / "ici" / "analysis").resolve(strict=False)


def _is_ignored(relative: PurePosixPath) -> bool:
    return any(part in _IGNORED_PARTS for part in relative.parts)


def _is_analysis_input(relative: PurePosixPath) -> bool:
    name = relative.name
    if name in _GENERATED_REPORT_NAMES:
        return False
    return (
        relative.suffix.casefold() in _SOURCE_SUFFIXES
        or relative.suffix.casefold() in _INPUT_SUFFIXES
        or name in _INPUT_NAMES
        or (name.startswith("requirements") and name.endswith(".txt"))
    )


def _regular_contained_file(root: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return _DIGEST_PREFIX + digest.hexdigest()


def _declared_input_candidates(project: ProjectModel, root: Path) -> dict[str, Path]:
    declared = {
        *project.python_sources,
        *project.cpp_sources,
        *project.cpp_headers,
        *project.compilable_cpp_sources,
    }
    candidates: dict[str, Path] = {}
    for relative_text in declared:
        relative = PurePosixPath(relative_text)
        if not _is_ignored(relative):
            candidates[relative.as_posix()] = root / relative
    return candidates


def _discover_input_candidates(root: Path, candidates: dict[str, Path]) -> None:
    for directory, names, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        relative_base = PurePosixPath(base.relative_to(root).as_posix())
        names[:] = sorted(
            name
            for name in names
            if not _is_ignored(relative_base / name) and not (base / name).is_symlink()
        )
        for name in sorted(filenames):
            path = base / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if _is_analysis_input(relative):
                candidates.setdefault(relative.as_posix(), path)


def _input_digest_record(root: Path, relative: str, candidate: Path) -> dict[str, Any] | None:
    resolved = _regular_contained_file(root, candidate)
    if resolved is None:
        return None
    try:
        details_before = resolved.stat()
        digest = _file_digest(resolved)
        details_after = resolved.stat()
    except OSError as err:
        raise CacheEntryError(f"could not read project input {relative}: {err}") from err
    identity_before = (
        details_before.st_size,
        details_before.st_mtime_ns,
        details_before.st_ino,
    )
    identity_after = (
        details_after.st_size,
        details_after.st_mtime_ns,
        details_after.st_ino,
    )
    if identity_before != identity_after:
        raise CacheEntryError(f"project input changed while hashing: {relative}")
    return {
        "path": relative,
        "sha256": digest,
        "mode": stat.S_IMODE(details_after.st_mode),
    }


def project_source_digest(project: ProjectModel) -> str:
    """Hash source and analysis/build configuration without modifying the project."""

    root = project.root.resolve(strict=False)
    candidates = _declared_input_candidates(project, root)
    try:
        _discover_input_candidates(root, candidates)
    except OSError as err:
        raise CacheEntryError(f"could not enumerate project inputs: {err}") from err

    records: list[dict[str, Any]] = []
    for relative_text, candidate in sorted(candidates.items()):
        record = _input_digest_record(root, relative_text, candidate)
        if record is not None:
            records.append(record)
    return canonical_digest(records)


def build_analysis_cache_key(
    descriptor: EngineDescriptor,
    context: AnalysisContext,
    source_digest: str,
    implementation: object | type[object] | None = None,
) -> AnalysisCacheKey:
    """Build a stable key from every input allowed to change engine semantics."""

    implementation_type = (
        implementation
        if isinstance(implementation, type)
        else type(implementation)
        if implementation is not None
        else None
    )
    implementation_source = ""
    if implementation_type is not None:
        try:
            implementation_source = inspect.getsource(implementation_type)
        except (OSError, TypeError):
            implementation_source = ""
    implementation_modules: list[dict[str, str]] = []
    if implementation_type is not None:
        raw_modules = getattr(implementation_type, "CACHE_IMPLEMENTATION_MODULES", ())
        if isinstance(raw_modules, tuple) and all(
            isinstance(name, str) and name for name in raw_modules
        ):
            for name in sorted(set(raw_modules)):
                module = sys.modules.get(name)
                try:
                    source = inspect.getsource(module) if module is not None else ""
                except (OSError, TypeError):
                    source = ""
                implementation_modules.append(
                    {"module": name, "source_digest": canonical_digest(source)}
                )
    descriptor_payload = {
        "name": descriptor.name,
        "factory_name": descriptor.factory_name,
        "dependencies": list(descriptor.dependencies),
        "produces": list(descriptor.produces),
        "consumes": list(descriptor.consumes),
        "execution": descriptor.execution.value,
        "profiles": sorted(profile.value for profile in descriptor.profiles),
        "implementation": (
            {
                "module": implementation_type.__module__,
                "qualname": implementation_type.__qualname__,
                "source_digest": canonical_digest(implementation_source),
                "dependency_modules": implementation_modules,
            }
            if implementation_type is not None
            else None
        ),
    }
    compilation = context.compilation
    compilation_payload = {
        "version": COMPILATION_IDENTITY_VERSION,
        "database_path": compilation.database_path,
        "database_digest": compilation.database_digest,
        "origin": compilation.origin,
        "generator": compilation.generator,
        "unity_build": compilation.unity_build,
        "units": [
            {
                "source": unit.source,
                "directory": unit.directory,
                "configuration": unit.configuration,
                "compiler": unit.compiler,
                "language": unit.language,
                "standard": unit.standard,
                "target": unit.target,
                "output": unit.output,
                "defines": [
                    {"name": definition.name, "value": definition.value}
                    for definition in unit.defines
                ],
                "include_paths": [
                    {
                        "path": search_path.path,
                        "kind": search_path.kind,
                        "scope": search_path.scope,
                        "exists": search_path.exists,
                    }
                    for search_path in unit.include_paths
                ],
                "sysroot": unit.sysroot,
                "sysroot_scope": unit.sysroot_scope,
                "diagnostics": [
                    {
                        "code": diagnostic.code,
                        "level": diagnostic.level,
                        "entry_index": diagnostic.entry_index,
                        "source": diagnostic.source,
                    }
                    for diagnostic in unit.diagnostics
                ],
            }
            for unit in compilation.units
        ],
        "diagnostics": [
            {
                "code": diagnostic.code,
                "level": diagnostic.level,
                "entry_index": diagnostic.entry_index,
                "source": diagnostic.source,
            }
            for diagnostic in compilation.diagnostics
        ],
    }
    return AnalysisCacheKey(
        engine_name=descriptor.name,
        project_root_digest=canonical_digest(str(context.project.root)),
        source_digest=source_digest,
        config_digest=context.identity.config_digest,
        toolchain_digest=context.identity.toolchain_digest,
        build_variant=(
            descriptor.build_variant.value if descriptor.build_variant is not None else "none"
        ),
        descriptor_digest=canonical_digest(descriptor_payload),
        compilation_digest=canonical_digest(compilation_payload),
    )


def is_cacheable_result(result: EngineResult, key: AnalysisCacheKey) -> bool:
    """Return whether a completed result is safe to reuse as analysis evidence."""

    if result.status in {EngineStatus.ERROR, EngineStatus.SKIP}:
        return False
    if result.evidence is EvidenceState.NOT_RUN:
        return False
    if any(item.timed_out or item.truncated or bool(item.error) for item in result.tool_evidence):
        return False
    expected_variant = None if key.build_variant == "none" else BuildVariant(key.build_variant)
    if result.artifact_manifests and expected_variant is None:
        return False
    try:
        for manifest in result.artifact_manifests:
            if manifest.variant is not expected_variant:
                return False
            if (
                manifest.config_digest != key.config_digest
                or manifest.toolchain_digest != key.toolchain_digest
            ):
                return False
            manifest.validate()
    except (OSError, ValueError):
        return False
    return True
