"""The observation cache — evidence kept across runs, with the receipt kept too.

#209's shape, and the two rules that make it honest rather than fast:

**What is stored is the observation, not the verdict.** A cached entry is the
task's raw evidence — findings, measurements, exit state — and the gate
re-judges it under the *current* policy every time. Caching the verdict would
let a threshold change inherit the old run's answer, which is exactly the
"policy changed but the result didn't" failure the spec names.

**A miss is a reason, not an error.** ``read`` never raises for a cache-shaped
problem: absent, corrupt, tampered, or unpromotable all come back as a miss
with the reason attached, because the difference between "nothing stored" and
"something stored that could not be trusted" is the difference between a slow
run and a wrong one.

An entry is not adopted unless the stored identity matches what this run would
hash — the key is computed by the caller from the task's declared inputs, so a
stale entry can only be *found*, never trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from ici.domain._codec import dumps
from ici.domain.observation import Observation
from ici.domain.serialization import observation_from_dict, observation_to_dict
from ici.execution.locks import exclusive

__all__ = ["CacheRead", "ObservationCache"]

CACHE_SCHEMA = "ici.next.observation-cache/v1"

#: Entries carry a serialisation version: a schema this build cannot read is a
#: miss with a reason, not a crash in whoever opened the file.
_MAX_ENTRIES = 512


@dataclass(frozen=True)
class CacheRead:
    """What a lookup found — the observation, or why there isn't one to use."""

    hit: bool
    reason: str
    observation: Observation | None = None
    #: The run that produced the stored evidence, for "where did this come
    #: from" diagnostics. Empty on a miss.
    source_run: str = ""


class ObservationCache:
    """A directory of stored observations, each named by the identity of its inputs.

    One file per key, private permissions, atomic publish, kernel-held lock.
    The directory is ici's own — nothing in it is a source or a build output,
    and eviction deletes only files that live directly inside it.
    """

    def __init__(self, directory: Path, *, max_entries: int = _MAX_ENTRIES) -> None:
        self._dir = directory
        self._max = max(1, max_entries)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def read(self, key: str) -> CacheRead:
        """The stored observation for ``key``, or the reason it cannot be used."""

        entry = self._entry(key)
        if not entry.is_file():
            return CacheRead(hit=False, reason="no stored result for these inputs")
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
                raise ValueError("unrecognised cache entry schema")
            identity = payload.get("identity")
            if identity != key:
                # The filename and the stored identity disagree — the entry was
                # written under a different key or tampered with. Either way it
                # cannot stand for this run's inputs.
                self._evict_one(entry)
                return CacheRead(hit=False, reason="stored identity does not match")
            observation_data = payload.get("observation")
            if not isinstance(observation_data, dict):
                raise ValueError("stored result has no observation")
            integrity = payload.get("integrity")
            if not isinstance(integrity, str):
                # Entries from before integrity existed are a miss, not a
                # guess — the cache is disposable, doubt is not.
                self._evict_one(entry)
                return CacheRead(hit=False, reason="stored result carries no integrity digest")
            if _digest(observation_data) != integrity:
                # The observation was modified after it was stored — a poisoned
                # cache must not launder a written-over finding into a pass.
                self._evict_one(entry)
                return CacheRead(hit=False, reason="stored result was modified")
            observation = observation_from_dict(observation_data)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._evict_one(entry)
            return CacheRead(hit=False, reason=f"stored result is not readable: {error}")
        return CacheRead(
            hit=True,
            reason="reused a verified stored result",
            observation=observation,
            source_run=str(payload.get("run", "")),
        )

    def write(self, key: str, observation: Observation, *, run_id: str) -> None:
        """Store an observation, refusing evidence that is not a finished answer.

        A failed, cancelled, truncated or timed-out run produced something —
        just not an answer. #209: partial output is never adopted as a normal
        cache entry, whatever the exit code said.
        """

        if not observation.evidence_is_complete:
            return
        observation_data = observation_to_dict(observation)
        body = {
            "schema": CACHE_SCHEMA,
            "identity": key,
            "run": run_id,
            "stored": int(time.time()),
            "integrity": _digest(observation_data),
            "observation": observation_data,
        }
        with exclusive(self._dir / ".write-lock"):
            _atomic_write(self._entry(key), body)
            self._evict()

    def _entry(self, key: str) -> Path:
        # The key is a hex digest of the caller's inputs — never a user path,
        # so it cannot walk out of the directory.
        safe = "".join(char for char in key if char in "0123456789abcdef")
        return self._dir / f"{safe or 'empty'}.json"

    def _evict_one(self, path: Path) -> None:
        """Drop one entry — only ever a file inside the cache directory."""

        try:
            resolved = path.resolve()
            if resolved.parent != self._dir.resolve() or resolved.is_symlink():
                return
            resolved.unlink(missing_ok=True)
        except OSError:
            return

    def _evict(self) -> None:
        """Bound the cache's size, oldest first.

        LRU by mtime is enough: a cache that grows without bound turns into a
        disk-usage finding about ici itself. Only regular files that live
        directly in the cache directory are candidates — a symlink or a path
        that resolves outside is left alone rather than followed.
        """

        try:
            candidates = [
                item
                for item in self._dir.iterdir()
                if item.name != ".write-lock" and item.is_file() and not item.is_symlink()
            ]
        except OSError:
            return
        excess = len(candidates) - self._max
        if excess <= 0:
            return
        candidates.sort(key=lambda item: item.stat().st_mtime)
        for item in candidates[:excess]:
            self._evict_one(item)


def _digest(payload: dict) -> str:
    """SHA-256 over the observation's canonical serialisation.

    The digest covers what was stored, not who stored it — the cache has no
    secrets to key a MAC with, and the property it needs is only "the bytes a
    later run reads are the bytes an earlier run wrote".
    """

    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()


def _atomic_write(destination: Path, payload: dict) -> Path:
    """Write the entry so a reader sees all of it or none of it.

    Same discipline as ``execution.manifest.publish``: staged in the
    destination's own directory so the rename stays inside one filesystem,
    flushed to disk before the rename, and the staging name removed on every
    failure path. Entries are user-private (0o600) — a cache of what a run saw
    is not shared state other users need.
    """

    staged = destination.parent / f".entry-{secrets.token_hex(8)}"
    handle = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    renamed = False
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, destination)
        renamed = True
    finally:
        if not renamed:
            staged.unlink(missing_ok=True)
    return destination
