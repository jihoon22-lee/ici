"""Canonical project path resolution (shared)."""

from pathlib import Path


def _resolve_project_root(base: Path) -> Path:
    try:
        return Path(base).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as err:
        raise ValueError(f"could not resolve project root {base}: {err}") from err


def resolve_project_path(base: Path, value: str) -> Path:
    """Resolve a project-relative path and enforce canonical containment.

    Raises ValueError if the resolved path escapes the project root.
    """
    try:
        project_root = _resolve_project_root(Path(base))
        candidate = (project_root / value).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as err:
        raise ValueError(f"could not resolve project path {value!r}: {err}") from err

    try:
        candidate.relative_to(project_root)
    except ValueError as err:
        raise ValueError(f"path is outside project root: {value}") from err
    return candidate
