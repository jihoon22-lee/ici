"""Immutable ownership contracts for one ici analysis run.

The analysis context is input state: engines and reporters may retain and read
it, but they cannot append discoveries to it.  Build adapters keep their
short-lived mutable state in ``BuildSession`` and publish successful outputs as
validated, immutable ``ArtifactManifest`` values.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core.backend import select_backend
from ici.core.capabilities import CapabilityInventory
from ici.core.project import (
    detect_project_type,
    get_all_cpp_headers,
    get_all_cpp_includes,
    get_all_cpp_sources,
    get_all_python_sources,
    get_compilable_cpp_sources,
    get_cpp_external_build_dirs,
    get_project_name,
    get_project_version,
    get_source_dirs,
)
from ici.core.runner import run_process

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}\Z")
_ARTIFACT_METADATA_TEXT_LIMIT = 512
_ARTIFACT_COMMAND_ARGS_LIMIT = 32_768
_ARTIFACT_COMMAND_ARG_LIMIT = 1_048_576
MAX_ARTIFACT_MANIFEST_RECORDS = 512
MAX_ARTIFACT_FILE_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 1024 * 1024 * 1024


class BuildVariant(str, Enum):
    """A semantically distinct build tree requested by an engine."""

    RELEASE = "release"
    COVERAGE = "coverage"
    SANITIZE = "sanitize"
    THREAD_SANITIZE = "thread-sanitize"


class ArtifactScope(str, Enum):
    """The root against which an artifact path is interpreted."""

    PROJECT = "project"
    SHADOW = "shadow"


def canonical_digest(value: Any) -> str:
    """Hash JSON-compatible data independently of mapping insertion order."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise ValueError(f"value is not canonical JSON data: {err}") from err
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def source_commit(root: Path) -> str:
    """Return the repository HEAD containing ``root``, or ``unavailable``."""

    git = shutil.which("git")
    if git is None:
        return "unavailable"
    result = run_process(
        [git, "-C", str(root), "rev-parse", "--verify", "HEAD"],
        cwd=root,
        timeout=2.0,
        max_output_chars=256,
    )
    value = result.stdout.strip().lower()
    if (
        result.returncode != 0
        or result.timed_out
        or result.truncated
        or _COMMIT_RE.fullmatch(value) is None
    ):
        return "unavailable"
    return value


def _relative_project_path(root: Path, path: Path) -> str:
    try:
        resolved = path.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as err:
        raise ValueError(f"path is outside project root: {path}") from err
    return relative.as_posix() or "."


