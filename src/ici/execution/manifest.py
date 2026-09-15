"""What a task produced, published so that seeing it means it is all there.

The acceptance criterion ends with *"부분 artifact를 거부한다"*, and a partial
artifact is not a path problem: the path is right, the file is there, and a
reader that checks whether the file exists gets a yes. That is the same shape as
everything else in #205 — **presence read as completeness** — and it is why a
manifest records a size and a digest rather than a list of names. A name proves
a write started. A digest proves it finished.

Two rules carry that:

**Nothing appears under its real name until all of it is there.** The manifest
is written into the staging area beside its destination, flushed to the disk
rather than to the kernel's opinion of the disk, and then renamed over. A reader
sees the old manifest or the new one. It cannot see half of one, which is what
writing in place gives you the moment a task is cancelled mid-write.

**A run that did not answer is not promoted.** #205 item 4 says failed and
cancelled results do not become a normal cache entry, and the rule is written
against :class:`~ici.execution.process.Interpretation`, not against the exit
code: a linter that exits 1 *because it found violations* answered the question
it was asked, and refusing to cache that would throw away a real result. What is
refused is a run that did not get to the end, and a run whose tool failed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ici.execution.outputs import OutputRoot
from ici.execution.process import ExitContract, TaskOutcome

__all__ = [
    "MANIFEST_SCHEMA",
    "Artifact",
    "MalformedManifest",
    "Manifest",
    "NotPromotable",
    "Verification",
    "describe",
    "publish",
    "read",
]

_LOGGER = logging.getLogger(__name__)

MANIFEST_SCHEMA = "ici.next.task-manifest/v1"

_DIGEST = "sha256"
_CHUNK = 1024 * 1024


class NotPromotable(RuntimeError):
    """This run's output must not become a normal cache entry."""


class MalformedManifest(ValueError):
    """A manifest on disk is not one this version can read."""


