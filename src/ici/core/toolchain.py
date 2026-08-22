"""Toolchain capability collection — real paths and versions of CI tools."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ici.core.runner import run_process

DEFAULT_PROBES: dict[str, list[str]] = {
    "gcc": ["gcc", "-dumpfullversion"],
    "g++": ["g++", "-dumpfullversion"],
    "make": ["make", "--version"],
    "cmake": ["cmake", "--version"],
    "qmake": ["qmake", "-query", "QT_VERSION"],
    "gcov": ["gcov", "--version"],
    "git": ["git", "--version"],
    "python3": ["python3", "-VV"],
}


@dataclass
class ToolCapability:
    """Observed availability and version of one external tool."""

    name: str
    path: str
    available: bool
    version: str = ""
    error: str = ""
    details: dict[str, str] = field(default_factory=dict)


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


def collect_tool_capability(
    name: str, probe: list[str], cwd: Path | None = None, timeout: float = 15.0
) -> tuple[ToolCapability, object | None]:
    """Probe one tool and return its capability plus the raw ProcessResult.

    ``which`` failures produce an unavailable capability without running argv.
    """

    resolved = shutil.which(probe[0])
    if resolved is None:
        return ToolCapability(name=name, path="", available=False), None

    result = run_process([resolved, *probe[1:]], cwd=cwd, timeout=timeout)
    if result.timed_out or result.truncated or result.returncode != 0:
        reason = (
            "probe timed out"
            if result.timed_out
            else "probe output truncated"
            if result.truncated
            else f"probe exited {result.returncode}"
        )
        return (
            ToolCapability(name=name, path=resolved, available=False, error=reason),
            result,
        )

    version = _first_meaningful_line(result.stdout) or _first_meaningful_line(result.stderr)
    return ToolCapability(name=name, path=resolved, available=True, version=version), result