@dataclass(frozen=True)
class ProjectModel:
    """One deterministic project discovery snapshot owned by a run."""

    root: Path
    name: str
    version: str
    project_type: str
    source_dirs: tuple[str, ...] = ()
    python_sources: tuple[str, ...] = ()
    cpp_sources: tuple[str, ...] = ()
    cpp_headers: tuple[str, ...] = ()
    compilable_cpp_sources: tuple[str, ...] = ()
    external_cpp_dirs: tuple[str, ...] = ()
    cpp_include_flags: tuple[str, ...] = ()
    backend: str | None = None
    backend_descriptor: str = ""
    backend_reason: str = ""

    def __post_init__(self) -> None:
        try:
            canonical_root = self.root.resolve(strict=False)
        except (OSError, RuntimeError, TypeError, ValueError) as err:
            raise ValueError(f"could not resolve project root {self.root}: {err}") from err
        object.__setattr__(self, "root", canonical_root)
        for field_name in (
            "source_dirs",
            "python_sources",
            "cpp_sources",
            "cpp_headers",
            "compilable_cpp_sources",
            "external_cpp_dirs",
            "cpp_include_flags",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        for field_name in (
            "source_dirs",
            "python_sources",
            "cpp_sources",
            "cpp_headers",
            "compilable_cpp_sources",
            "external_cpp_dirs",
        ):
            for value in getattr(self, field_name):
                _validate_relative_path(
                    value,
                    f"project {field_name}",
                    allow_dot=field_name in {"source_dirs", "external_cpp_dirs"},
                )
        if self.project_type not in {"python", "cpp", "hybrid"}:
            raise ValueError(f"unsupported project type: {self.project_type!r}")


def discover_project_model(root: Path, config: dict[str, Any]) -> ProjectModel:
    """Discover project metadata and source scope exactly once."""

    canonical_root = root.resolve(strict=False)
    backend = select_backend(canonical_root, config)
    project_config = config.get("project", {})
    configured_type = config.get("type")
    if configured_type is None and isinstance(project_config, dict):
        configured_type = project_config.get("type")
    project_type = (
        configured_type
        if configured_type in {"python", "cpp", "hybrid"}
        else detect_project_type(canonical_root)
    )

    def relative(paths) -> tuple[str, ...]:
        return tuple(sorted(_relative_project_path(canonical_root, path) for path in paths))

    source_dirs = get_source_dirs(canonical_root, config)
    return ProjectModel(
        root=canonical_root,
        name=get_project_name(canonical_root),
        version=get_project_version(canonical_root),
        project_type=project_type,
        source_dirs=relative(source_dirs),
        python_sources=relative(get_all_python_sources(canonical_root, config)),
        cpp_sources=relative(get_all_cpp_sources(canonical_root, config)),
        cpp_headers=relative(get_all_cpp_headers(canonical_root, config)),
        compilable_cpp_sources=relative(get_compilable_cpp_sources(canonical_root, config)),
        external_cpp_dirs=relative(get_cpp_external_build_dirs(canonical_root, config)),
        cpp_include_flags=tuple(get_all_cpp_includes(canonical_root, config)),
        backend=backend.kind,
        backend_descriptor=backend.descriptor,
        backend_reason=backend.reason,
    )


def _toolchain_payload(inventory: CapabilityInventory) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for name in sorted(inventory.capabilities):
        capability = inventory.capabilities[name]
        payload.append(
            {
                "name": name,
                "path": capability.path,
                "available": capability.available,
                "complete": capability.complete,
                "version": capability.version,
                "version_tuple": list(capability.version_tuple),
                "details": dict(sorted(capability.details.items())),
            }
        )
    return payload


@dataclass(frozen=True)
class AnalysisIdentity:
    """Inputs that make build and analysis outputs reproducible."""

    source_commit: str
    config_digest: str
    toolchain_digest: str

    def __post_init__(self) -> None:
        if self.source_commit != "unavailable" and _COMMIT_RE.fullmatch(self.source_commit) is None:
            raise ValueError("source_commit must be a full hexadecimal commit id or unavailable")
        for name in ("config_digest", "toolchain_digest"):
            if _DIGEST_RE.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"{name} must be a sha256 digest")


@dataclass(frozen=True)
class CompilationDiagnostic:
    """Bounded, reporting-safe evidence from compile database ingestion."""

    code: str
    message: str
    level: str = "warning"
    entry_index: int | None = None
    source: str = ""

    def __post_init__(self) -> None:
        for field_name in ("code", "message", "level", "source"):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"compilation diagnostic {field_name} must be a string")
        if not self.code or not self.message:
            raise ValueError("compilation diagnostic code and message must not be empty")
        if self.level not in {"info", "warning", "error"}:
            raise ValueError(f"unsupported compilation diagnostic level: {self.level!r}")
        if self.entry_index is not None and (
            type(self.entry_index) is not int or self.entry_index < 0
        ):
            raise ValueError("compilation diagnostic entry index must be non-negative")
        if self.source:
            _validate_relative_path(self.source, "compilation diagnostic source", allow_dot=False)


@dataclass(frozen=True)
class CompilationDefine:
    """One compiler preprocessor definition without losing its optional value."""

    name: str
    value: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("compilation define name must be a string")
        if not self.name or any(character.isspace() for character in self.name):
            raise ValueError("compilation define name must be a non-empty token")
        if self.value is not None and not isinstance(self.value, str):
            raise ValueError("compilation define value must be a string or null")


@dataclass(frozen=True)
class CompilationSearchPath:
    """One normalized compiler header search path and its trust scope."""

    path: str
    kind: str
    scope: str
    exists: bool

    def __post_init__(self) -> None:
        for field_name in ("path", "kind", "scope"):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"compilation search path {field_name} must be a string")
        if self.kind not in {"include", "system", "quote"}:
            raise ValueError(f"unsupported compilation search path kind: {self.kind!r}")
        if self.scope not in {"project", "external"}:
            raise ValueError(f"unsupported compilation search path scope: {self.scope!r}")
        if type(self.exists) is not bool:
            raise ValueError("compilation search path exists flag must be a boolean")
        if self.scope == "project":
            _validate_relative_path(self.path, "compilation search path", allow_dot=True)
        elif not isinstance(self.path, str) or not self.path:
            raise ValueError("external compilation search path must not be empty")


