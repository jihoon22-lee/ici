"""Contained source-file discovery for ici analysis helpers."""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ici.core.path_utils import _resolve_project_root, resolve_project_path

DEFAULT_SOURCE_DIRS = ["src", "lib", "app", "packages", "python"]


def _safe_project_dir(base: Path, path: Path) -> Path | None:
    """Return an existing in-project directory without following an escaped link."""
    try:
        candidate = resolve_project_path(base, os.fspath(path))
        return candidate if candidate.is_dir() else None
    except (OSError, ValueError):
        return None


def _iter_project_files(root: Path, base: Path, suffixes: tuple[str, ...]) -> Iterator[Path]:
    """Yield contained regular files while ignoring symlink traversal."""
    safe_root = _safe_project_dir(base, root)
    if safe_root is None:
        return

    for current, dir_names, file_names in os.walk(safe_root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_names: list[str] = []
        for dir_name in dir_names:
            path = current_path / dir_name
            if path.is_symlink():
                continue
            safe_dir = _safe_project_dir(base, path)
            if safe_dir is None or _should_ignore_path(safe_dir):
                continue
            safe_names.append(dir_name)
        dir_names[:] = safe_names

        for file_name in file_names:
            path = current_path / file_name
            if path.is_symlink() or path.suffix not in suffixes:
                continue
            try:
                canonical = resolve_project_path(base, os.fspath(path))
                if canonical.is_file() and not _should_ignore_path(canonical):
                    yield canonical
            except (OSError, ValueError):
                continue


def get_source_dirs(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Resolves existing project source directories (overridable via config project.source_dirs)."""
    base = _resolve_project_root(base_path or Path.cwd())
    names: list[str] | None = None
    configured = False
    if config:
        proj_cfg = config.get("project")
        if isinstance(proj_cfg, dict):
            raw = proj_cfg.get("source_dirs")
            if isinstance(raw, list):
                configured = True
                names = []
                for item in raw:
                    if not isinstance(item, str) or not item:
                        raise ValueError("project.source_dirs must be a list of non-empty strings")
                    names.append(item)
    if names is None:
        names = DEFAULT_SOURCE_DIRS

    dirs: list[Path] = []
    for name in names:
        try:
            candidate = resolve_project_path(base, name)
        except ValueError:
            if configured:
                raise
            continue
        try:
            if candidate.is_dir():
                dirs.append(candidate)
        except OSError as err:
            if configured:
                raise ValueError(f"could not inspect source directory {name!r}: {err}") from err
    return dirs


def get_all_python_sources(
    base_path: Path | None = None, config: dict[str, Any] | None = None
) -> list[Path]:
    """Finds all Python source files across project source directories."""
    base = _resolve_project_root(base_path or Path.cwd())
    py_files: list[Path] = []
    for src_dir in get_source_dirs(base, config):
        py_files.extend(_iter_project_files(src_dir, base, (".py",)))
    return sorted(py_files)


def _should_ignore_path(p: Path) -> bool:
    parts = p.parts
    return any(
        part in (".venv", "venv", "build", "__pycache__", ".git", ".pytest_cache", ".ruff_cache")
        or part.startswith("v1.")
        or part.startswith("v0.")
        for part in parts
    )
