"""What the workspace's source globs actually name, hashed once each.

WP09 PR B (#207 items 3 and 6). The model says which files a component *claims*;
this module says which files actually exist, what role they play, and what their
content is — once per file, no matter how many units claim it. That one-read
property is the point: a source shared by two analysis units is read and hashed
one time, and the sharing is recorded as membership rather than by reading it
twice and hoping the copies agree.

Roles are distinguished, not flattened: a file under a build directory is
``generated`` (a prepare step's output is an input, not a source), a file a
component marks ``vendor`` is third-party (analysed but counted differently),
and a declared ``external`` input is read but reported separately so a reader
can tell "the workspace's code" from "headers it was allowed to look at".

Nothing here executes. There is no runner parameter and no subprocess: an
inventory that configured, built or imported the code it was counting would not
be an inventory. This is also what the acceptance criterion means by a snapshot
that proves a run's inputs — reading bytes is the whole job.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from glob import glob
from pathlib import Path, PurePosixPath

from ici.domain._validation import require_digest, require_relative_path, require_text
from ici.domain.workspace import SourceSnapshot, Workspace

__all__ = [
    "ExternalInput",
    "InventoriedFile",
    "InventoryDiff",
    "SourceInventory",
    "SourceRole",
    "diff",
    "take",
]


class SourceRole(str, Enum):
    """What a matched file is to the analysis that reads it."""

    SOURCE = "source"
    VENDOR = "vendor"
    GENERATED = "generated"


@dataclass(frozen=True)
class InventoriedFile:
    """One real file, its content identity, and the units claiming it.

    ``units`` preserves the sharing the model declared: two units naming one
    file produce one entry naming two units, which is what lets an engine read
    it once while still knowing both compilations saw it.
    """

    path: str
    role: SourceRole
    digest: str
    size: int
    units: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", require_relative_path(self.path, "inventoried file"))
        object.__setattr__(self, "digest", require_digest(self.digest, "file digest"))
        if not isinstance(self.role, SourceRole):
            object.__setattr__(self, "role", SourceRole(self.role))
        if not isinstance(self.size, int) or self.size < 0:
            raise ValueError("file size must be a non-negative integer")


@dataclass(frozen=True)
class ExternalInput:
    """A declared read outside the workspace.

    ``present=False`` is a state, not an error: the declaration said this input
    exists and the filesystem disagreed, and hiding that would report the run
    on smaller evidence than was promised.
    """

    path: str
    present: bool
    digest: str | None = None
    files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", require_text(self.path, "external input"))
        if not isinstance(self.present, bool):
            raise ValueError("external input presence must be a boolean")
        if self.digest is not None:
            require_digest(self.digest, "external input digest")
        if not self.present and self.digest is not None:
            raise ValueError("an absent external input cannot carry a digest")


@dataclass(frozen=True)
class SourceInventory:
    """The resolved source scope of a workspace at one moment in time."""

    workspace_id: str
    files: tuple[InventoriedFile, ...]
    externals: tuple[ExternalInput, ...]
    generated_roots: tuple[str, ...]
    unreadable: tuple[str, ...]
    digest: str

    def snapshot(self, *, commit: str | None = None, dirty: bool = False) -> SourceSnapshot:
        """The identity a run result carries, built from what was counted.

        ``commit`` and ``dirty`` come from :mod:`ici.workspace.vcs` — a commit
        id alone does not describe dirty, untracked or generated files, so the
        digest over content is primary and the VCS state is annotation.
        """

        return SourceSnapshot(
            digest=self.digest,
            files=tuple(item.path for item in self.files),
            generated=tuple(item.path for item in self.files if item.role is SourceRole.GENERATED),
            external_inputs=tuple(item.path for item in self.externals),
            commit=commit,
            dirty=dirty,
        )


@dataclass(frozen=True)
class InventoryDiff:
    """What changed between two inventories of the same workspace.

    Run-start vs run-end is the honest check on "the run did not mutate its
    inputs": a written-to source shows up as ``changed``, a cleaned-up
    intermediate as ``removed``.
    """

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    role_changed: tuple[str, ...] = ()
    externals_added: tuple[str, ...] = ()
    externals_removed: tuple[str, ...] = ()

    @property
    def stable(self) -> bool:
        return not (
            self.added
            or self.removed
            or self.changed
            or self.role_changed
            or self.externals_added
            or self.externals_removed
        )


def diff(before: SourceInventory, after: SourceInventory) -> InventoryDiff:
    """Compare two inventories; membership churn is visible, not averaged away."""

    earlier = {item.path: item for item in before.files}
    later = {item.path: item for item in after.files}
    return InventoryDiff(
        added=tuple(sorted(path for path in later if path not in earlier)),
        removed=tuple(sorted(path for path in earlier if path not in later)),
        changed=tuple(
            sorted(
                path
                for path in earlier.keys() & later.keys()
                if earlier[path].digest != later[path].digest
            )
        ),
        role_changed=tuple(
            sorted(
                path
                for path in earlier.keys() & later.keys()
                if earlier[path].role is not later[path].role
            )
        ),
        externals_added=tuple(
            sorted(
                item.path
                for item in after.externals
                if item.path not in {e.path for e in before.externals}
            )
        ),
        externals_removed=tuple(
            sorted(
                item.path
                for item in before.externals
                if item.path not in {e.path for e in after.externals}
            )
        ),
    )


def take(workspace: Workspace, *, root: Path) -> SourceInventory:
    """Resolve every component's globs under ``root`` and hash each match once.

    Globs arrive already anchored workspace-relative by composition, so
    expansion is ``root`` plus pattern — no chdir, no per-file anchoring math
    left to redo. Role precedence is generated > vendor > source: a build
    output matched by a broad glob is still a build output.
    """

    root = Path(root)
    generated_roots = tuple(
        sorted({build.directory for build in workspace.builds if build.directory})
    )
    generated_prefixes = tuple(root / directory for directory in generated_roots)

    claimed: dict[str, set[str]] = {}
    vendor_paths: set[str] = set()
    for component in workspace.components:
        unit_ids = tuple(
            unit.id for unit in workspace.analysis_units if unit.component_id == component.id
        )
        for pattern in (*component.sources, *component.include):
            for path in _expand(root, pattern):
                claimed.setdefault(path, set()).update(unit_ids)
        for pattern in component.exclude:
            for path in _expand(root, pattern):
                claimed.pop(path, None)
        for pattern in component.vendor:
            vendor_paths.update(_expand(root, pattern))
        claimed.update(_externals_inside(component, root, unit_ids))

    unreadable: list[str] = []
    digests: dict[str, tuple[str, int]] = {}
    for path in sorted(claimed):
        physical = root / path
        if not physical.is_file():
            continue
        try:
            digest, size = _hash(physical)
        except OSError:
            unreadable.append(path)
            continue
        digests[path] = (digest, size)

    externals = tuple(
        _external(root, entry) for component in workspace.components for entry in component.external
    )

    items = sorted(
        (
            InventoriedFile(
                path=path,
                role=_role(root / path, path, vendor_paths, generated_prefixes),
                digest=digests[path][0],
                size=digests[path][1],
                units=tuple(sorted(claimed[path])),
            )
            for path in digests
        ),
        key=lambda item: item.path,
    )

    composite = hashlib.sha256(
        "\n".join(f"{item.role.value}\t{item.digest}\t{item.path}" for item in items).encode(
            "utf-8"
        )
    ).hexdigest()
    return SourceInventory(
        workspace_id=workspace.id,
        files=tuple(items),
        externals=externals,
        generated_roots=generated_roots,
        unreadable=tuple(sorted(unreadable)),
        digest=f"sha256:{composite}",
    )


def _expand(root: Path, pattern: str) -> list[str]:
    """Workspace-relative paths matching one glob; absolute patterns allowed.

    Matches outside ``root`` are dropped rather than carried as absolute
    members: a read that leaves the checkout must be declared as ``external``,
    which is the split SPEC-01 section 3 draws between membership and reads.
    """

    base = root.resolve()
    patterns = [pattern]
    # ``third_party/**`` reads as "everything under third_party", but a
    # trailing ``**`` alone matches only directories; spell the file half too.
    if pattern.endswith("/**") or pattern == "**":
        patterns.append(pattern + "/*")
    if PurePosixPath(pattern).is_absolute():
        matches = [Path(path) for spelling in patterns for path in glob(spelling, recursive=True)]
    else:
        matches = [match for spelling in patterns for match in root.glob(spelling)]
    members: list[str] = []
    for match in matches:
        if not match.is_file():
            continue
        try:
            members.append(match.resolve().relative_to(base).as_posix())
        except (OSError, ValueError):
            continue
    return members


def _externals_inside(component, root: Path, unit_ids: tuple[str, ...]) -> dict[str, set[str]]:
    """External entries that resolve inside the workspace are still members."""

    inside: dict[str, set[str]] = {}
    for entry in component.external:
        candidate = Path(entry)
        if not candidate.is_absolute():
            candidate = root / entry
        try:
            relative = candidate.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            inside.setdefault(relative.as_posix(), set()).update(unit_ids)
        elif candidate.is_dir():
            for path in sorted(candidate.rglob("*")):
                if path.is_file():
                    inside.setdefault(path.relative_to(root).as_posix(), set()).update(unit_ids)
    return inside


def _external(root: Path, entry: str) -> ExternalInput:
    """One declared external input, inventoried only as far as it exists."""

    candidate = Path(entry)
    if not candidate.is_absolute():
        candidate = root / entry
    if not candidate.exists():
        return ExternalInput(path=entry, present=False)
    if candidate.is_dir():
        members = tuple(sorted(path.as_posix() for path in candidate.rglob("*") if path.is_file()))
        digest = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()
        return ExternalInput(path=entry, present=True, digest=f"sha256:{digest}", files=members)
    digest, _size = _hash(candidate)
    return ExternalInput(path=entry, present=True, digest=digest, files=(entry,))


def _role(
    physical: Path,
    path: str,
    vendor_paths: set[str],
    generated_prefixes: tuple[Path, ...],
) -> SourceRole:
    for prefix in generated_prefixes:
        try:
            physical.relative_to(prefix)
            return SourceRole.GENERATED
        except ValueError:
            continue
    if path in vendor_paths:
        return SourceRole.VENDOR
    return SourceRole.SOURCE


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return f"sha256:{digest.hexdigest()}", size