def _typed_tuple(values: Any, expected: type, description: str) -> tuple[Any, ...]:
    normalized = _collection_tuple(values, description)
    if not all(isinstance(item, expected) for item in normalized):
        raise ValueError(f"{description} must contain {expected.__name__} values")
    return normalized


def _collection_tuple(values: Any, description: str) -> tuple[Any, ...]:
    """Normalize a model collection while turning boundary TypeErrors into ValueErrors."""

    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(values, Iterable):
        raise ValueError(f"{description} must be an iterable collection")
    try:
        return tuple(values)
    except TypeError as err:
        raise ValueError(f"{description} must be an iterable collection") from err


def _validate_compilation_unit_scalars(unit: CompilationUnit) -> None:
    _validate_relative_path(unit.source, "compilation source", allow_dot=False)
    _validate_relative_path(unit.directory, "compilation directory", allow_dot=True)
    if not isinstance(unit.output, str):
        raise ValueError("compilation output must be a string")
    if unit.output:
        _validate_relative_path(unit.output, "compilation output", allow_dot=False)
    if not unit.argv or not all(isinstance(item, str) and item for item in unit.argv):
        raise ValueError("compilation argv must contain non-empty strings")
    for field_name in ("compiler", "language", "standard", "target", "configuration"):
        if not isinstance(getattr(unit, field_name), str):
            raise ValueError(f"compilation {field_name} must be a string")
    if unit.target and (
        len(unit.target) > 512
        or any(character in unit.target for character in ("/", "\\", "\0", "\n", "\r"))
    ):
        raise ValueError("compilation target must be a bounded name")
    if unit.language and unit.language not in {"c", "c++", "objective-c", "objective-c++"}:
        raise ValueError(f"unsupported compilation language: {unit.language!r}")
    if unit.configuration and _DIGEST_RE.fullmatch(unit.configuration) is None:
        raise ValueError("compilation configuration must be a sha256 digest")


def _validate_compilation_sysroot(unit: CompilationUnit) -> None:
    for field_name in ("sysroot", "sysroot_scope"):
        if not isinstance(getattr(unit, field_name), str):
            raise ValueError(f"compilation {field_name} must be a string")
    if unit.sysroot_scope not in {"", "project", "external"}:
        raise ValueError(f"unsupported compilation sysroot scope: {unit.sysroot_scope!r}")
    if bool(unit.sysroot) != bool(unit.sysroot_scope):
        raise ValueError("compilation sysroot and scope must be declared together")
    if unit.sysroot_scope == "project":
        _validate_relative_path(unit.sysroot, "compilation sysroot", allow_dot=True)
    elif unit.sysroot and not isinstance(unit.sysroot, str):
        raise ValueError("external compilation sysroot must be a string")


