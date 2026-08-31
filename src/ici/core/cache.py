"""Deterministic, local-only cache for completed engine results."""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from ici.core.cache_codec import (
    decode_entry as _decode_entry,
)
from ici.core.cache_codec import (
    read_cache_json as _read_cache_json,
)
from ici.core.cache_codec import (
    serialize_cache_result as _serialize_cache_result,
)
from ici.core.cache_codec import (
    validate_inventory_entry as _validate_inventory_entry,
)
from ici.core.cache_identity import (
    _DIGEST_PREFIX,
    CACHE_KEY_VERSION,
    CACHE_SCHEMA_VERSION,
    AnalysisCacheKey,
    CacheEntryError,
    _require_digest,
    build_analysis_cache_key,
    default_cache_dir,
    is_cacheable_result,
    project_source_digest,
)
from ici.core.models import EngineResult
from ici.reporters.json_rep import serialize_engine_result

MAX_CACHE_ENTRY_BYTES = 32 * 1024 * 1024

__all__ = [
    "CACHE_KEY_VERSION",
    "CACHE_SCHEMA_VERSION",
    "MAX_CACHE_ENTRY_BYTES",
    "AnalysisCache",
    "AnalysisCacheKey",
    "CacheEntryError",
    "CacheInventory",
    "build_analysis_cache_key",
    "default_cache_dir",
    "is_cacheable_result",
    "project_source_digest",
]


@dataclass(frozen=True)
class CacheInventory:
    """Small, non-sensitive summary shown by ``ici cache``."""

    root: Path
    entries: int
    bytes: int
    corrupt_entries: int


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
            payload = _read_cache_json(path, MAX_CACHE_ENTRY_BYTES)
            result = _decode_entry(payload, key, project_root.resolve(strict=False))
            serialize_engine_result(result, project_root=project_root)
        except (
            CacheEntryError,
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            RecursionError,
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
                payload = _read_cache_json(path, MAX_CACHE_ENTRY_BYTES)
                _validate_inventory_entry(payload, path)
            except (CacheEntryError, OSError, UnicodeError, ValueError, RecursionError):
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
