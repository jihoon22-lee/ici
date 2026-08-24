"""Load Zero-CDN assets via importlib.resources (inline at generation time)."""

from pathlib import Path

try:
    from importlib.resources import files as _files
except ImportError:
    from importlib_resources import files as _files  # type: ignore


def _read_text(package: str, resource: str) -> str:
    try:
        return (_files(package) / resource).read_text(encoding="utf-8")
    except Exception:
        # Fallback to a file path relative to this file (for dev without
        # package install) -- resource lives under html/assets/, one level
        # below this file's own parent.
        return (Path(__file__).parent / "assets" / resource).read_text(encoding="utf-8")


HTML_CSS = _read_text("ici.reporters.html.assets", "style.css")
HTML_JS = _read_text("ici.reporters.html.assets", "app.js")
