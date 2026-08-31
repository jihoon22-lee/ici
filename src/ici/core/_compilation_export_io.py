"""Safe filesystem boundary for standalone compilation exports."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import suppress
from pathlib import Path, PureWindowsPath


def atomic_write(path: Path, encoded: bytes) -> None:
    """Durably replace a validated target without leaving partial output."""

    target = Path(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
        _sync_directory(target.parent)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_output(root: Path, output: str, database_path: str) -> Path | None:
    """Reject outputs that could overwrite an input, policy, or special file."""

    if output == "-":
        return None
    if os.name != "nt" and ("\\" in output or PureWindowsPath(output).drive):
        raise ValueError("--output uses foreign platform path syntax")
    raw_target = Path(output)
    lexical_target = raw_target if raw_target.is_absolute() else root / raw_target
    if not lexical_target.name:
        raise ValueError("the output path does not identify a file")
    target = lexical_target.parent.resolve(strict=False) / lexical_target.name
    database = (root / database_path).resolve(strict=False)
    protected = {
        database,
        *(
            (root / name).resolve(strict=False)
            for name in ("ici.toml", "dev.toml", "pyproject.toml")
        ),
    }

    try:
        target_mode = target.lstat().st_mode
    except FileNotFoundError:
        target_mode = None
    if target_mode is not None and not (stat.S_ISREG(target_mode) or stat.S_ISLNK(target_mode)):
        raise ValueError("--output must identify a regular file or replaceable symbolic link")

    same_protected_file = target.exists() and any(
        candidate.exists() and target.samefile(candidate) for candidate in protected
    )
    if target in protected or same_protected_file:
        raise ValueError("--output must not overwrite the compilation database or project policy")
    return target
