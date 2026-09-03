"""Security and atomicity contracts for the ZipApp output assembler."""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "assemble_pyz.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("assemble_pyz", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assemble_pyz = _load_module()


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "raw.pyz"
    raw.write_bytes(b"#!/usr/bin/env python3\nPK\x03\x04archive")
    preamble = tmp_path / "launcher.sh"
    preamble.write_bytes(b"#!/bin/sh\n")
    return raw, preamble


def test_assemble_publishes_identical_executable_outputs(tmp_path: Path) -> None:
    raw, preamble = _inputs(tmp_path)
    first = tmp_path / "dist" / "ici.pyz"
    second = tmp_path / "dist" / "ici"
    first.parent.mkdir()

    assemble_pyz.assemble(raw, preamble, [first, second])

    expected = b"#!/bin/sh\nPK\x03\x04archive"
    assert first.read_bytes() == expected
    assert second.read_bytes() == expected
    assert stat.S_IMODE(first.stat().st_mode) == 0o755
    assert stat.S_IMODE(second.stat().st_mode) == 0o755
    assert not list(first.parent.glob(".*.tmp-*"))


def test_assemble_restores_preexisting_outputs_when_second_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed multi-output publish must restore the complete old output set."""
    raw, preamble = _inputs(tmp_path)
    first = tmp_path / "dist" / "ici.pyz"
    second = tmp_path / "dist" / "ici"
    first.parent.mkdir()
    first.write_bytes(b"previous pyz")
    second.write_bytes(b"previous launcher")
    first.chmod(0o640)
    second.chmod(0o710)

    original_replace = assemble_pyz.os.replace

    def fail_second_new_publish(src: object, dst: object, *args: object, **kwargs: object) -> None:
        source_name = Path(os.fspath(src)).name
        destination_name = os.fspath(dst)
        if destination_name == second.name and ".tmp" in source_name:
            raise OSError("simulated second output publish failure")
        original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(assemble_pyz.os, "replace", fail_second_new_publish)

    with pytest.raises(assemble_pyz.AssemblyError):
        assemble_pyz.assemble(raw, preamble, [first, second])

    assert first.read_bytes() == b"previous pyz"
    assert second.read_bytes() == b"previous launcher"
    assert stat.S_IMODE(first.stat().st_mode) == 0o640
    assert stat.S_IMODE(second.stat().st_mode) == 0o710
    assert not [entry for entry in first.parent.iterdir() if entry.name.startswith(".")]


def test_assemble_removes_new_outputs_when_second_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, preamble = _inputs(tmp_path)
    first = tmp_path / "dist" / "ici.pyz"
    second = tmp_path / "dist" / "ici"
    first.parent.mkdir()
    original_replace = assemble_pyz.os.replace

    def fail_second_new_publish(src: object, dst: object, *args: object, **kwargs: object) -> None:
        source_name = Path(os.fspath(src)).name
        destination_name = os.fspath(dst)
        if destination_name == second.name and ".tmp" in source_name:
            raise OSError("simulated second output publish failure")
        original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(assemble_pyz.os, "replace", fail_second_new_publish)

    with pytest.raises(assemble_pyz.AssemblyError):
        assemble_pyz.assemble(raw, preamble, [first, second])

    assert not first.exists()
    assert not second.exists()
    assert not list(first.parent.iterdir())


def test_assemble_preserves_backup_when_rollback_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, preamble = _inputs(tmp_path)
    first = tmp_path / "dist" / "ici.pyz"
    second = tmp_path / "dist" / "ici"
    first.parent.mkdir()
    first.write_bytes(b"recoverable previous pyz")
    second.write_bytes(b"previous launcher")
    original_replace = assemble_pyz.os.replace

    def fail_publish_and_rollback(
        src: object, dst: object, *args: object, **kwargs: object
    ) -> None:
        source_name = Path(os.fspath(src)).name
        destination_name = os.fspath(dst)
        if destination_name == second.name and ".tmp" in source_name:
            raise OSError("simulated second output publish failure")
        if destination_name == first.name and ".backup" in source_name:
            raise OSError("simulated rollback failure")
        original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(assemble_pyz.os, "replace", fail_publish_and_rollback)

    with pytest.raises(assemble_pyz.AssemblyError, match="previous inode preserved as"):
        assemble_pyz.assemble(raw, preamble, [first, second])

    recovery_files = list(first.parent.glob(f".{first.name}.backup-*"))
    assert len(recovery_files) == 1
    assert recovery_files[0].read_bytes() == b"recoverable previous pyz"
    assert second.read_bytes() == b"previous launcher"
    assert not list(first.parent.glob(".*.tmp-*"))


def test_assemble_rejects_existing_output_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    raw, preamble = _inputs(tmp_path)
    victim = tmp_path / "victim"
    victim.write_bytes(b"preserve me")
    output = tmp_path / "ici.pyz"
    output.symlink_to(victim)

    with pytest.raises(assemble_pyz.AssemblyError, match="must not be a symbolic link"):
        assemble_pyz.assemble(raw, preamble, [output])

    assert output.is_symlink()
    assert victim.read_bytes() == b"preserve me"


def test_assemble_rejects_symlink_output_parent(tmp_path: Path) -> None:
    raw, preamble = _inputs(tmp_path)
    real_parent = tmp_path / "real-dist"
    real_parent.mkdir()
    linked_parent = tmp_path / "dist"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(assemble_pyz.AssemblyError, match="non-symlink directory"):
        assemble_pyz.assemble(raw, preamble, [linked_parent / "ici.pyz"])

    assert not list(real_parent.iterdir())


@pytest.mark.parametrize("source_name", ["raw", "preamble"])
def test_assemble_rejects_symlink_inputs(tmp_path: Path, source_name: str) -> None:
    raw, preamble = _inputs(tmp_path)
    source = raw if source_name == "raw" else preamble
    real_source = source.with_suffix(source.suffix + ".real")
    source.rename(real_source)
    source.symlink_to(real_source)

    with pytest.raises(assemble_pyz.AssemblyError, match="non-symlink file"):
        assemble_pyz.assemble(raw, preamble, [tmp_path / "ici.pyz"])


def test_assemble_rejects_special_or_duplicate_outputs(tmp_path: Path) -> None:
    raw, preamble = _inputs(tmp_path)
    output = tmp_path / "ici.pyz"
    output.mkdir()

    with pytest.raises(assemble_pyz.AssemblyError, match="absent or a regular file"):
        assemble_pyz.assemble(raw, preamble, [output])
    with pytest.raises(assemble_pyz.AssemblyError, match="must be unique"):
        assemble_pyz.assemble(raw, preamble, [tmp_path / "ici", tmp_path / "ici"])


def test_assemble_rejects_invalid_raw_and_preamble(tmp_path: Path) -> None:
    raw, preamble = _inputs(tmp_path)
    raw.write_bytes(b"not a zip")
    with pytest.raises(assemble_pyz.AssemblyError, match="ZIP local-file signature"):
        assemble_pyz.assemble(raw, preamble, [tmp_path / "ici.pyz"])

    raw.write_bytes(b"PK\x03\x04archive")
    preamble.write_bytes(b"#!/bin/sh")
    with pytest.raises(assemble_pyz.AssemblyError, match="must end with a newline"):
        assemble_pyz.assemble(raw, preamble, [tmp_path / "ici.pyz"])


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO inputs are not available")
def test_assemble_rejects_fifo_input_without_blocking(tmp_path: Path) -> None:
    """A special input must be rejected before opening it for a blocking read."""
    raw, preamble = _inputs(tmp_path)
    raw.unlink()
    os.mkfifo(raw, 0o600)
    output = tmp_path / "ici.pyz"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--raw",
            str(raw),
            "--preamble",
            str(preamble),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert completed.returncode != 0
    assert "regular file" in completed.stderr
    assert not output.exists()


class _WriteFailingStream:
    def __init__(self, wrapped: object) -> None:
        self._wrapped = wrapped

    def __enter__(self) -> _WriteFailingStream:
        self._wrapped.__enter__()
        return self

    def __exit__(self, *args: object) -> object:
        return self._wrapped.__exit__(*args)

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def write(self, _payload: bytes) -> int:
        raise OSError("simulated temporary output write failure")


def test_assemble_cleans_temp_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, preamble = _inputs(tmp_path)
    output = tmp_path / "dist" / "ici.pyz"
    output.parent.mkdir()
    original_fdopen = assemble_pyz.os.fdopen

    def fail_temp_write(fd: int, *args: object, **kwargs: object) -> _WriteFailingStream:
        return _WriteFailingStream(original_fdopen(fd, *args, **kwargs))

    monkeypatch.setattr(assemble_pyz.os, "fdopen", fail_temp_write)

    with pytest.raises(assemble_pyz.AssemblyError, match="could not write atomic output"):
        assemble_pyz.assemble(raw, preamble, [output])

    assert not output.exists()
    assert not list(output.parent.glob(".*.tmp-*"))


def test_assemble_cleans_temp_after_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw, preamble = _inputs(tmp_path)
    output = tmp_path / "dist" / "ici.pyz"
    output.parent.mkdir()
    original_fsync = assemble_pyz.os.fsync

    def fail_temp_fsync(fd: int) -> None:
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("simulated temporary output fsync failure")
        original_fsync(fd)

    monkeypatch.setattr(assemble_pyz.os, "fsync", fail_temp_fsync)

    with pytest.raises(assemble_pyz.AssemblyError, match="could not write atomic output"):
        assemble_pyz.assemble(raw, preamble, [output])

    assert not output.exists()
    assert not list(output.parent.glob(".*.tmp-*"))
