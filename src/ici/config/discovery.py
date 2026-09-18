"""Finding the file to read, and refusing to guess which workspace you meant.

SPEC-01 section 2 spends most of its length on things discovery must *not* do,
and each one is a way a run can silently analyse the wrong thing:

- a ``[component]`` file found on its own is **not** promoted to a workspace.
  Promoting it would make "did the workspace pass" depend on which directory
  someone happened to be standing in.
- a child ``ici.toml`` the root never registered is **not** merged in. Merging
  it would let a file appear in a directory and quietly join the build.
- a nested workspace is **not** absorbed into the one above it.
- the search stops at the VCS root, so a stray ``ici.toml`` in a home directory
  cannot capture a project.

An explicit ``--config`` naming a component file is allowed, and is the one case
that produces a single-component run — recorded as ``STANDALONE`` so it can
never be read as its parent workspace passing.

This module is the only part of the config layer that touches the filesystem.
Everything it finds is handed to the pure readers, which is what keeps the
schema and composition testable from strings.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import tomli

from ici.config.composition import EffectiveConfig, compose, compose_standalone
from ici.config.documents import ComponentDocument, RootDocument
from ici.config.errors import ConfigProblem, NextConfigError, fail
from ici.config.origin import Origin
from ici.config.overlay import read_local
from ici.config.schema import read_component, read_root

__all__ = [
    "CONFIG_FILENAME",
    "Discovery",
    "discover",
    "find_workspace_root",
    "load",
]

CONFIG_FILENAME = "ici.toml"

# A directory holding one of these is the top of a checkout. The search stops
# there so that a file above it — in a home directory, or a parent of several
# unrelated projects — cannot capture a workspace it knows nothing about.
_VCS_MARKERS = (".git", ".hg", ".svn")


@dataclass(frozen=True)
class Discovery:
    """Which file was chosen, and whether it is a whole workspace."""

    path: Path
    is_workspace: bool
    searched: tuple[Path, ...] = ()
    stopped_at: Path | None = None

    @property
    def directory(self) -> Path:
        return self.path.parent


def discover(start: Path, *, explicit: Path | None = None) -> Discovery:
    """Choose the configuration file for a run.

    ``explicit`` is ``--config``. It is taken as given, including when it names
    a component file, because naming a file is an unambiguous statement about
    which one you meant — unlike a search, which has to guess.
    """

    if explicit is not None:
        return _explicit(explicit)

    start = start.resolve()
    searched: list[Path] = []
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        searched.append(candidate)
        if candidate.is_file() and _declares_workspace(candidate):
            return Discovery(path=candidate, is_workspace=True, searched=tuple(searched))
        if _is_vcs_root(directory):
            return _not_found(start, tuple(searched), stopped_at=directory)
    return _not_found(start, tuple(searched), stopped_at=None)


def find_workspace_root(start: Path) -> Path | None:
    """The directory whose root file declares a workspace, or ``None``.

    Same search and stop rules as :func:`discover` — a directory's file that
    does not declare ``[workspace]`` is passed over, and the walk stops at the
    checkout root — but nothing is demanded: no file, or no workspace above
    ``start``, is a ``None`` rather than an error. The caller is choosing a
    path, not requiring one.
    """

    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file() and _declares_workspace_strict(candidate):
            return directory
        if _is_vcs_root(directory):
            return None
    return None


def _declares_workspace_strict(path: Path) -> bool:
    """Whether a file is parseable *and* declares ``[workspace]``.

    Unlike :func:`_declares_workspace` — which treats a malformed file as a
    candidate so the reader can name its real problem — this answers "is this
    certainly a next-schema root". An undecodable or unparseable file is not:
    the caller is choosing a dispatch path, and a broken file gets a better
    error from the stable reader than from being claimed by the next path.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        return "workspace" in tomli.loads(text)
    except Exception:
        # Any failure to interpret the file — malformed TOML, the integer
        # digit limit, pathological nesting — means it cannot be claimed as a
        # next-schema root. "Not certainly a workspace" is the safe answer:
        # the caller falls through to the stable reader, which reports the
        # file's actual problem.
        return False


def _is_vcs_root(directory: Path) -> bool:
    """Whether this directory is the top of a checkout.

    The search stops here so a stray ici.toml above a project — in a home
    directory, or a parent holding several unrelated checkouts — cannot capture
    a workspace that knows nothing about it.
    """

    return any((directory / marker).exists() for marker in _VCS_MARKERS)


