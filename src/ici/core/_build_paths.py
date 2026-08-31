"""Owned build-path construction shared by native build adapters."""

from __future__ import annotations

from pathlib import Path


def shadow_dir(root: Path, backend: str, suffix: str = "") -> Path:
    """Return the per-backend build directory owned by ici."""

    return root / "build" / f"ici-{backend}{suffix}"


def prepare_owned_shadow(root: Path, shadow: Path) -> tuple[Path | None, str]:
    """Create an owned shadow without following an escape outside *root*."""

    try:
        build_root = root / "build"
        build_root.mkdir(parents=True, exist_ok=True)
        resolved_build = build_root.resolve(strict=True)
        resolved_build.relative_to(root)
        shadow.mkdir(parents=True, exist_ok=True)
        resolved_shadow = shadow.resolve(strict=True)
        resolved_shadow.relative_to(resolved_build)
    except (OSError, RuntimeError, ValueError) as err:
        return None, f"build shadow is unsafe or unavailable: {err}"
    return resolved_shadow, ""


__all__ = ["prepare_owned_shadow", "shadow_dir"]
