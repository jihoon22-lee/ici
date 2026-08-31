"""Path and bounded-file primitives for compilation database ingestion.

This private module has no compiler or shell side effects.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any


@dataclass(frozen=True)
class _RowError(Exception):
    code: str
    message: str
    source: str = ""


@dataclass(frozen=True)
class _ReadError(Exception):
    code: str
    message: str


def _relative_text(path: Path, root: Path, *, allow_dot: bool = True) -> str:
    relative = path.relative_to(root).as_posix()
    if not relative and allow_dot:
        return "."
    return relative


def _scoped_path(root: Path, base: Path, value: str) -> tuple[str, str, Path]:
    if os.name != "nt" and ("\\" in value or bool(PureWindowsPath(value).drive)):
        raise _RowError(
            "foreign-path-syntax",
            "A compilation path uses foreign platform syntax.",
        )
    candidate = Path(value)
    lexical = candidate if candidate.is_absolute() else base / candidate
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as err:
        raise _RowError("invalid-path", "A compilation path could not be resolved.") from err
    try:
        relative = _relative_text(resolved, root)
    except ValueError:
        return resolved.as_posix(), "external", resolved
    return relative, "project", resolved


def _select_database(root: Path, config: dict[str, Any]) -> tuple[str | None, bool]:
    project = config.get("project", {})
    explicit = project.get("compile_database") if isinstance(project, dict) else None
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit:
            return None, True
        if os.name != "nt" and ("\\" in explicit or bool(PureWindowsPath(explicit).drive)):
            return None, True
        try:
            resolved = (root / explicit).resolve(strict=False)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None, True
        if not relative or relative == ".":
            return None, True
        return relative, True
    for candidate in ("compile_commands.json", "build/compile_commands.json"):
        try:
            if (root / candidate).exists():
                return candidate, False
        except OSError:
            continue
    return None, False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _open_contained(path: Path, root: Path, file_flags: int) -> int:
    """Open a root-relative file without following any intermediate symlink."""

    try:
        relative = path.relative_to(root)
    except ValueError as err:
        raise _ReadError("unreadable", "The file is outside its containment root.") from err
    if not relative.parts:
        raise _ReadError("not-file", "The selected path is not a regular file.")
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_DIRECTORY"):
        return os.open(path, file_flags)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        for part in relative.parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        return os.open(relative.parts[-1], file_flags, dir_fd=current)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_bounded_regular(
    path: Path,
    limit: int,
    *,
    containment_root: Path | None = None,
) -> bytes:
    """Read one stable regular file through a no-follow descriptor."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = (
            _open_contained(path, containment_root, flags)
            if containment_root is not None
            else os.open(path, flags)
        )
    except FileNotFoundError:
        raise
    except OSError as err:
        raise _ReadError("unreadable", "The file could not be opened safely.") from err
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _ReadError("not-file", "The selected path is not a regular file.")
        if before.st_size > limit:
            raise _ReadError("too-large", "The file exceeds the bounded input size.")
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise _ReadError("too-large", "The file exceeds the bounded input size.")
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity or total != after.st_size:
            raise _ReadError("changed", "The file changed while it was being read.")
        return b"".join(chunks)
    except OSError as err:
        raise _ReadError("unreadable", "The file could not be read safely.") from err
    finally:
        os.close(descriptor)