def _explicit(path: Path) -> Discovery:
    if not path.is_file():
        fail([ConfigProblem(f"no such file: {path}", Origin(file=str(path)))])
    return Discovery(path=path, is_workspace=_declares_workspace(path), searched=(path,))


def _not_found(start: Path, searched: tuple[Path, ...], *, stopped_at: Path | None) -> Discovery:
    """Report what was looked at, including the component files passed over.

    Naming a component file that was seen and not used is the difference
    between "there is no config here" and "the config you are thinking of is
    not registered anywhere" — two problems with different fixes.
    """

    unregistered = [path for path in searched if path.is_file()]
    detail = (
        f"no {CONFIG_FILENAME} with a [workspace] table above {start}"
        if not unregistered
        else (
            f"found {CONFIG_FILENAME} above {start} but none declares a [workspace]: "
            + ", ".join(str(path) for path in unregistered)
        )
    )
    hint = (
        "a component file is registered by a root, not promoted to one; "
        "pass --config to run it on its own"
        if unregistered
        else "run ici init to create one"
    )
    if stopped_at is not None:
        detail += f" (the search stopped at the checkout root {stopped_at})"
    fail([ConfigProblem(detail, Origin(file=str(start)), hint=hint)])


def _declares_workspace(path: Path) -> bool:
    """Whether a file is a root, read without committing to the whole schema.

    Deliberately tolerant: a file that is malformed should be reported by the
    reader, with its key and origin, rather than being skipped here and turned
    into "no workspace found" three directories later.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # Cannot even open it. Nothing useful to say about its contents, so the
        # search goes on rather than stopping on a file nobody can read.
        return False
    except UnicodeDecodeError:
        # The file exists but is not UTF-8 — malformed, not absent. The reader
        # below reports the decode problem on the file that has it, which is
        # what returning True here is for (see the TOMLDecodeError branch).
        return True
    try:
        return "workspace" in tomli.loads(text)
    except Exception:
        # A file that is here and unparseable is a candidate, not an absence.
        # Returning False sends the search past it and the user is eventually
        # told "none declares a [workspace]" about a file whose [workspace] is
        # right there and whose real problem is a syntax error three lines up.
        # (TOMLDecodeError, its ValueError siblings like the integer digit
        # limit, and pathological nesting all land here.)
        return True


def load(
    start: Path,
    *,
    explicit: Path | None = None,
    local: Path | None = None,
) -> EffectiveConfig:
    """Discover, read every file it names, and compose them."""

    found = discover(start, explicit=explicit)
    overlay = read_local(local.read_text(encoding="utf-8"), path=str(local)) if local else {}

    if not found.is_workspace:
        document = read_component(found.path.read_text(encoding="utf-8"), path=str(found.path))
        return compose_standalone(
            document, component_id=found.directory.name, environment=os.environ
        )

    root = read_root(found.path.read_text(encoding="utf-8"), path=str(found.path))
    return compose(root, _children(root, found, os.environ), local=overlay, environment=os.environ)


def _children(
    root: RootDocument, found: Discovery, environment: Mapping[str, str]
) -> dict[str, ComponentDocument]:
    """Read exactly the files the root registered, and no others.

    A reference is a declared path, so it goes through the same contract as
    every other path value — ``${env:NAME}`` substitutes, ``..`` is refused by
    the resolver, and the file must still land inside the workspace.
    """

    problems: list[ConfigProblem] = []
    children: dict[str, ComponentDocument] = {}
    base = PurePosixPath(str(found.directory.resolve()))
    for reference in root.references:
        resolved = reference.config.resolve(
            declaring_directory=base, environment=environment, problems=problems
        )
        try:
            resolved.relative_to(base)
        except ValueError:
            problems.append(
                ConfigProblem(
                    f"component config escapes the workspace: {reference.config.raw!r}",
                    reference.config.origin,
                    hint="a registered child lives inside the workspace",
                )
            )
            continue
        path = Path(str(resolved))
        if not path.is_file():
            problems.append(
                ConfigProblem(
                    f"no such file: {path}",
                    reference.config.origin,
                    hint=f"{reference.id.value} is registered here but the file is missing",
                )
            )
            continue
        children[reference.id.value] = read_component(
            path.read_text(encoding="utf-8"), path=str(path)
        )
    if problems:
        raise NextConfigError(tuple(problems))
    return children