@dataclass(frozen=True)
class CompilationUnit:
    """One normalized compile invocation with immutable provenance."""

    source: str
    directory: str
    argv: tuple[str, ...]
    output: str = ""
    compiler: str = ""
    language: str = ""
    standard: str = ""
    target: str = ""
    defines: tuple[CompilationDefine, ...] = ()
    include_paths: tuple[CompilationSearchPath, ...] = ()
    sysroot: str = ""
    sysroot_scope: str = ""
    configuration: str = ""
    diagnostics: tuple[CompilationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        argv = _collection_tuple(self.argv, "compilation argv")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("compilation argv must contain non-empty strings")
        object.__setattr__(self, "argv", argv)
        defines = _typed_tuple(self.defines, CompilationDefine, "compilation defines")
        include_paths = _typed_tuple(
            self.include_paths,
            CompilationSearchPath,
            "compilation include paths",
        )
        diagnostics = _typed_tuple(
            self.diagnostics,
            CompilationDiagnostic,
            "compilation unit diagnostics",
        )
        object.__setattr__(self, "defines", defines)
        object.__setattr__(self, "include_paths", include_paths)
        object.__setattr__(self, "diagnostics", diagnostics)
        _validate_compilation_unit_scalars(self)
        _validate_compilation_sysroot(self)


@dataclass(frozen=True)
class CompilationContext:
    """Immutable owner of compilation database observations."""

    units: tuple[CompilationUnit, ...] = ()
    database_path: str | None = None
    database_digest: str = ""
    origin: str = ""
    generator: str = ""
    unity_build: bool | None = None
    diagnostics: tuple[CompilationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        units = _typed_tuple(self.units, CompilationUnit, "compilation context units")
        if self.database_path is not None:
            if not isinstance(self.database_path, str):
                raise ValueError("compilation database path must be a string or null")
            _validate_relative_path(self.database_path, "compilation database", allow_dot=False)
        if not isinstance(self.database_digest, str):
            raise ValueError("compilation database digest must be a string")
        if self.database_digest and _DIGEST_RE.fullmatch(self.database_digest) is None:
            raise ValueError("compilation database digest must be a sha256 digest")
        if not isinstance(self.origin, str) or self.origin not in {
            "",
            "configured",
            "discovered",
            "cmake",
            "qmake",
        }:
            raise ValueError(f"unsupported compilation database origin: {self.origin!r}")
        if not isinstance(self.generator, str) or len(self.generator) > 512:
            raise ValueError("compilation generator must be a bounded string")
        if self.unity_build is not None and type(self.unity_build) is not bool:
            raise ValueError("compilation unity build flag must be boolean or null")
        diagnostics = _typed_tuple(
            self.diagnostics,
            CompilationDiagnostic,
            "compilation context diagnostics",
        )
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "diagnostics", diagnostics)


@dataclass(frozen=True)
class ArtifactRecord:
    """Identity and producer metadata for one regular output file."""

    path: str
    scope: ArtifactScope
    kind: str
    sha256: str
    size: int
    mode: int
    producer: str
    artifact_id: str = ""
    target: str = ""
    command: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_relative_path(self.path, "artifact", allow_dot=False)
        if not isinstance(self.scope, ArtifactScope):
            object.__setattr__(self, "scope", ArtifactScope(self.scope))
        if not self.kind or not self.producer:
            raise ValueError("artifact kind and producer must not be empty")
        for field_name in ("artifact_id", "target"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"artifact {field_name} must be a string")
            if len(value) > _ARTIFACT_METADATA_TEXT_LIMIT:
                raise ValueError(f"artifact {field_name} exceeds the size limit")
        if isinstance(self.command, (str, bytes)) or not isinstance(self.command, (tuple, list)):
            raise ValueError("artifact command must be an array of strings")
        command = tuple(self.command)
        if len(command) > _ARTIFACT_COMMAND_ARGS_LIMIT:
            raise ValueError("artifact command exceeds the argument limit")
        if any(not isinstance(argument, str) for argument in command):
            raise ValueError("artifact command arguments must be strings")
        if any(len(argument) > _ARTIFACT_COMMAND_ARG_LIMIT for argument in command):
            raise ValueError("artifact command argument exceeds the size limit")
        object.__setattr__(self, "command", command)
        if _DIGEST_RE.fullmatch(self.sha256) is None:
            raise ValueError("artifact sha256 must be a canonical digest")
        if type(self.size) is not int or self.size < 0:
            raise ValueError("artifact size must be a non-negative integer")
        if type(self.mode) is not int or self.mode < 0:
            raise ValueError("artifact mode must be a non-negative integer")


def _validate_relative_path(value: str, description: str, *, allow_dot: bool) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{description} path must be non-empty project-relative POSIX form")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{description} path must be relative and contained: {value!r}")
    canonical = path.as_posix()
    if canonical != value or (not allow_dot and canonical == "."):
        raise ValueError(f"{description} path must be canonical: {value!r}")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    consumed = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            consumed += len(chunk)
            if consumed > MAX_ARTIFACT_FILE_BYTES:
                raise ValueError(f"artifact exceeds the per-file byte limit: {path.name}")
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class ArtifactManifest:
    """Validated outputs for one build variant and analysis identity."""

    project_root: Path
    shadow_root: Path | None
    variant: BuildVariant
    source_commit: str
    config_digest: str
    toolchain_digest: str
    artifacts: tuple[ArtifactRecord, ...] = ()

    def __post_init__(self) -> None:
        project_root = self.project_root.resolve(strict=False)
        shadow_root = (
            self.shadow_root.resolve(strict=False) if self.shadow_root is not None else None
        )
        object.__setattr__(self, "project_root", project_root)
        object.__setattr__(self, "shadow_root", shadow_root)
        if not isinstance(self.variant, BuildVariant):
            object.__setattr__(self, "variant", BuildVariant(self.variant))
        AnalysisIdentity(self.source_commit, self.config_digest, self.toolchain_digest)
        object.__setattr__(self, "artifacts", tuple(self.artifacts))

    @classmethod
    def create(
        cls,
        project_root: Path,
        shadow_root: Path | None,
        variant: BuildVariant,
        identity: AnalysisIdentity,
        paths: list[tuple[Path, ArtifactScope, str]] | tuple[tuple[Path, ArtifactScope, str], ...],
        producer: str,
    ) -> ArtifactManifest:
        """Create records from existing regular files, then validate the result."""

        root = project_root.resolve(strict=False)
        shadow = shadow_root.resolve(strict=False) if shadow_root is not None else None
        if len(paths) > MAX_ARTIFACT_MANIFEST_RECORDS:
            raise ValueError(
                f"artifact manifest exceeds the {MAX_ARTIFACT_MANIFEST_RECORDS} record limit"
            )
        records: list[ArtifactRecord] = []
        total_size = 0
        for path, scope, kind in paths:
            declared_root = root if scope is ArtifactScope.PROJECT else shadow
            if declared_root is None:
                raise ValueError("shadow artifact requires a shadow root")
            if path.is_absolute():
                raise ValueError(f"artifact path must be relative: {path}")
            relative_input = path.as_posix()
            _validate_relative_path(relative_input, "artifact", allow_dot=False)
            lexical = declared_root / PurePosixPath(relative_input)
            try:
                resolved = lexical.resolve(strict=True)
            except (OSError, RuntimeError) as err:
                raise ValueError(f"artifact file could not be resolved: {path}: {err}") from err
            if not _is_within(resolved, declared_root):
                raise ValueError(f"artifact symlink escapes its declared root: {path}")
            if not resolved.is_file():
                raise ValueError(f"artifact is not a regular file: {path}")
            relative = resolved.relative_to(declared_root).as_posix()
            details = resolved.stat()
            if details.st_size > MAX_ARTIFACT_FILE_BYTES:
                raise ValueError(f"artifact exceeds the per-file byte limit: {relative}")
            total_size += details.st_size
            if total_size > MAX_ARTIFACT_TOTAL_BYTES:
                raise ValueError("artifact manifest exceeds the aggregate byte limit")
            records.append(
                ArtifactRecord(
                    path=relative,
                    scope=scope,
                    kind=kind,
                    sha256=_file_digest(resolved),
                    size=details.st_size,
                    mode=stat.S_IMODE(details.st_mode),
                    producer=producer,
                )
            )
        manifest = cls(
            project_root=root,
            shadow_root=shadow,
            variant=variant,
            source_commit=identity.source_commit,
            config_digest=identity.config_digest,
            toolchain_digest=identity.toolchain_digest,
            artifacts=tuple(
                sorted(
                    records,
                    key=lambda item: (item.scope.value, item.path, item.kind, item.producer),
                )
            ),
        )
        return manifest.validate()

    def validate(self) -> ArtifactManifest:
        """Revalidate containment and content identity at a trust boundary."""

        if self.shadow_root is not None and not _is_within(self.shadow_root, self.project_root):
            raise ValueError("shadow root is outside project root")
        if len(self.artifacts) > MAX_ARTIFACT_MANIFEST_RECORDS:
            raise ValueError(
                f"artifact manifest exceeds the {MAX_ARTIFACT_MANIFEST_RECORDS} record limit"
            )
        total_size = sum(artifact.size for artifact in self.artifacts)
        if any(artifact.size > MAX_ARTIFACT_FILE_BYTES for artifact in self.artifacts):
            raise ValueError("artifact manifest contains an oversized artifact")
        if total_size > MAX_ARTIFACT_TOTAL_BYTES:
            raise ValueError("artifact manifest exceeds the aggregate byte limit")
        seen: set[tuple[ArtifactScope, str]] = set()
        seen_ids: set[str] = set()
        for artifact in self.artifacts:
            key = (artifact.scope, artifact.path)
            if key in seen:
                raise ValueError(
                    f"duplicate artifact record: {artifact.scope.value}:{artifact.path}"
                )
            seen.add(key)
            if artifact.artifact_id:
                if artifact.artifact_id in seen_ids:
                    raise ValueError(f"duplicate artifact id: {artifact.artifact_id}")
                seen_ids.add(artifact.artifact_id)
            declared_root = (
                self.project_root if artifact.scope is ArtifactScope.PROJECT else self.shadow_root
            )
            if declared_root is None:
                raise ValueError("shadow artifact requires a shadow root")
            lexical = declared_root / PurePosixPath(artifact.path)
            try:
                resolved = lexical.resolve(strict=True)
            except (OSError, RuntimeError) as err:
                raise ValueError(f"artifact could not be resolved: {artifact.path}: {err}") from err
            if not _is_within(resolved, declared_root):
                raise ValueError(f"artifact symlink escapes its declared root: {artifact.path}")
            if not resolved.is_file():
                raise ValueError(f"artifact is not a regular file: {artifact.path}")
            details = resolved.stat()
            if details.st_size != artifact.size:
                raise ValueError(f"artifact size changed: {artifact.path}")
            if stat.S_IMODE(details.st_mode) != artifact.mode:
                raise ValueError(f"artifact mode changed: {artifact.path}")
            if _file_digest(resolved) != artifact.sha256:
                raise ValueError(f"artifact content changed: {artifact.path}")
        return self


