"""Contained source discovery: src-layout, lib-layout, ignores, escapes."""

from pathlib import Path

import pytest

from ici.core.project import (
    DEFAULT_SOURCE_DIRS,
    get_all_python_sources,
    get_source_dirs,
)


def test_default_dirs_and_ignores(tmp_path: Path) -> None:
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "a.py").write_text("", encoding="utf-8")
    (src / "b.txt").write_text("", encoding="utf-8")
    ignored = src / "__pycache__"
    ignored.mkdir()
    (ignored / "c.py").write_text("", encoding="utf-8")
    venv = tmp_path / ".venv" / "x"
    venv.mkdir(parents=True)
    (venv / "d.py").write_text("", encoding="utf-8")

    found = {p.relative_to(tmp_path).as_posix() for p in get_all_python_sources(tmp_path)}
    assert found == {"src/pkg/a.py"}


def test_lib_layout_is_a_default_source_dir(tmp_path: Path) -> None:
    lib = tmp_path / "lib" / "mylib"
    lib.mkdir(parents=True)
    (lib / "core.py").write_text("", encoding="utf-8")
    assert [p.name for p in get_all_python_sources(tmp_path)] == ["core.py"]


def test_source_dirs_config_overrides_and_validates(tmp_path: Path) -> None:
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "m.py").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "s.py").write_text("", encoding="utf-8")
    config = {"project": {"source_dirs": ["other"]}}
    found = [p.name for p in get_all_python_sources(tmp_path, config)]
    assert found == ["m.py"]
    with pytest.raises(ValueError):
        get_source_dirs(tmp_path, {"project": {"source_dirs": [""]}})


def test_missing_default_dirs_are_not_errors(tmp_path: Path) -> None:
    assert get_source_dirs(tmp_path) == []
    for name in DEFAULT_SOURCE_DIRS:
        (tmp_path / name).mkdir()
    assert len(get_source_dirs(tmp_path)) == len(DEFAULT_SOURCE_DIRS)


def test_escaped_symlink_dirs_are_not_followed(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}_outside"
    outside.mkdir(exist_ok=True)
    (outside / "o.py").write_text("", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.py").write_text("", encoding="utf-8")
    (src / "link").symlink_to(outside, target_is_directory=True)
    (src / "o.py").symlink_to(outside / "o.py")

    found = {p.relative_to(tmp_path).as_posix() for p in get_all_python_sources(tmp_path)}
    assert found == {"src/ok.py"}
