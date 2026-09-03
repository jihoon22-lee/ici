#!/usr/bin/env python3
"""Safely assemble launcher-prefixed ZipApp outputs.

The builder writes only through opened, non-symlink directory descriptors and
atomically replaces regular output files. Existing symlinks and special files
are rejected before any output is changed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

_MAX_RAW_BYTES = 512 * 1024 * 1024
_MAX_PREAMBLE_BYTES = 64 * 1024


class AssemblyError(RuntimeError):
    """Raised when an input or output violates the assembly boundary."""


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode)


def _read_bounded(stream: BinaryIO, maximum: int, label: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise AssemblyError(f"{label} exceeds the {maximum}-byte limit")


def _read_regular(path: Path, label: str, maximum: int) -> bytes:
    absolute = path.absolute()
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(absolute, flags)
    except OSError as exc:
        raise AssemblyError(f"could not open {label} as a non-symlink file: {exc}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise AssemblyError(f"{label} must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = _read_bounded(stream, maximum, label)
        final = os.fstat(descriptor)
        try:
            named = os.stat(absolute, follow_symlinks=False)
        except OSError as exc:
            raise AssemblyError(f"{label} changed while it was read: {exc}") from exc
        if _identity(initial) != _identity(final) or _identity(final) != _identity(named):
            raise AssemblyError(f"{label} changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def _open_output_parent(path: Path) -> tuple[int, os.stat_result]:
    parent = path.parent.absolute()
    try:
        named = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise AssemblyError(f"could not inspect output directory {parent}: {exc}") from exc
    if not stat.S_ISDIR(named.st_mode):
        raise AssemblyError(f"output parent must be a non-symlink directory: {parent}")
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(parent, flags)
    except OSError as exc:
        raise AssemblyError(f"could not open output directory {parent}: {exc}") from exc
    opened = os.fstat(descriptor)
    if _directory_identity(named) != _directory_identity(opened):
        os.close(descriptor)
        raise AssemblyError(f"output directory changed while it was opened: {parent}")
    return descriptor, opened


def _validate_output(descriptor: int, name: str) -> None:
    try:
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AssemblyError(f"could not inspect output {name}: {exc}") from exc
    if stat.S_ISLNK(current.st_mode):
        raise AssemblyError(f"output must not be a symbolic link: {name}")
    if not stat.S_ISREG(current.st_mode):
        raise AssemblyError(f"output must be absent or a regular file: {name}")


def _create_temp(descriptor: int, name: str, payload: bytes) -> str:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _ in range(32):
        temporary = f".{name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            output = os.open(temporary, flags, 0o600, dir_fd=descriptor)
        except FileExistsError:
            continue
        except OSError as exc:
            raise AssemblyError(f"could not create atomic output for {name}: {exc}") from exc
        try:
            with os.fdopen(output, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
            os.fchmod(output, 0o755)
            os.fsync(output)
        finally:
            os.close(output)
        return temporary
    raise AssemblyError(f"could not allocate an atomic output name for {name}")


def assemble(raw_path: Path, preamble_path: Path, output_paths: Sequence[Path]) -> None:
    """Assemble one payload and atomically publish it to every output path."""

    if not output_paths:
        raise AssemblyError("at least one output path is required")
    absolute_outputs = [path.absolute() for path in output_paths]
    if len(set(absolute_outputs)) != len(absolute_outputs):
        raise AssemblyError("output paths must be unique")

    raw = _read_regular(raw_path, "raw ZipApp", _MAX_RAW_BYTES)
    if raw.startswith(b"#!"):
        newline = raw.find(b"\n")
        if newline < 0:
            raise AssemblyError("raw ZipApp shebang is not newline-terminated")
        raw = raw[newline + 1 :]
    if not raw.startswith(b"PK\x03\x04"):
        raise AssemblyError("raw input does not have a ZIP local-file signature")
    preamble = _read_regular(preamble_path, "launcher preamble", _MAX_PREAMBLE_BYTES)
    if not preamble.endswith(b"\n"):
        raise AssemblyError("launcher preamble must end with a newline")
    payload = preamble + raw

    opened: dict[Path, tuple[int, os.stat_result]] = {}
    pending: list[tuple[int, str]] = []
    try:
        for output in absolute_outputs:
            parent = output.parent
            if parent not in opened:
                opened[parent] = _open_output_parent(output)
            descriptor, _ = opened[parent]
            _validate_output(descriptor, output.name)

        for output in absolute_outputs:
            descriptor, _ = opened[output.parent]
            pending.append((descriptor, _create_temp(descriptor, output.name, payload)))

        for output in absolute_outputs:
            descriptor, temporary = pending[0]
            os.replace(temporary, output.name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
            pending.pop(0)
            written = os.stat(output.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(written.st_mode) or stat.S_IMODE(written.st_mode) != 0o755:
                raise AssemblyError(f"atomic output has unexpected mode: {output}")

        for parent, (descriptor, opened_info) in opened.items():
            os.fsync(descriptor)
            named = os.stat(parent, follow_symlinks=False)
            if _directory_identity(opened_info) != _directory_identity(named):
                raise AssemblyError(f"output directory changed during assembly: {parent}")
    finally:
        for descriptor, temporary in pending:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=descriptor)
        for descriptor, _ in opened.values():
            os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--preamble", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, action="append")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        assemble(args.raw, args.preamble, args.output)
    except AssemblyError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
