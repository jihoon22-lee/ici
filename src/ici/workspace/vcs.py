"""The version-control half of a source snapshot: commit, dirty, untracked.

WP09 PR B (#207 item 6). ``SourceSnapshot.commit`` is honest only as long as it
is not asked to be sufficient — a commit id says nothing about files that were
modified, added or never tracked at all, which is why SPEC-02 section 6 refuses
commit-SHA-only cache keys. This module reports all three states separately so
a snapshot can carry ``dirty`` alongside the commit rather than instead of
knowing.

Everything is a query: ``rev-parse HEAD`` and ``status --porcelain`` read the
repository; nothing fetches, checks out or writes. A directory that is not a
repository reports ``None`` — "no commit to name" is information, not an error —
but the snapshot then cannot claim clean, so ``dirty`` comes back ``True``
rather than guessed-false.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

__all__ = ["VcsState", "status"]

_GIT_TIMEOUT = 10

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class VcsState:
    """What version control can say about the tree being analysed.

    ``dirty`` and ``untracked`` are workspace-relative paths; ``dirty`` covers
    staged and unstaged modifications, ``untracked`` the files git has never
    seen. Both are paths rather than flags because "something changed" is only
    actionable when it says where.
    """

    commit: str | None
    dirty: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        """Whether every inventoried path is what the commit says it is."""

        return self.commit is not None and not self.dirty and not self.untracked


def _run(argv: list[str], root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=root, capture_output=True, text=False, timeout=_GIT_TIMEOUT)


def status(root: Path, *, runner: Runner | None = None) -> VcsState | None:
    """Query the repository at ``root``; ``None`` when there isn't one.

    The runner exists for tests — the real one is a plain ``git`` subprocess
    with a short timeout so a wedged filesystem cannot hang the inventory.
    """

    root = Path(root)
    run = runner or _run
    try:
        head = run(["git", "rev-parse", "HEAD"], root)
    except (OSError, subprocess.SubprocessError):
        return None
    if head.returncode != 0:
        return None
    commit = head.stdout.decode("utf-8", "replace").strip() or None

    try:
        listing = run(["git", "status", "--porcelain=v1", "--untracked-files=all", "-z"], root)
    except (OSError, subprocess.SubprocessError):
        return VcsState(commit=commit)
    dirty: list[str] = []
    untracked: list[str] = []
    if listing.returncode == 0:
        for record in listing.stdout.decode("utf-8", "replace").split("\0"):
            if len(record) < 4:
                continue
            marker, path = record[:2], record[3:]
            if marker == "??":
                untracked.append(path)
            else:
                dirty.append(path)
    return VcsState(commit=commit, dirty=tuple(sorted(dirty)), untracked=tuple(sorted(untracked)))
