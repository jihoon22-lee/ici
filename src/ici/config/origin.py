"""Where a configuration value came from.

SPEC-01 section 3 requires the source file and key of every final value. The
reason is recorded in the same section as a measured defect: today's loader
merges an XDG file, ``dev.toml`` and the project file with ``_deep_merge``
(``ici/config/__init__.py``), and after the merge **nothing says which file won**.
A user whose quality gate silently changed has no way to find out why.

So origin is not metadata attached later. A value that cannot say where it came
from is not representable here: :class:`Sourced` carries both or neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

__all__ = ["Origin", "Sourced"]

T = TypeVar("T")


@dataclass(frozen=True)
class Origin:
    """One value's source: which file declared it, and under which key.

    ``file`` is kept as the caller gave it rather than resolved, because a
    diagnostic should echo the path the user typed. ``key`` is the dotted path
    into the document, with list positions included — ``components[2].root``,
    not ``components.root`` — since "which component" is the first thing a
    reader needs.
    """

    file: str
    key: str = ""

    def __post_init__(self) -> None:
        if not self.file:
            raise ValueError("origin file must not be empty")

    @property
    def is_document(self) -> bool:
        """Whether this names a whole file rather than a key inside it."""

        return not self.key

    def child(self, key: str) -> Origin:
        """The origin of a key nested inside this one.

        A top-level key reads ``components[0].build`` rather than
        ``<document>.components[0].build``: the message is something a user
        greps their file for, so it has to be what is actually in the file.
        """

        return Origin(file=self.file, key=key if self.is_document else f"{self.key}.{key}")

    def item(self, index: int) -> Origin:
        """The origin of a list entry inside this one."""

        return Origin(file=self.file, key=f"{self.key}[{index}]")

    def __str__(self) -> str:
        return self.file if self.is_document else f"{self.file}: {self.key}"


@dataclass(frozen=True)
class Sourced(Generic[T]):
    """A value together with where it came from.

    Generic rather than one wrapper per type so that composition in PR B can
    keep origins through precedence without knowing what it is carrying.
    """

    value: T
    origin: Origin

    def replace(self, value: object) -> Sourced[object]:
        """The same origin with a different value, for normalisation steps."""

        return Sourced(value=value, origin=self.origin)
