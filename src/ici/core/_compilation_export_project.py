"""Process-free project metadata discovery for compilation exports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ici.core.backend import select_backend
from ici.core.context import ProjectModel
from ici.core.project import read_project_metadata


def _configured_project_type(config: dict[str, Any]) -> str | None:
    configured = config.get("type")
    project = config.get("project")
    if configured is None and isinstance(project, dict):
        configured = project.get("type")
    return configured if configured in {"python", "cpp", "hybrid"} else None


def _static_project_type(root: Path, config: dict[str, Any], backend: str | None) -> str:
    configured = _configured_project_type(config)
    if configured is not None:
        return configured
    has_cpp = backend is not None or any(
        (root / name).is_file() for name in ("Makefile", "makefile")
    )
    has_python = any((root / name).is_file() for name in ("pyproject.toml", "setup.py"))
    if has_cpp and has_python:
        return "hybrid"
    return "cpp" if has_cpp else "python"


def discover_export_project(root: Path, config: dict[str, Any]) -> ProjectModel:
    """Discover only export metadata, without subprocesses or recursive source scans."""

    canonical = root.resolve(strict=False)
    backend = select_backend(canonical, config)
    name, version = read_project_metadata(canonical, allow_git=False)
    return ProjectModel(
        root=canonical,
        name=name,
        version=version,
        project_type=_static_project_type(canonical, config, backend.kind),
        backend=backend.kind,
        backend_descriptor=backend.descriptor,
        backend_reason=backend.reason,
    )
