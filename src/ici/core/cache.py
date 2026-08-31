"""Deterministic, local-only cache for completed engine results."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import stat
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ici import __version__
from ici.core.context import (
    AnalysisContext,
    ArtifactManifest,
    ArtifactRecord,
    ArtifactScope,
    BuildVariant,
    ProjectModel,
    canonical_digest,
)
from ici.core.models import (
    AnalysisMode,
    EngineResult,
    EngineStatus,
    EngineSupport,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingMetric,
    FindingSeverity,
    FindingSuppression,
    InspectionTarget,
    SourceLocation,
    SupportLanguage,
    SupportMatrix,
    SuppressionKind,
    ToolEvidence,
)
from ici.reporters.json_rep import serialize_engine_result

if TYPE_CHECKING:
    from ici.core.pipeline import EngineDescriptor

CACHE_SCHEMA_VERSION = "ici.analysis-cache/v1"
CACHE_KEY_VERSION = "ici.analysis-cache-key/v1"
MAX_CACHE_ENTRY_BYTES = 32 * 1024 * 1024

_DIGEST_PREFIX = "sha256:"
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
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.payload())


@dataclass(frozen=True)
class CacheInventory:
    """Small, non-sensitive summary shown by ``ici cache``."""

    root: Path
    entries: int
    bytes: int
    corrupt_entries: int


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


def project_source_digest(project: ProjectModel) -> str:
    """Hash source and analysis/build configuration without modifying the project."""

    root = project.root.resolve(strict=False)
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
    try:
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
    except OSError as err:
        raise CacheEntryError(f"could not enumerate project inputs: {err}") from err

    records: list[dict[str, Any]] = []
    for relative, candidate in sorted(candidates.items()):
        resolved = _regular_contained_file(root, candidate)
        if resolved is None:
            continue
        try:
            details_before = resolved.stat()
            digest = _file_digest(resolved)
            details_after = resolved.stat()
        except OSError as err:
            raise CacheEntryError(f"could not read project input {relative}: {err}") from err
        if (
            details_before.st_size,
            details_before.st_mtime_ns,
            details_before.st_ino,
        ) != (
            details_after.st_size,
            details_after.st_mtime_ns,
            details_after.st_ino,
        ):
            raise CacheEntryError(f"project input changed while hashing: {relative}")
        records.append(
            {
                "path": relative,
                "sha256": digest,
                "mode": stat.S_IMODE(details_after.st_mode),
            }
        )
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
            }
            if implementation_type is not None
            else None
        ),
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


def _serialize_cache_result(result: EngineResult, project_root: Path) -> dict[str, Any]:
    """Serialize native findings only; legacy target findings are report-time projections."""

    serialized = serialize_engine_result(result, project_root=project_root)
    native_only = replace(result, targets=[])
    serialized["findings"] = serialize_engine_result(
        native_only,
        project_root=project_root,
    )["findings"]
    return serialized


class AnalysisCache:
    """Atomic digest-addressed store; cache failures always degrade to misses."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or default_cache_dir()).resolve(strict=False)
        self.entries_dir = self.root / "entries-v1"

    def _entry_path(self, key: AnalysisCacheKey) -> Path:
        digest = _require_digest(key.digest, "cache entry key").removeprefix(_DIGEST_PREFIX)
        return self.entries_dir / f"{digest}.json"

    def load(self, key: AnalysisCacheKey, project_root: Path) -> EngineResult | None:
        """Load and validate one result. Corrupt, stale, or missing entries are misses."""

        if self.entries_dir.is_symlink():
            return None
        path = self._entry_path(key)
        try:
            if path.is_symlink():
                return None
            details = path.stat()
            if not path.is_file() or details.st_size > MAX_CACHE_ENTRY_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            result = _decode_entry(payload, key, project_root.resolve(strict=False))
            serialize_engine_result(result, project_root=project_root)
        except (
            CacheEntryError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ):
            return None
        return replace(result, cache_hit=True, cache_key=key.digest)

    def store(self, key: AnalysisCacheKey, result: EngineResult, project_root: Path) -> bool:
        """Atomically store a reusable result and return whether it was written."""

        if not is_cacheable_result(result, key):
            return False
        clean = replace(result, cache_hit=False, cache_key="")
        try:
            if self.entries_dir.is_symlink():
                return False
            serialized = _serialize_cache_result(clean, project_root)
            payload = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "key": key.digest,
                "inputs": key.payload(),
                "created_at": int(time.time()),
                "result": serialized,
            }
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_CACHE_ENTRY_BYTES:
                return False
            self.entries_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.entries_dir.resolve(strict=True).parent != self.root:
                return False
            path = self._entry_path(key)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.entries_dir,
                prefix=f".{path.stem}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                with suppress(FileNotFoundError):
                    temporary.unlink()
        except (OSError, TypeError, ValueError):
            return False
        return True

    def inventory(self) -> CacheInventory:
        """Return a bounded inventory without decoding cached diagnostics."""

        entries = 0
        total_bytes = 0
        corrupt = 0
        if self.entries_dir.is_symlink():
            return CacheInventory(
                root=self.root,
                entries=0,
                bytes=0,
                corrupt_entries=1,
            )
        try:
            candidates = tuple(self.entries_dir.glob("*.json"))
        except OSError:
            candidates = ()
            corrupt = 1
        for path in candidates:
            try:
                if path.is_symlink() or not path.is_file():
                    corrupt += 1
                    continue
                details = path.stat()
                total_bytes += details.st_size
                if details.st_size > MAX_CACHE_ENTRY_BYTES:
                    corrupt += 1
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                _validate_inventory_entry(payload, path)
            except (CacheEntryError, OSError, UnicodeError, ValueError, json.JSONDecodeError):
                corrupt += 1
            else:
                entries += 1
        return CacheInventory(
            root=self.root,
            entries=entries,
            bytes=total_bytes,
            corrupt_entries=corrupt,
        )

    def clear(self) -> int:
        """Remove only cache entry/temp files under this cache's exact entries directory."""

        removed = 0
        if self.entries_dir.is_symlink():
            return 0
        try:
            candidates = tuple(self.entries_dir.iterdir())
        except (FileNotFoundError, OSError):
            return 0
        for path in candidates:
            if not (path.name.endswith(".json") or path.name.endswith(".tmp")):
                continue
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


