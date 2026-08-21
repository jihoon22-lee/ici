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
        # Fallback to file path relative to this file (for dev without package install)
        return (Path(__file__).parent / resource).read_text(encoding="utf-8")


try:
    HTML_CSS = _read_text("ici.reporters.html.assets", "style.css")
except Exception:
    HTML_CSS = Path(__file__).parent.joinpath("assets", "style.css").read_text(encoding="utf-8")

try:
    HTML_JS = _read_text("ici.reporters.html.assets", "app.js")
except Exception:
    HTML_JS = Path(__file__).parent.joinpath("assets", "app.js").read_text(encoding="utf-8")
