"""Adapter selection — explicit config wins, then unique detection."""

import shutil
from pathlib import Path

from ici.build_adapters.base import BuildAdapter, BuildAdapterError
from ici.build_adapters.cmake import CMakeAdapter
from ici.build_adapters.qmake import QMakeAdapter


def _resolve_tools(tool_paths: dict[str, str], names: tuple[str, ...]) -> dict[str, str]:
    """Fill any missing tool path from PATH."""
    resolved: dict[str, str | None] = dict(tool_paths)
    for name in names:
        if not resolved.get(name):
            resolved[name] = shutil.which(name)
    return {k: v for k, v in resolved.items() if v}


def detect_build_system(project_root: Path) -> tuple[str, Path | None]:
    """Return ('cmake', None) | ('qmake', pro_path) | ('none', None).

    Ambiguous layouts (both systems, or multiple .pro files) raise.
    """
    has_cmake = (project_root / "CMakeLists.txt").is_file()
    pro_files = (
        sorted(p for p in project_root.iterdir() if p.suffix == ".pro" and p.is_file())
        if project_root.is_dir()
        else []
    )

    if has_cmake and pro_files:
        raise BuildAdapterError("multiple build systems found: CMakeLists.txt and *.pro")
    if len(pro_files) > 1:
        names = ", ".join(p.name for p in pro_files)
        raise BuildAdapterError(f"multiple qmake project files found: {names}")
    if has_cmake:
        return "cmake", None
    if pro_files:
        return "qmake", pro_files[0]
    return "none", None


def select_build_adapter(
    project_root: Path,
    adapter_choice: str,
    tool_paths: dict[str, str],
) -> tuple[str, BuildAdapter | None]:
    """Return (adapter_name, adapter_instance) or ('none', None)."""
    detected, pro_file = detect_build_system(project_root)
    chosen = adapter_choice or "auto"
    if chosen == "auto":
        chosen = detected

    if chosen == "cmake":
        return "cmake", CMakeAdapter(_resolve_tools(tool_paths, ("cmake", "ctest")))
    if chosen == "qmake":
        pro = _single_pro_or_raise(project_root) if pro_file is None else pro_file
        return "qmake", QMakeAdapter(
            _resolve_tools(tool_paths, ("qmake", "make")),
            pro,
        )
    return "none", None


def _single_pro_or_raise(project_root: Path) -> Path:
    from ici.build_adapters.registry import detect_build_system as _detect

    _, pro_file = _detect(project_root)
    if pro_file is None:
        raise BuildAdapterError("adapter 'qmake' requested but no .pro file exists")
    return pro_file