def _require_digest(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(_DIGEST_PREFIX)
        or len(value) != len(_DIGEST_PREFIX) + 64
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise CacheEntryError(f"{context} must be a sha256 digest")
    return value


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CacheEntryError(f"{context} must be an object")
    return value


def _sequence(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise CacheEntryError(f"{context} must be an array")
    return value


def _string(value: Any, context: str, *, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise CacheEntryError(f"{context} must be a string")
    return value


def _boolean(value: Any, context: str) -> bool:
    if type(value) is not bool:
        raise CacheEntryError(f"{context} must be a boolean")
    return value


def _number(value: Any, context: str, *, nullable: bool = False) -> int | float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CacheEntryError(f"{context} must be a number")
    number = float(value)
    if not (number >= 0.0) or number == float("inf"):
        raise CacheEntryError(f"{context} must be finite and non-negative")
    return value


def _optional_int(value: Any, context: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value < 1:
        raise CacheEntryError(f"{context} must be a positive integer or null")
    return value


def _string_list(value: Any, context: str) -> list[str]:
    return [_string(item, f"{context} item") for item in _sequence(value, context)]


def _location(payload: Any, context: str) -> SourceLocation:
    value = _mapping(payload, context)
    return SourceLocation(
        path=_string(value.get("path"), f"{context}.path", nonempty=True),
        start_line=_optional_int(value.get("start_line"), f"{context}.start_line") or 1,
        end_line=_optional_int(value.get("end_line"), f"{context}.end_line"),
        start_column=_optional_int(value.get("start_column"), f"{context}.start_column"),
        end_column=_optional_int(value.get("end_column"), f"{context}.end_column"),
        label=_string(value.get("label"), f"{context}.label"),
    )


def _target(payload: Any) -> InspectionTarget:
    value = _mapping(payload, "cache target")
    metrics = _mapping(value.get("metrics"), "cache target.metrics")
    return InspectionTarget(
        file_path=_string(value.get("file_path"), "cache target.file_path", nonempty=True),
        start_line=_optional_int(value.get("start_line"), "cache target.start_line") or 1,
        end_line=_optional_int(value.get("end_line"), "cache target.end_line"),
        target_name=_string(value.get("target_name"), "cache target.target_name"),
        status=EngineStatus(_string(value.get("status"), "cache target.status")),
        message=_string(value.get("message"), "cache target.message"),
        snippet=_string(value.get("snippet"), "cache target.snippet"),
        metrics=metrics,
        start_column=_optional_int(value.get("start_column"), "cache target.start_column"),
        end_column=_optional_int(value.get("end_column"), "cache target.end_column"),
    )


def _tool(payload: Any) -> ToolEvidence:
    value = _mapping(payload, "cache tool evidence")
    returncode = value.get("returncode")
    if returncode is not None and type(returncode) is not int:
        raise CacheEntryError("cache tool returncode must be an integer or null")
    return ToolEvidence(
        name=_string(value.get("name"), "cache tool.name"),
        path=_string(value.get("path"), "cache tool.path"),
        version=_string(value.get("version"), "cache tool.version"),
        argv=_string_list(value.get("argv"), "cache tool.argv"),
        returncode=returncode,
        timed_out=_boolean(value.get("timed_out"), "cache tool.timed_out"),
        truncated=_boolean(value.get("truncated"), "cache tool.truncated"),
        error=_string(value.get("error"), "cache tool.error"),
    )


def _finding(payload: Any) -> Finding:
    value = _mapping(payload, "cache finding")
    suppression_value = _mapping(value.get("suppression"), "cache finding.suppression")
    metrics_value = _mapping(value.get("metrics"), "cache finding.metrics")
    metrics: dict[str, FindingMetric] = {}
    for name, metric_payload in metrics_value.items():
        metric = _mapping(metric_payload, f"cache finding metric {name}")
        metric_number = metric.get("value")
        if isinstance(metric_number, bool) or not isinstance(metric_number, (int, float)):
            raise CacheEntryError("cache finding metric value must be numeric")
        metrics[name] = FindingMetric(
            value=metric_number,
            unit=_string(metric.get("unit"), "cache finding metric unit"),
        )
    related = _sequence(value.get("related_locations"), "cache related locations")
    return Finding(
        rule_id=_string(value.get("rule_id"), "cache finding.rule_id", nonempty=True),
        category=FindingCategory(_string(value.get("category"), "cache finding.category")),
        severity=FindingSeverity(_string(value.get("severity"), "cache finding.severity")),
        confidence=FindingConfidence(_string(value.get("confidence"), "cache finding.confidence")),
        fingerprint=_require_digest(value.get("fingerprint"), "cache finding fingerprint"),
        primary_location=_location(value.get("primary_location"), "cache primary location"),
        related_locations=[_location(item, "cache related location") for item in related],
        message=_string(value.get("message"), "cache finding.message"),
        explanation=_string(value.get("explanation"), "cache finding.explanation"),
        remediation=_string(value.get("remediation"), "cache finding.remediation"),
        tool_rule_id=_string(value.get("tool_rule_id"), "cache finding.tool_rule_id"),
        tool_name=_string(value.get("tool_name"), "cache finding.tool_name"),
        tool_version=_string(value.get("tool_version"), "cache finding.tool_version"),
        suppression=FindingSuppression(
            suppressed=_boolean(
                suppression_value.get("suppressed"), "cache suppression.suppressed"
            ),
            kind=SuppressionKind(_string(suppression_value.get("kind"), "cache suppression.kind")),
            reason=_string(suppression_value.get("reason"), "cache suppression.reason"),
        ),
        metrics=metrics,
        snippet=_string(value.get("snippet"), "cache finding.snippet"),
    )


def _support(payload: Any) -> SupportMatrix | None:
    if payload is None:
        return None
    value = _mapping(payload, "cache support matrix")
    entries: list[EngineSupport] = []
    for item in _sequence(value.get("entries"), "cache support entries"):
        entry = _mapping(item, "cache support entry")
        active_mode = entry.get("active_mode")
        fallback_mode = entry.get("fallback_mode")
        entries.append(
            EngineSupport(
                engine_name=_string(entry.get("engine_name"), "cache support engine"),
                language=SupportLanguage(_string(entry.get("language"), "cache support language")),
                mode=AnalysisMode(_string(entry.get("mode"), "cache support mode")),
                active_mode=(
                    AnalysisMode(_string(active_mode, "cache active mode"))
                    if active_mode is not None
                    else None
                ),
                applicable=_boolean(entry.get("applicable"), "cache support applicable"),
                enabled=_boolean(entry.get("enabled"), "cache support enabled"),
                evidence=EvidenceState(_string(entry.get("evidence"), "cache support evidence")),
                confidence=FindingConfidence(
                    _string(entry.get("confidence"), "cache support confidence")
                ),
                frameworks=_string_list(entry.get("frameworks"), "cache frameworks"),
                required_tools=_string_list(entry.get("required_tools"), "cache required tools"),
                optional_tools=_string_list(entry.get("optional_tools"), "cache optional tools"),
                fallback_mode=(
                    AnalysisMode(_string(fallback_mode, "cache fallback mode"))
                    if fallback_mode is not None
                    else None
                ),
                limitations=_string_list(entry.get("limitations"), "cache limitations"),
                reason=_string(entry.get("reason"), "cache support reason"),
            )
        )
    return SupportMatrix(
        project_languages=[
            SupportLanguage(_string(item, "cache project language"))
            for item in _sequence(value.get("project_languages"), "cache project languages")
        ],
        project_frameworks=_string_list(
            value.get("project_frameworks"), "cache project frameworks"
        ),
        entries=entries,
    )


def _manifest(payload: Any, project_root: Path) -> ArtifactManifest:
    value = _mapping(payload, "cache artifact manifest")
    if value.get("schema_version") != "ici.artifacts/v1" or value.get("project_root") != ".":
        raise CacheEntryError("cache artifact manifest identity is invalid")
    shadow_value = value.get("shadow_root")
    shadow_root = None
    if shadow_value is not None:
        shadow_text = _string(shadow_value, "cache shadow root", nonempty=True)
        shadow_relative = PurePosixPath(shadow_text)
        if shadow_relative.is_absolute() or ".." in shadow_relative.parts:
            raise CacheEntryError("cache shadow root must be project-relative")
        shadow_root = project_root / shadow_relative
    records = []
    for item in _sequence(value.get("artifacts"), "cache artifacts"):
        record = _mapping(item, "cache artifact")
        size = record.get("size")
        mode = record.get("mode")
        if type(size) is not int or size < 0 or type(mode) is not int or mode < 0:
            raise CacheEntryError("cache artifact size/mode is invalid")
        records.append(
            ArtifactRecord(
                path=_string(record.get("path"), "cache artifact path", nonempty=True),
                scope=ArtifactScope(_string(record.get("scope"), "cache artifact scope")),
                kind=_string(record.get("kind"), "cache artifact kind", nonempty=True),
                sha256=_require_digest(record.get("sha256"), "cache artifact digest"),
                size=size,
                mode=mode,
                producer=_string(record.get("producer"), "cache artifact producer", nonempty=True),
            )
        )
    manifest = ArtifactManifest(
        project_root=project_root,
        shadow_root=shadow_root,
        variant=BuildVariant(_string(value.get("variant"), "cache artifact variant")),
        source_commit=_string(
            value.get("source_commit"), "cache artifact source commit", nonempty=True
        ),
        config_digest=_require_digest(value.get("config_digest"), "cache artifact config digest"),
        toolchain_digest=_require_digest(
            value.get("toolchain_digest"), "cache artifact toolchain digest"
        ),
        artifacts=tuple(records),
    )
    return manifest.validate()


def _decode_result(payload: Any, project_root: Path) -> EngineResult:
    value = _mapping(payload, "cache result")
    if value.get("schema_version") != "ici.result/v3":
        raise CacheEntryError("cache result schema is unsupported")
    return EngineResult(
        engine_name=_string(value.get("engine_name"), "cache engine name", nonempty=True),
        status=EngineStatus(_string(value.get("status"), "cache result status")),
        summary=_string(value.get("summary"), "cache result summary"),
        score=_number(value.get("score"), "cache result score", nullable=True),
        max_score=_number(value.get("max_score"), "cache result max score", nullable=True),
        duration=_number(value.get("duration"), "cache result duration") or 0.0,
        targets=[_target(item) for item in _sequence(value.get("targets"), "cache targets")],
        raw_output=_string(value.get("raw_output"), "cache raw output"),
        extra=_mapping(value.get("extra"), "cache extra"),
        required=_boolean(value.get("required"), "cache required"),
        evidence=EvidenceState(_string(value.get("evidence"), "cache evidence")),
        tool_evidence=[
            _tool(item) for item in _sequence(value.get("tool_evidence"), "cache tool evidence")
        ],
        findings=[_finding(item) for item in _sequence(value.get("findings"), "cache findings")],
        support_matrix=_support(value.get("support_matrix")),
        artifact_manifests=tuple(
            _manifest(item, project_root)
            for item in _sequence(value.get("artifact_manifests"), "cache artifact manifests")
        ),
    )


def _decode_entry(
    payload: Any,
    key: AnalysisCacheKey,
    project_root: Path,
) -> EngineResult:
    value = _mapping(payload, "cache entry")
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CacheEntryError("cache entry schema is unsupported")
    if value.get("key") != key.digest or value.get("inputs") != key.payload():
        raise CacheEntryError("cache entry identity does not match its key")
    created_at = value.get("created_at")
    if type(created_at) is not int or created_at < 0:
        raise CacheEntryError("cache entry timestamp is invalid")
    result = _decode_result(value.get("result"), project_root)
    if result.engine_name != key.engine_name or not is_cacheable_result(result, key):
        raise CacheEntryError("cache result does not match its engine identity")
    return result


def _validate_inventory_entry(payload: Any, path: Path) -> None:
    value = _mapping(payload, "cache inventory entry")
    if value.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise CacheEntryError("cache inventory schema is unsupported")
    key = _require_digest(value.get("key"), "cache inventory key")
    if path.stem != key.removeprefix(_DIGEST_PREFIX):
        raise CacheEntryError("cache inventory filename does not match key")
    inputs = _mapping(value.get("inputs"), "cache inventory inputs")
    if inputs.get("key_version") != CACHE_KEY_VERSION:
        raise CacheEntryError("cache inventory key version is unsupported")
    _mapping(value.get("result"), "cache inventory result")
