#!/usr/bin/env python3
"""Safely assemble launcher-prefixed ZipApp outputs.

The builder writes only through opened, non-symlink directory descriptors and
atomically replaces each regular output file. Existing outputs are backed up
before publication so a later replacement failure restores the previous set.
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
    try:
        named_before = os.stat(absolute, follow_symlinks=False)
    except OSError as exc:
        raise AssemblyError(f"could not inspect {label}: {exc}") from exc
    if stat.S_ISLNK(named_before.st_mode):
        raise AssemblyError(f"could not open {label} as a non-symlink file")
    if not stat.S_ISREG(named_before.st_mode):
        raise AssemblyError(f"{label} must be a regular file")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
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
        if _identity(named_before) != _identity(initial):
            raise AssemblyError(f"{label} changed while it was opened")
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


def _validate_output(descriptor: int, name: str) -> os.stat_result | None:
    try:
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AssemblyError(f"could not inspect output {name}: {exc}") from exc
    if stat.S_ISLNK(current.st_mode):
        raise AssemblyError(f"output must not be a symbolic link: {name}")
    if not stat.S_ISREG(current.st_mode):
        raise AssemblyError(f"output must be absent or a regular file: {name}")
    return current


def _unlink_if_present(descriptor: int, name: str) -> None:
    with suppress(FileNotFoundError):
        os.unlink(name, dir_fd=descriptor)


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
                written = stream.write(payload)
                stream.flush()
            if written != len(payload):
                raise AssemblyError(f"short write while creating atomic output for {name}")
            os.fchmod(output, 0o755)
            os.fsync(output)
        except BaseException as exc:
            with suppress(OSError):
                os.close(output)
            _unlink_if_present(descriptor, temporary)
            if isinstance(exc, OSError):
                raise AssemblyError(f"could not write atomic output for {name}: {exc}") from exc
            raise
        try:
            os.close(output)
        except BaseException as exc:
            _unlink_if_present(descriptor, temporary)
            if isinstance(exc, OSError):
                raise AssemblyError(f"could not close atomic output for {name}: {exc}") from exc
            raise
        return temporary
    raise AssemblyError(f"could not allocate an atomic output name for {name}")


def _create_backup(
    descriptor: int,
    name: str,
    previous: os.stat_result,
) -> str:
    """Hard-link one existing regular output before any replacement starts."""

    for _ in range(32):
        backup = f".{name}.backup-{os.getpid()}-{secrets.token_hex(8)}"
        try:
            os.link(
                name,
                backup,
                src_dir_fd=descriptor,
                dst_dir_fd=descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise AssemblyError(f"could not back up existing output {name}: {exc}") from exc
        try:
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            saved = os.stat(backup, dir_fd=descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or not stat.S_ISREG(saved.st_mode)
                or (current.st_dev, current.st_ino) != (saved.st_dev, saved.st_ino)
                or (previous.st_dev, previous.st_ino) != (saved.st_dev, saved.st_ino)
            ):
                raise AssemblyError(f"output changed while it was backed up: {name}")
        except BaseException:
            _unlink_if_present(descriptor, backup)
            raise
        return backup
    raise AssemblyError(f"could not allocate a backup name for {name}")


def _assert_previous_output(
    descriptor: int,
    name: str,
    previous: os.stat_result | None,
    backup: str | None,
) -> None:
    """Confirm that publication will replace exactly the state we backed up."""

    try:
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        if previous is None:
            return
        raise AssemblyError(f"output disappeared before publication: {name}") from None
    except OSError as exc:
        raise AssemblyError(f"could not recheck output {name}: {exc}") from exc
    if previous is None:
        raise AssemblyError(f"output appeared before publication: {name}")
    if backup is None:
        raise AssemblyError(f"existing output has no rollback backup: {name}")
    saved = os.stat(backup, dir_fd=descriptor, follow_symlinks=False)
    if (
        not stat.S_ISREG(current.st_mode)
        or not stat.S_ISREG(saved.st_mode)
        or (current.st_dev, current.st_ino) != (saved.st_dev, saved.st_ino)
    ):
        raise AssemblyError(f"output changed before publication: {name}")


def _verify_published(descriptor: int, name: str, payload: bytes) -> None:
    """Verify one published name before its rollback backup is discarded."""

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        published = os.open(name, flags, dir_fd=descriptor)
    except OSError as exc:
        raise AssemblyError(f"could not verify published output {name}: {exc}") from exc
    try:
        initial = os.fstat(published)
        if (
            not stat.S_ISREG(initial.st_mode)
            or stat.S_IMODE(initial.st_mode) != 0o755
            or initial.st_size != len(payload)
        ):
            raise AssemblyError(f"published output has invalid metadata: {name}")
        with os.fdopen(published, "rb", closefd=False) as stream:
            observed = _read_bounded(stream, len(payload), f"published output {name}")
        final = os.fstat(published)
        named = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if (
            observed != payload
            or _identity(initial) != _identity(final)
            or _identity(final) != _identity(named)
        ):
            raise AssemblyError(f"published output failed content verification: {name}")
    finally:
        os.close(published)


class _OutputState:
    """Mutable publication state kept private to one assembly transaction."""

    def __init__(
        self,
        path: Path,
        descriptor: int,
        previous: os.stat_result | None,
    ) -> None:
        self.path = path
        self.descriptor = descriptor
        self.previous = previous
        self.temporary: str | None = None
        self.backup: str | None = None
        self.published = False


def _rollback(states: Sequence[_OutputState], cause: BaseException) -> AssemblyError:
    failures: list[str] = []
    restored_directories: set[int] = set()
    for state in reversed(states):
        if not state.published:
            continue
        try:
            if state.backup is None:
                os.unlink(state.path.name, dir_fd=state.descriptor)
            else:
                os.replace(
                    state.backup,
                    state.path.name,
                    src_dir_fd=state.descriptor,
                    dst_dir_fd=state.descriptor,
                )
                state.backup = None
            state.published = False
            restored_directories.add(state.descriptor)
        except OSError as exc:
            recovery = state.backup
            if recovery is not None:
                # Do not let the outer cleanup destroy the only recovery copy.
                state.backup = None
                failures.append(f"{state.path}: {exc} (previous inode preserved as {recovery})")
            else:
                failures.append(f"{state.path}: {exc}")
    for descriptor in restored_directories:
        with suppress(OSError):
            os.fsync(descriptor)
    message = f"could not publish a consistent output set: {cause}"
    if failures:
        message += "; rollback also failed: " + "; ".join(failures)
    return AssemblyError(message)


def assemble(raw_path: Path, preamble_path: Path, output_paths: Sequence[Path]) -> None:
    """Assemble one payload with atomic per-name replacement and set rollback."""

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
    states: list[_OutputState] = []
    try:
        for output in absolute_outputs:
            parent = output.parent
            if parent not in opened:
                opened[parent] = _open_output_parent(output)
            descriptor, _ = opened[parent]
            states.append(
                _OutputState(output, descriptor, _validate_output(descriptor, output.name))
            )

        for state in states:
            state.temporary = _create_temp(state.descriptor, state.path.name, payload)
        for state in states:
            if state.previous is not None:
                state.backup = _create_backup(
                    state.descriptor,
                    state.path.name,
                    state.previous,
                )
        for state in states:
            _assert_previous_output(
                state.descriptor,
                state.path.name,
                state.previous,
                state.backup,
            )

        try:
            for state in states:
                assert state.temporary is not None
                os.replace(
                    state.temporary,
                    state.path.name,
                    src_dir_fd=state.descriptor,
                    dst_dir_fd=state.descriptor,
                )
                state.temporary = None
                state.published = True
                _verify_published(state.descriptor, state.path.name, payload)

            for parent, (descriptor, opened_info) in opened.items():
                os.fsync(descriptor)
                named = os.stat(parent, follow_symlinks=False)
                if _directory_identity(opened_info) != _directory_identity(named):
                    raise AssemblyError(f"output directory changed during assembly: {parent}")
        except BaseException as exc:
            raise _rollback(states, exc) from exc

        for state in states:
            if state.backup is not None:
                _unlink_if_present(state.descriptor, state.backup)
                state.backup = None
        for descriptor, _ in opened.values():
            os.fsync(descriptor)
    finally:
        for state in states:
            if state.temporary is not None:
                _unlink_if_present(state.descriptor, state.temporary)
            if state.backup is not None:
                _unlink_if_present(state.descriptor, state.backup)
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