@dataclass(frozen=True)
class AnalysisContext:
    """Read-only run input shared by engines and reporters."""

    project: ProjectModel
    capabilities: CapabilityInventory
    identity: AnalysisIdentity
    compilation: CompilationContext = field(default_factory=CompilationContext)
    profile: str = "standard"
    requested_variants: tuple[BuildVariant, ...] = ()
    manifests: tuple[ArtifactManifest, ...] = ()

    def __post_init__(self) -> None:
        if self.profile not in {"fast", "standard", "deep"}:
            raise ValueError(f"unsupported analysis profile: {self.profile!r}")
        order = {variant: index for index, variant in enumerate(BuildVariant)}
        variants = tuple(
            sorted(
                {
                    item if isinstance(item, BuildVariant) else BuildVariant(item)
                    for item in self.requested_variants
                },
                key=order.__getitem__,
            )
        )
        manifests = tuple(self.manifests)
        for manifest in manifests:
            if manifest.project_root != self.project.root:
                raise ValueError("artifact manifest belongs to another project")
            if (
                manifest.source_commit,
                manifest.config_digest,
                manifest.toolchain_digest,
            ) != (
                self.identity.source_commit,
                self.identity.config_digest,
                self.identity.toolchain_digest,
            ):
                raise ValueError("artifact manifest identity does not match analysis context")
        object.__setattr__(self, "requested_variants", variants)
        object.__setattr__(self, "manifests", manifests)

    def with_manifest(self, manifest: ArtifactManifest) -> AnalysisContext:
        """Return a new context snapshot; never mutate reporter-visible input."""

        return replace(self, manifests=(*self.manifests, manifest))


def create_analysis_context(
    root: Path,
    config: dict[str, Any],
    capabilities: CapabilityInventory,
    requested_variants: tuple[BuildVariant, ...] = (),
    *,
    profile: str = "standard",
    project: ProjectModel | None = None,
    compilation: CompilationContext | None = None,
) -> AnalysisContext:
    """Create the run snapshot after capability policy has been evaluated."""

    project = project or discover_project_model(root, config)
    if project.root != root.resolve(strict=False):
        raise ValueError("project model belongs to another root")
    identity = AnalysisIdentity(
        source_commit=source_commit(project.root),
        config_digest=canonical_digest(config),
        toolchain_digest=canonical_digest(_toolchain_payload(capabilities)),
    )
    return AnalysisContext(
        project=project,
        capabilities=capabilities,
        identity=identity,
        compilation=compilation if compilation is not None else CompilationContext(),
        profile=profile,
        requested_variants=requested_variants,
    )
