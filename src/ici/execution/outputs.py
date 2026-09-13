"""Where a task is allowed to write, and what "allowed" has to mean.

#205 item 4 says task output goes only inside a permitted run/build root, and
the acceptance criterion names the three ways that is got wrong: *"output path
이탈·symlink·부분 artifact를 거부한다"*. The first two are here; the third is
:mod:`ici.execution.manifest`, because a partial artifact is not a path problem.

The reason a root is a type rather than a convention is that every check below
is one somebody skips exactly once. A relative path is checked for ``..`` and
looks safe; the check that is actually load-bearing is the one after it, because
**a name that stays inside the root can still resolve outside it.** A symlink
placed in the output tree by a previous run, or by the tool itself, turns
``reports/out.json`` into ``/etc/out.json`` with no ``..`` anywhere in it.

So a path is accepted only when it stays inside the root *after* every symlink
on the way to it has been followed, and the root's own realpath is what it is
compared against — a root that is itself reached through a link (``/tmp`` on
macOS) is a root, not an escape.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

__all__ = ["STAGING_DIRECTORY", "OutputRoot", "PathRefused"]

#: Staged work lives inside the root, not in the system temporary directory.
#: Two reasons, and the first is the one that makes it mandatory: an atomic
#: replace needs the staged file on the same filesystem as its destination, and
#: nothing guarantees that of /tmp. The second is that a shared temporary
#: directory is writable by everyone, so the name a task is about to use can be
#: taken, or linked elsewhere, by somebody else first.
STAGING_DIRECTORY = ".ici-staging"


class PathRefused(ValueError):
    """A task asked to write somewhere it is not allowed to write."""


@dataclass(frozen=True)
class OutputRoot:
    """The one directory tree a task may produce files in."""

    path: Path

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise PathRefused(f"an output root must be absolute, got {self.path}")
        object.__setattr__(self, "path", Path(os.path.normpath(self.path)))

    @property
    def real(self) -> Path:
        """The root with its own links followed.

        A root reached through a symlink is still a root. Comparing against the
        unresolved path would refuse every write on a machine where the build
        directory happens to live behind a link.
        """

        return self.path.resolve()

    def resolve(self, relative: str | Path) -> Path:
        """The absolute path for ``relative``, or a refusal saying which rule.

        Refuses an absolute path, a path containing ``..``, a path whose
        components include a symlink, and — the one that catches what the
        others miss — any path that does not land inside the root once
        everything on the way to it has been resolved.
        """

        candidate = Path(relative)
        if candidate.is_absolute():
            raise PathRefused(f"{relative} is absolute; task output is relative to its root")
        parts = candidate.parts
        if not parts:
            raise PathRefused("a task output needs a name")
        if ".." in parts:
            raise PathRefused(f"{relative} climbs out of its root")

        walked = self.path
        for part in parts:
            walked = walked / part
            if walked.is_symlink():
                # Not "resolve it and see": a link inside the output tree is a
                # way for one run to write through another's name, and the
                # target it points at today is not the target it points at
                # when the tool gets there.
                raise PathRefused(f"{relative} goes through the symlink {walked}")

        settled = Path(os.path.normpath(walked))
        # Two checks, each against the root in its own terms. Lexically the name
        # must stay under the root as written; once links are followed it must
        # stay under the root as it really is. Mixing the two refuses every
        # write on a machine whose build directory lives behind a link.
        if not _inside(settled, self.path):
            raise PathRefused(f"{relative} resolves to {settled}, outside {self.path}")
        landing = _resolved_parent(settled)
        if not _inside(landing, self.real):
            raise PathRefused(f"{relative} would be written to {landing}, outside {self.real}")
        return settled

    def prepare(self, relative: str | Path) -> Path:
        """Resolve ``relative`` and make its parent directory exist."""

        target = self.resolve(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    @contextmanager
    def staging(self, prefix: str = "task-") -> Iterator[Path]:
        """A private directory inside the root, removed when the block ends.

        Private because the name is created by ``mkdtemp`` with 0700 and nobody
        else knows it; inside the root because that is what makes the eventual
        rename atomic.
        """

        area = self.path / STAGING_DIRECTORY
        area.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix=prefix, dir=area))
        try:
            yield staged
        finally:
            # A cancelled task leaves its half-written files here rather than
            # under a name a reader would trust, and this is what removes them.
            shutil.rmtree(staged, ignore_errors=True)

    def holds(self, target: Path) -> bool:
        """Whether ``target`` is a path this root would have handed out."""

        try:
            return _inside(Path(os.path.normpath(target)), self.real)
        except OSError:  # pragma: no cover - unreadable path
            return False


def _inside(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def _resolved_parent(target: Path) -> Path:
    """The nearest existing ancestor of ``target``, with links followed.

    The target itself may not exist yet, so it cannot be resolved; its parent
    can, and that is where a link would have to be for the write to land
    somewhere else.
    """

    parent = target.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.resolve()
