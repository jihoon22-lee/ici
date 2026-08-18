"""Tests for shared-toolchain discovery (find_uv)."""

import os
import stat
from pathlib import Path

from ici.core.env import find_uv


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_find_uv_prefers_ici_uv_env(tmp_path: Path, monkeypatch):
    uv_path = tmp_path / "tools" / "uv"
    _make_executable(uv_path)
    monkeypatch.setenv("ICI_UV", str(uv_path))
    assert find_uv() == str(uv_path)


def test_find_uv_discovers_nas_shared_bin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    nas = tmp_path / "nas_shared"
    _make_executable(nas / "bin" / "uv")
    monkeypatch.setenv("NAS_SHARED_DIR", str(nas))
    monkeypatch.delenv("ICI_UV", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert find_uv() == str(nas / "bin" / "uv")


def test_find_uv_falls_back_to_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    fake_bin = tmp_path / "pathbin"
    _make_executable(fake_bin / "uv")
    monkeypatch.delenv("ICI_UV", raising=False)
    monkeypatch.delenv("NAS_SHARED_DIR", raising=False)
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    result = find_uv()
    assert result is not None
    assert os.path.basename(result) == "uv"
