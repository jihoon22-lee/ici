"""Bounded pytest output normalization — verdicts, node locations, durations.

Extracted from the stable ``ici.engines.test_output`` module so the next
path's pytest provider and the remaining stable test engine share one parser.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

_PYTEST_DURATION_RE = re.compile(
    r"^\s*(?P<seconds>(?:\d+(?:\.\d+)?|\.\d+))s\s+"
    r"(?P<phase>[A-Za-z][A-Za-z0-9_-]*)\s+(?P<nodeid>\S.*?)\s*$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MAX_DURATION_TOKEN_CHARS = 32


def _strip_ansi(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _node_location(nodeid: str, project_root: Path) -> tuple[str, int]:
    """Resolve a pytest node id to a contained project path and line.

    Pytest's duration report normally contains a node id without a source line,
    but plugins and custom reporters sometimes emit ``file.py:42::test``.  The
    line is kept when present.  A malformed or escaping path is intentionally
    reduced to the stable ``tests`` scope rather than allowing report data to
    point outside the project.
    """

    source = nodeid.split("::", 1)[0]
    line = 1
    line_match = re.match(r"^(?P<path>.*):(?P<line>[1-9]\d*)$", source)
    if line_match is not None:
        source = line_match.group("path")
        line = int(line_match.group("line"))

    try:
        root = project_root.resolve(strict=False)
        candidate = Path(source)
        resolved = (
            candidate.resolve(strict=False)
            if candidate.is_absolute()
            else (root / candidate).resolve(strict=False)
        )
        relative = resolved.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError, TypeError):
        return "tests", 1
    if not relative or relative == "." or relative.startswith("../"):
        return "tests", 1
    return relative, line


def parse_pytest_outcomes(output: str) -> dict[str, str]:
    """Return deterministic per-node pytest verdicts from verbose output.

    Summary-only output cannot identify individual nodes and therefore yields
    an empty mapping.  When a plugin repeats a report line, the last verdict is
    authoritative, matching pytest's final terminal state.
    """

    outcomes: dict[str, str] = {}
    for line in _strip_ansi(output).splitlines():
        if "::" not in line:
            continue
        # Match from the line rather than splitting on whitespace: pytest
        # parameter ids may legitimately contain spaces (``test[x y]``).
        verdict_matches = list(
            re.finditer(r"\b(?:PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b", line)
        )
        if not verdict_matches:
            continue
        match = verdict_matches[-1]
        nodeid = line[: match.start()].strip()
        if nodeid:
            outcomes[nodeid] = match.group(0)
    return {key: outcomes[key] for key in sorted(outcomes)}


def pytest_node_location(nodeid: str, project_root: Path) -> tuple[str, int]:
    """Expose the contained source location mapping used by quality findings."""

    return _node_location(nodeid, project_root)


def _pytest_duration_summary(
    output: str,
    project_root: Path,
    *,
    threshold: float = 0.0,
    max_items: int = 50,
) -> tuple[list[dict[str, Any]], int]:
    """Parse pytest duration rows and retain a bounded, source-aware inventory.

    Pytest prints the slowest rows in a presentation-oriented table.  Parsing
    and sorting here gives callers stable ordering across pytest versions and
    keeps duplicate plugin rows from inflating the observed count.
    """

    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(threshold)
        or threshold < 0
    ):
        raise ValueError("duration threshold must be a finite non-negative number")
    if type(max_items) is not int or max_items < 1:
        raise ValueError("max_items must be positive")

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for line in _strip_ansi(output).splitlines():
        match = _PYTEST_DURATION_RE.match(line)
        if match is None:
            continue
        seconds_text = match.group("seconds")
        if len(seconds_text) > _MAX_DURATION_TOKEN_CHARS:
            continue
        seconds = float(seconds_text)
        if not math.isfinite(seconds):
            continue
        if seconds < threshold:
            continue
        phase = match.group("phase")
        nodeid = match.group("nodeid").strip()
        if "::" not in nodeid:
            continue
        file_path, start_line = _node_location(nodeid, project_root)
        key = (nodeid, phase)
        candidate = {
            "nodeid": nodeid,
            "phase": phase,
            "duration": seconds,
            "file_path": file_path,
            "start_line": start_line,
        }
        current = rows.get(key)
        if current is None or seconds > float(current["duration"]):
            rows[key] = candidate

    ordered = sorted(
        rows.values(),
        key=lambda row: (
            -float(row["duration"]),
            str(row["file_path"]),
            int(row["start_line"]),
            str(row["nodeid"]),
            str(row["phase"]),
        ),
    )
    return ordered[:max_items], len(ordered)


def parse_pytest_durations(
    output: str,
    project_root: Path,
    *,
    threshold: float = 0.0,
    max_items: int = 50,
) -> list[dict[str, Any]]:
    """Return the bounded retained portion of a pytest duration report."""

    rows, _observed = _pytest_duration_summary(
        output,
        project_root,
        threshold=threshold,
        max_items=max_items,
    )
    return rows
