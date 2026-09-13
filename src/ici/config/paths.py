"""The three kinds of string that look like a path but are not the same thing.

SPEC-01 section 3 draws two lines that the current loader does not, and both
were drawn because of a way a project can silently analyse the wrong files:

1. A **declared path** (``root``, ``config``, a build ``project`` or
   ``directory``, a tool ``config``) is relative to *the directory of the file
   that declared it*. Splitting a root file into child files must not change
   what any path means, and it only stays true if the anchor travels with the
   declaration.
2. A **source glob** (``sources``, ``include``, ``exclude``, ``test_paths``) is
   relative to *the component root*. A component's root is where its sources
   are; anchoring its globs to the file that happened to declare them would
   make ``**/*.py`` mean different sets in a root file and a child file.

The third kind is an **executable**. A bare ``python`` or ``qmake`` is a PATH
lookup; anything containing a separator is a path, anchored like (1). The spec
is explicit that "실행 cwd가 달라도 선택 결과가 바뀌지 않아야 한다" — running from a
subdirectory must not change which interpreter is chosen.

Nothing here touches the filesystem or reads ``os.getcwd()``. Resolution takes
the anchor as an argument, which is what makes the cwd-independence testable
rather than merely intended.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from ici.config.errors import ConfigProblem
from ici.config.origin import Origin

__all__ = [
    "DeclaredPath",
    "Executable",
    "SourceGlob",
    "substitute_environment",
]

# Only this form, and only in path-valued fields. SPEC-01 section 3 rules out
# shell expansion and command substitution, so $VAR, ${VAR}, $(cmd) and `cmd`
# are all left alone as literal text rather than quietly interpreted.
_ENVIRONMENT = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


def substitute_environment(
    raw: str,
    *,
    origin: Origin,
    environment: Mapping[str, str],
    problems: list[ConfigProblem],
) -> str:
    """Replace ``${env:NAME}`` occurrences, recording missing names.

    A missing variable yields the raw text unchanged plus a problem, so that one
    run reports every missing variable instead of stopping at the first.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environment:
            problems.append(
                ConfigProblem(
                    f"environment variable {name} is not set",
                    origin,
                    hint="set it, or write the path literally",
                )
            )
            return match.group(0)
        return environment[name]

    return _ENVIRONMENT.sub(replace, raw)


def _reject_absolute_or_escaping(
    path: PurePosixPath, *, origin: Origin, problems: list[ConfigProblem]
) -> None:
    if path.is_absolute():
        problems.append(
            ConfigProblem(
                "an absolute path is not portable between checkouts",
                origin,
                hint="write it relative to this file, or use ${env:NAME}",
            )
        )


@dataclass(frozen=True)
class DeclaredPath:
    """A path anchored to the directory of the file that declared it."""

    raw: str
    origin: Origin

    def resolve(
        self,
        *,
        declaring_directory: PurePosixPath,
        environment: Mapping[str, str],
        problems: list[ConfigProblem],
    ) -> PurePosixPath:
        """Anchor this path, substituting environment references first.

        ``declaring_directory`` must be absolute. That is not a convenience
        check: the whole property this type exists to guarantee is that the
        answer does not depend on where the process was started, and a relative
        anchor would quietly reintroduce that dependency.
        """

        if not declaring_directory.is_absolute():
            raise ValueError("declaring directory must be absolute to anchor a declared path")
        substituted = substitute_environment(
            self.raw, origin=self.origin, environment=environment, problems=problems
        )
        candidate = PurePosixPath(substituted)
        if candidate.is_absolute():
            _reject_absolute_or_escaping(candidate, origin=self.origin, problems=problems)
            return candidate
        return _normalise(declaring_directory / candidate)


@dataclass(frozen=True)
class SourceGlob:
    """A glob anchored to its component's root, wherever it was declared."""

    raw: str
    origin: Origin

    def __post_init__(self) -> None:
        if not self.raw:
            raise ValueError("a source glob must not be empty")

    def resolve(self, *, component_root: PurePosixPath) -> str:
        """The glob as a pattern relative to the workspace.

        Returned as text rather than a path: a glob is a pattern, and turning
        ``**/*.cpp`` into a PurePath invites someone downstream to treat it as a
        filename. Expanding it is an adapter's job — this layer never reads the
        filesystem.
        """

        if PurePosixPath(self.raw).is_absolute():
            return self.raw
        return str(_normalise(component_root / self.raw))


@dataclass(frozen=True)
class Executable:
    """A program to run: either a PATH lookup or a path to anchor."""

    raw: str
    origin: Origin

    def __post_init__(self) -> None:
        if not self.raw:
            raise ValueError("an executable must not be empty")

    @property
    def searches_path(self) -> bool:
        """Whether this name is looked up on PATH rather than anchored.

        SPEC-01 section 3 splits on the separator alone: ``python`` is a lookup,
        ``./python`` and ``.venv/bin/python`` are paths. No heuristic about
        whether the file happens to exist, because that would make the meaning
        of a config depend on the machine reading it.
        """

        return "/" not in self.raw

    def as_declared_path(self) -> DeclaredPath:
        """This executable as a path, for the non-PATH case."""

        if self.searches_path:
            raise ValueError(f"{self.raw} is a PATH lookup, not a path")
        return DeclaredPath(raw=self.raw, origin=self.origin)


def _normalise(path: PurePosixPath) -> PurePosixPath:
    """Collapse ``.`` and ``..`` textually, without asking the filesystem.

    ``Path.resolve`` would follow symlinks and consult the disk, which this
    layer must not do — and which would make the result depend on the machine.
    """

    parts: list[str] = []
    for part in path.parts:
        if part == ".":
            continue
        if part == ".." and parts and parts[-1] not in ("..", "/"):
            parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts) if parts else PurePosixPath(".")