def _text(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise MalformedManifest(f"{key} must be text, got {type(value).__name__}")
    return value


def _whole_number(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    # bool is an int in Python and never a size or an exit code.
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedManifest(f"{key} must be a whole number, got {type(value).__name__}")
    return value


@dataclass(frozen=True)
class Artifact:
    """One produced file, described well enough to tell a short one apart."""

    path: str
    size: int
    digest: str

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "size": self.size, "digest": self.digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Artifact:
        return cls(
            path=_text(data, "path"),
            size=_whole_number(data, "size"),
            digest=_text(data, "digest"),
        )


@dataclass(frozen=True)
class Manifest:
    """The record a later reader trusts instead of trusting the directory.

    ``run`` and ``identity`` are #209's provenance: which run produced this,
    and which input identity it answered. An artifact without them still
    verifies as *intact* — provenance is what lets a reader tell "intact" from
    "the right answer to this run's question".
    """

    task: str
    outcome: str
    exit_code: int
    interpretation: str
    artifacts: tuple[Artifact, ...] = ()
    run: str = ""
    identity: str = ""
    schema: str = MANIFEST_SCHEMA

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": self.schema,
            "task": self.task,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "interpretation": self.interpretation,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
        }
        if self.run:
            payload["run"] = self.run
        if self.identity:
            payload["identity"] = self.identity
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> Manifest:
        """Read a manifest, saying what is wrong with one that is not.

        This parses a file, and a file can be truncated, hand-edited or written
        by a different version. Reaching into it and letting a TypeError out
        would report a damaged manifest as a crash in whoever opened it.
        """

        schema = data.get("schema")
        if schema != MANIFEST_SCHEMA:
            raise MalformedManifest(f"{schema!r} is not {MANIFEST_SCHEMA}")
        listed = data.get("artifacts", [])
        if not isinstance(listed, list):
            raise MalformedManifest(f"artifacts must be a list, got {type(listed).__name__}")
        artifacts = []
        for index, item in enumerate(listed):
            if not isinstance(item, Mapping):
                raise MalformedManifest(
                    f"artifact {index} must be a table, got {type(item).__name__}"
                )
            artifacts.append(Artifact.from_dict(item))
        return cls(
            task=_text(data, "task"),
            outcome=_text(data, "outcome"),
            exit_code=_whole_number(data, "exit_code"),
            interpretation=_text(data, "interpretation"),
            artifacts=tuple(artifacts),
            run=str(data.get("run", "")),
            identity=str(data.get("identity", "")),
            schema=MANIFEST_SCHEMA,
        )


@dataclass(frozen=True)
class Verification:
    """Whether what is on disk is still what the manifest said it was."""

    missing: tuple[str, ...] = ()
    altered: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.missing and not self.altered

    def __str__(self) -> str:
        if self.is_complete:
            return "every artifact is present and unchanged"
        parts = []
        if self.missing:
            parts.append(f"missing: {', '.join(self.missing)}")
        if self.altered:
            parts.append(f"altered: {', '.join(self.altered)}")
        return "; ".join(parts)


def digest_of(target: Path) -> str:
    """The digest of a file, read in chunks so a large artifact is not loaded."""

    hasher = hashlib.new(_DIGEST)
    with target.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return f"{_DIGEST}:{hasher.hexdigest()}"


def describe(
    root: OutputRoot,
    outcome: TaskOutcome,
    produced: tuple[str, ...] = (),
    contract: ExitContract | None = None,
    *,
    run_id: str = "",
    identity: str = "",
) -> Manifest:
    """Record what a task produced, refusing a run that is not an answer.

    Measured here rather than taken on trust: the size and digest come from
    reading the files, so a manifest cannot describe an artifact that was never
    finished being written.
    """

    interpretation = (contract or ExitContract()).read(outcome)
    if not interpretation.is_an_answer:
        raise NotPromotable(
            f"{outcome.spec.name} {outcome.outcome.value}"
            f"{': ' + outcome.detail if outcome.detail else ''} — not a cacheable result"
        )
    artifacts = []
    for name in produced:
        target = root.resolve(name)
        if not target.is_file():
            raise NotPromotable(f"{name} was listed as produced but is not there")
        artifacts.append(Artifact(path=name, size=target.stat().st_size, digest=digest_of(target)))
    return Manifest(
        task=outcome.spec.name,
        outcome=outcome.outcome.value,
        exit_code=outcome.exit_code,
        interpretation=interpretation.value,
        artifacts=tuple(artifacts),
        run=run_id,
        identity=identity,
    )


def publish(manifest: Manifest, destination: Path) -> Path:
    """Write ``manifest`` so that a reader sees all of it or none of it."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    body = manifest.to_json().encode("utf-8")
    # Same directory as the destination, so os.replace is a rename within one
    # filesystem and therefore atomic. A staging area elsewhere would turn this
    # into a copy, which is exactly the half-written file being avoided.
    handle, staged_path = _staged_file(destination.parent)
    renamed = False
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
            stream.flush()
            # Without this the rename can land before the bytes do, and a
            # machine that loses power keeps the name and loses the content.
            os.fsync(stream.fileno())
        os.replace(staged_path, destination)
        renamed = True
    finally:
        # Covers every way out without catching any of them. A staged file left
        # behind is litter inside the output root, which is the one place a
        # later reader looks.
        if not renamed:
            staged_path.unlink(missing_ok=True)
    _sync_directory(destination.parent)
    return destination


def read(destination: Path) -> Manifest:
    """Read a published manifest."""

    return Manifest.from_dict(json.loads(destination.read_text(encoding="utf-8")))


def verify(manifest: Manifest, root: OutputRoot) -> Verification:
    """Re-read every artifact and report what no longer matches.

    A file that is gone and a file that is short are different problems with
    the same cause: a reader that trusted the directory listing.
    """

    missing: list[str] = []
    altered: list[str] = []
    for artifact in manifest.artifacts:
        try:
            target = root.resolve(artifact.path)
        except ValueError:
            missing.append(artifact.path)
            continue
        if not target.is_file():
            missing.append(artifact.path)
            continue
        if target.stat().st_size != artifact.size or digest_of(target) != artifact.digest:
            altered.append(artifact.path)
    return Verification(missing=tuple(missing), altered=tuple(altered))


def _staged_file(directory: Path) -> tuple[int, Path]:
    """Create the file the manifest is written into before it gets its name.

    Deliberately not mkstemp. mkstemp creates the file 0600 and os.replace
    keeps the mode, so the published manifest came out readable only by the
    user who ran the task — private, in the shared build root that exists so
    other people can read it. Opening with 0o666 lets the kernel apply the
    umask, which is what any other file the task writes gets, so the manifest
    is never more private than the artifacts it describes.
    """

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    while True:
        staged = directory / f".manifest-{secrets.token_hex(8)}"
        try:
            return os.open(staged, flags, 0o666), staged
        except FileExistsError:  # pragma: no cover - 8 random bytes colliding
            continue


def _sync_directory(directory: Path) -> bool:
    """Make the rename itself durable, and say whether that could be done.

    Not every platform lets a directory be opened, and not every filesystem
    honours the sync. The answer is returned rather than swallowed: the rename
    is already atomic for a reader, so failing here costs durability across a
    power cut, not correctness, and a caller that cares can tell the difference.
    """

    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError as error:  # pragma: no cover - Windows cannot open a directory
        _LOGGER.debug("cannot open %s to sync it: %s", directory, error)
        return False
    try:
        os.fsync(handle)
    except OSError as error:  # pragma: no cover - some filesystems refuse
        _LOGGER.debug("cannot sync %s: %s", directory, error)
        return False
    finally:
        os.close(handle)
    return True
