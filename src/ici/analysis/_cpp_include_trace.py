"""Bounded GCC/Clang include-trace and missing-include parsers."""

from __future__ import annotations

import re
from pathlib import Path

from ici.core.models import EngineStatus, InspectionTarget

MAX_INCLUDE_TRACE_ENTRIES = 200_000
MAX_INCLUDE_TRACE_DEPTH = 4_096
MAX_DIAGNOSTIC_LINE = 2_147_483_647
_TRACE_RE = re.compile(r"^(?P<depth>\.+) (?P<path>\S.*)$")
_GCC_MISSING_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::[1-9]\d*)?: fatal error: "
    r"(?P<include>.+?): No such file or directory$"
)
_CLANG_MISSING_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>[1-9]\d*)(?::[1-9]\d*)?: fatal error: "
    r"['<](?P<include>.+?)[>'] file not found$"
)
_MISSING_CONTEXT_RE = re.compile(
    r"^(?:In file included from .+|\s*from .+|\s*\d+\s*\|.*|\s*\|.*|\s*[\^~].*|"
    r"compilation terminated\.|\d+ errors? generated\.)$"
)


def _relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _existing_trace_path(value: str, cwd: Path) -> Path | None:
    lexical = Path(value)
    try:
        return (lexical if lexical.is_absolute() else cwd / lexical).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None


def _consume_trace_entry(
    match: re.Match[str],
    *,
    cwd: Path,
    stack: list[Path | None],
) -> tuple[Path, Path] | None:
    depth = len(match.group("depth"))
    if depth > MAX_INCLUDE_TRACE_DEPTH:
        raise ValueError("compiler include trace exceeds its bounded shape")
    if depth > len(stack):
        raise ValueError("compiler include trace jumps over a parent depth")
    raw_path = match.group("path")
    if raw_path.startswith("<") and raw_path.endswith(">"):
        stack[depth:] = [None]
        return None
    child = _existing_trace_path(raw_path, cwd)
    if child is None:
        raise ValueError("compiler include trace references a stale path")
    parent = stack[depth - 1]
    stack[depth:] = [child]
    return (parent, child) if parent is not None else None


def parse_include_trace(
    stderr: str,
    *,
    cwd: Path,
    source: Path,
) -> tuple[list[tuple[Path, Path]], list[str]]:
    """Return real include edges and every unrecognized trace line."""

    stack: list[Path | None] = [source]
    edges: list[tuple[Path, Path]] = []
    unexpected: list[str] = []
    trailer = False
    entries = 0
    for raw_line in stderr.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        if line == "Multiple include guards may be useful for:":
            if trailer:
                unexpected.append(line)
            trailer = True
            continue
        if trailer:
            entries += 1
            if entries > MAX_INCLUDE_TRACE_ENTRIES:
                raise ValueError("compiler include trace exceeds its bounded shape")
            if _existing_trace_path(line, cwd) is None:
                unexpected.append(line)
            continue
        match = _TRACE_RE.fullmatch(line)
        if match is None:
            unexpected.append(line)
            continue
        entries += 1
        if entries > MAX_INCLUDE_TRACE_ENTRIES:
            raise ValueError("compiler include trace exceeds its bounded shape")
        if edge := _consume_trace_entry(match, cwd=cwd, stack=stack):
            edges.append(edge)
    return edges, unexpected


def _missing_target(root: Path, cwd: Path, line: str) -> InspectionTarget | None:
    match = _GCC_MISSING_RE.fullmatch(line) or _CLANG_MISSING_RE.fullmatch(line)
    if match is None:
        return None
    line_value = match.group("line")
    if len(line_value) > 10:
        return None
    try:
        line_number = int(line_value)
        file_value = Path(match.group("file"))
        resolved = (file_value if file_value.is_absolute() else cwd / file_value).resolve(
            strict=False
        )
    except (OSError, RuntimeError, ValueError, OverflowError):
        return None
    if not 0 < line_number <= MAX_DIAGNOSTIC_LINE:
        return None
    return InspectionTarget(
        file_path=_relative_or_absolute(root, resolved),
        start_line=line_number,
        target_name="CppIncludeUnresolved",
        status=EngineStatus.WARN,
        message=(
            f'Compiler could not resolve active include "{match.group("include")}"; '
            "no dependency edge was recorded"
        ),
        snippet=line,
        metrics={
            "include": match.group("include"),
            "candidates": [],
            "resolution": "compiler_trace",
        },
    )


def parse_missing_include_targets(
    root: Path,
    cwd: Path,
    source: Path,
    stderr: str,
) -> list[InspectionTarget]:
    """Accept only located missing includes accompanied by a valid trace."""

    targets: list[InspectionTarget] = []
    trace_lines: list[str] = []
    trailer = False
    for line in stderr.splitlines():
        if line == "Multiple include guards may be useful for:":
            trailer = True
            trace_lines.append(line)
            continue
        if trailer:
            trace_lines.append(line)
            continue
        if not line:
            continue
        if _TRACE_RE.fullmatch(line):
            trace_lines.append(line)
            continue
        target = _missing_target(root, cwd, line)
        if target is not None:
            targets.append(target)
            continue
        if _MISSING_CONTEXT_RE.fullmatch(line) is None:
            return []
    try:
        _, unexpected = parse_include_trace(
            "\n".join(trace_lines),
            cwd=cwd,
            source=source,
        )
    except ValueError:
        return []
    return [] if unexpected else targets
