"""Regression tests for project metadata parsing and validation."""

from pathlib import Path

import pytest

from ici.core.project import (
    get_project_name,
    get_project_version,
    read_project_metadata,
    resolve_project_path,
)


def test_project_metadata_reads_project_table(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo-app'\nversion = '2.4.1'\n", encoding="utf-8"
    )

    assert get_project_name(tmp_path) == "demo-app"
    assert get_project_version(tmp_path) == "v2.4.1"
    assert read_project_metadata(tmp_path) == ("demo-app", "v2.4.1")


def test_ici_metadata_uses_top_level_name_and_version(tmp_path: Path):
    (tmp_path / "ici.toml").write_text(
        "name = 'demo-app'\nversion = 'v1.2.3'\n[project]\nname = 'wrong'\n",
        encoding="utf-8",
    )

    assert read_project_metadata(tmp_path) == ("demo-app", "v1.2.3")


def test_project_metadata_rejects_unsafe_name(tmp_path: Path):
    (tmp_path / "ici.toml").write_text("name = '../escape'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="project name"):
        get_project_name(tmp_path)


def test_project_metadata_rejects_dot_name(tmp_path: Path):
    (tmp_path / "ici.toml").write_text("name = '..'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="project name"):
        get_project_name(tmp_path)


def test_project_metadata_rejects_unsafe_version(tmp_path: Path):
    (tmp_path / "ici.toml").write_text("version = '1/2.3'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="project version"):
        get_project_version(tmp_path)


def test_project_metadata_rejects_malformed_toml(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project\nname = 'demo-app'\n", encoding="utf-8")

    with pytest.raises(ValueError, match="project metadata"):
        read_project_metadata(tmp_path)


def test_project_metadata_rejects_symlinked_metadata_escape(tmp_path: Path):
    outside = tmp_path.parent / "outside-metadata"
    outside.write_text("name = 'outside'\nversion = '9.9.9'\n", encoding="utf-8")
    (tmp_path / "ici.toml").symlink_to(outside)

    with pytest.raises(ValueError, match="outside project root"):
        read_project_metadata(tmp_path)


def test_project_metadata_rejects_symlink_loop_project_root(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.symlink_to(second, target_is_directory=True)
    second.symlink_to(first, target_is_directory=True)

    with pytest.raises(ValueError, match="project root"):
        read_project_metadata(first)


def test_resolve_project_path_uses_canonical_containment(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "link").symlink_to(tmp_path.parent, target_is_directory=True)

    assert resolve_project_path(tmp_path, "src/../src") == source.resolve()
    with pytest.raises(ValueError, match="outside project root"):
        resolve_project_path(tmp_path, "src/link")
