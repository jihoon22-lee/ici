"""Security and atomicity contracts for the ZipApp output assembler."""

from __future__ import annotations

import importlib.util
import stat
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
