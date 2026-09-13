"""The clean component. A finding here is a misattribution."""


def label(kind: str) -> str:
    """Return a display label for a known kind."""

    known = {"a": "Alpha", "b": "Beta"}
    return known.get(kind, "Unknown")
