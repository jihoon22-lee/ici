"""Bounded normalization of ASan, LSan, UBSan, and TSan diagnostics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core.findings import canonical_project_path
from ici.core.models import SourceLocation

MAX_SANITIZER_OUTPUT_BYTES = 1_048_576
# Retain the public-in-tests name while enforcing the stricter UTF-8 byte cap.
MAX_SANITIZER_OUTPUT_CHARS = MAX_SANITIZER_OUTPUT_BYTES
MAX_SANITIZER_DIAGNOSTICS = 64
MAX_SANITIZER_FRAMES = 32
MAX_SANITIZER_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SANITIZER_LINE = 2_147_483_647

_ERROR_RE = re.compile(
    r"^\s*(?:==\d+==)?\s*ERROR:\s*"
    r"(?P<tool>AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer):\s*"
    r"(?P<summary>\S.*)$"
)
_TSAN_WARNING_RE = re.compile(
    r"^\s*(?:==\d+==)?\s*WARNING:\s*"
    r"(?P<tool>ThreadSanitizer):\s*(?P<summary>\S.*)$"
)
_SUMMARY_RE = re.compile(
    r"^\s*(?:==\d+==)?\s*SUMMARY:\s*"
    r"(?P<tool>AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|ThreadSanitizer):\s*"
    r"(?P<summary>\S.*)$"
)
_PATH = r"(?:[A-Za-z]:[\\/][^:\r\n]*|/[^:\r\n]*|(?:\.{1,2}[\\/])?[^:\s]+)"
_LOCATION_RE = re.compile(rf"(?P<path>{_PATH}):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?")
_RUNTIME_RE = re.compile(
    rf"^(?P<location>{_PATH}:[1-9]\d*(?::[1-9]\d*)?):\s*"
    r"runtime error:\s*(?P<summary>\S.*)$"
)
_STACK_RE = re.compile(r"^\s*#(?P<index>\d+)\s+")
_ADDRESS_RE = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\b\d+\b")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_TSAN_DEFECT_PREFIXES = (
    ("data race", "data-race"),
    ("lock-order-inversion", "lock-order-inversion"),
    ("thread leak", "thread-leak"),
    ("mutex destroyed while busy", "mutex-destroyed-while-busy"),
    ("unlock of an unlocked mutex", "invalid-mutex-unlock"),
    ("double lock of a mutex", "double-mutex-lock"),
)

_MEMORY_DEFECT_PREFIXES = (
    ("heap-use-after-free", "heap-use-after-free"),
    ("stack-use-after-return", "stack-use-after-return"),
    ("stack-use-after-scope", "stack-use-after-scope"),
    ("heap-buffer-overflow", "heap-buffer-overflow"),
    ("stack-buffer-overflow", "stack-buffer-overflow"),
    ("global-buffer-overflow", "global-buffer-overflow"),
    ("container-overflow", "container-overflow"),
    ("initialization-order-fiasco", "initialization-order-fiasco"),
    ("alloc-dealloc-mismatch", "alloc-dealloc-mismatch"),
    ("new-delete-type-mismatch", "new-delete-type-mismatch"),
    ("attempting double-free", "double-free"),
    ("attempting free on address", "invalid-free"),
    ("negative-size-param", "negative-size-param"),
    ("detected memory leaks", "memory-leak"),
    ("direct leak", "memory-leak"),
    ("indirect leak", "memory-leak"),
    ("signed integer overflow", "signed-integer-overflow"),
    ("unsigned integer overflow", "unsigned-integer-overflow"),
    ("division by zero", "division-by-zero"),
    ("shift exponent", "invalid-shift"),
    ("shift base", "invalid-shift"),
    ("load of misaligned address", "misaligned-load"),
    ("store to misaligned address", "misaligned-store"),
    ("reference binding to misaligned address", "misaligned-reference"),
    ("member access within", "invalid-member-access"),
    ("index ", "out-of-bounds-index"),
    ("null pointer", "null-pointer"),
    ("applying non-zero offset", "invalid-pointer-offset"),
    ("implicit conversion", "implicit-conversion"),
    ("execution reached an unreachable", "unreachable"),
)


class SanitizerDiagnosticError(ValueError):
    """The sanitizer transcript could not be safely normalized."""


@dataclass(frozen=True)
class SanitizerDiagnostic:
    """One normalized runtime-sanitizer report."""

    kind: str
    tool_name: str
    defect: str
    rule_id: str
    message: str
    primary_location: SourceLocation | None
    related_locations: tuple[SourceLocation, ...]
    frames_observed: int
    project_frames: int


def _kind(tool: str, summary: str) -> str:
    lowered = summary.casefold()
    if tool == "ThreadSanitizer":
        return "tsan"
    if tool == "LeakSanitizer" or "leak" in lowered:
        return "lsan"
    if tool == "UndefinedBehaviorSanitizer":
        return "ubsan"
    return "asan"


def _defect(tool: str, summary: str) -> str:
    lowered = " ".join(summary.casefold().split())
    prefixes = _TSAN_DEFECT_PREFIXES if tool == "ThreadSanitizer" else _MEMORY_DEFECT_PREFIXES
    for prefix, defect in prefixes:
        if lowered.startswith(prefix) or f": {prefix}" in lowered:
            return defect
    if tool == "ThreadSanitizer":
        # TSan summaries may contain unstable addresses, process details, and
        # free-form runtime wording. Never derive a public rule ID from that
        # text when it is outside the explicit taxonomy above.
        return "thread-safety-defect"
    stable = _ADDRESS_RE.sub("address", lowered)
    stable = _NUMBER_RE.sub("number", stable)
    stable = stable.split(" on address", 1)[0].split(" at address", 1)[0]
    stable = stable.split(":", 1)[0]
    slug = _SLUG_RE.sub("-", stable).strip("-")
    return slug[:80].rstrip("-") or "runtime-defect"


def _safe_source_location(
    raw_path: str,
    line: int,
    column: int | None,
    project_root: Path,
    source_cache: dict[Path, tuple[str, ...] | None],
    *,
    label: str,
) -> SourceLocation | None:
    if not 0 < line <= MAX_SANITIZER_LINE:
        return None
    if column is not None and not 0 < column <= MAX_SANITIZER_LINE:
        return None
    try:
        relative = canonical_project_path(raw_path, project_root)
        candidate = project_root / relative
        candidate.relative_to(project_root)
    except (OSError, RuntimeError, ValueError):
        return None

    if candidate not in source_cache:
        try:
            payload = _read_bounded_regular(
                candidate,
                MAX_SANITIZER_SOURCE_BYTES,
                containment_root=project_root,
            )
            if b"\x00" in payload:
                source_cache[candidate] = None
            else:
                source_cache[candidate] = tuple(payload.decode("utf-8").splitlines())
        except (FileNotFoundError, _ReadError, UnicodeError):
            source_cache[candidate] = None
    lines = source_cache[candidate]
    if lines is None or line > len(lines):
        return None
    if column is not None and column > len(lines[line - 1].encode("utf-8")) + 1:
        return None
    return SourceLocation(
        path=candidate.relative_to(project_root).as_posix(),
        start_line=line,
        start_column=column,
        label=label[:512],
    )


def _normalized_location(
    raw_path: str,
    line: int,
    column: int | None,
    project_root: Path,
    source_cache: dict[Path, tuple[str, ...] | None],
    *,
    label: str,
) -> SourceLocation | None:
    """Return a validated project location or a redacted external sentinel."""

    if not 0 < line <= MAX_SANITIZER_LINE:
        return None
    if column is not None and not 0 < column <= MAX_SANITIZER_LINE:
        return None
    try:
        canonical_project_path(raw_path, project_root)
    except ValueError:
        return SourceLocation(
            path="[external]",
            start_line=line,
            start_column=column,
            label=label[:512],
        )
    return _safe_source_location(
        raw_path,
        line,
        column,
        project_root,
        source_cache,
        label=label,
    )


def _location_from_line(
    text: str,
    project_root: Path,
    source_cache: dict[Path, tuple[str, ...] | None],
    *,
    label: str,
) -> SourceLocation | None:
    matches = list(_LOCATION_RE.finditer(text))
    if not matches:
        return None
    match = matches[-1]
    return _normalized_location(
        match.group("path"),
        int(match.group("line")),
        int(match.group("column")) if match.group("column") else None,
        project_root,
        source_cache,
        label=label,
    )


def _function_label(line: str, location_start: int, frame_index: int) -> str:
    prefix = line[:location_start].strip()
    function = prefix.rsplit(" in ", 1)[1].strip() if " in " in prefix else ""
    function = _ADDRESS_RE.sub("address", function)
    function = " ".join(function.split())[:480]
    return f"frame #{frame_index}: {function}".rstrip(": ")


def _locations(
    lines: list[str],
    project_root: Path,
    source_cache: dict[Path, tuple[str, ...] | None],
) -> tuple[SourceLocation | None, tuple[SourceLocation, ...], int, int]:
    observed = 0
    runtime_locations: list[SourceLocation] = []
    frame_locations: list[SourceLocation] = []
    marker_locations: list[SourceLocation] = []
    project_frames = 0
    for line in lines:
        runtime = _RUNTIME_RE.match(line)
        if runtime is not None:
            location = _location_from_line(
                runtime.group("location"),
                project_root,
                source_cache,
                label="runtime error",
            )
            if location is not None:
                runtime_locations.append(location)
            continue
        frame = _STACK_RE.match(line)
        if frame is None:
            if (
                _ERROR_RE.match(line) is not None
                or _TSAN_WARNING_RE.match(line) is not None
                or _SUMMARY_RE.match(line) is not None
            ):
                location = _location_from_line(
                    line,
                    project_root,
                    source_cache,
                    label="sanitizer diagnostic",
                )
                if location is not None:
                    marker_locations.append(location)
            continue
        observed += 1
        if observed > MAX_SANITIZER_FRAMES:
            raise SanitizerDiagnosticError("sanitizer stack exceeds the bounded frame limit")
        matches = list(_LOCATION_RE.finditer(line))
        if not matches:
            continue
        match = matches[-1]
        location = _normalized_location(
            match.group("path"),
            int(match.group("line")),
            int(match.group("column")) if match.group("column") else None,
            project_root,
            source_cache,
            label=_function_label(line, match.start(), int(frame.group("index"))),
        )
        if location is not None:
            frame_locations.append(location)
            if location.path != "[external]":
                project_frames += 1

    owned_runtime = [item for item in runtime_locations if item.path != "[external]"]
    owned_frames = [item for item in frame_locations if item.path != "[external]"]
    owned_markers = [item for item in marker_locations if item.path != "[external]"]
    primary = next(iter((*owned_runtime, *owned_frames, *owned_markers)), None)
    locations = [*runtime_locations, *frame_locations]
    if not owned_runtime and not owned_frames:
        locations.extend(marker_locations)
    unique: list[SourceLocation] = []
    seen: set[tuple[str, int, int | None]] = set()
    for location in locations:
        key = (location.path, location.start_line, location.start_column)
        if key not in seen:
            unique.append(location)
            seen.add(key)
    related: list[SourceLocation] = []
    removed_primary = False
    for location in unique:
        if not removed_primary and primary is not None and location == primary:
            removed_primary = True
            continue
        related.append(location)
    return primary, tuple(related), observed, project_frames


def _diagnostic(
    tool: str,
    summary: str,
    block: list[str],
    project_root: Path,
    source_cache: dict[Path, tuple[str, ...] | None],
) -> SanitizerDiagnostic:
    kind = _kind(tool, summary)
    defect = _defect(tool, summary)
    primary, related, observed, project_frames = _locations(block, project_root, source_cache)
    return SanitizerDiagnostic(
        kind=kind,
        tool_name=tool,
        defect=defect,
        rule_id=f"ici.sanitize.{kind}.{defect}",
        message=f"{tool} detected {defect.replace('-', ' ')}",
        primary_location=primary,
        related_locations=related,
        frames_observed=observed,
        project_frames=project_frames,
    )


def parse_sanitizer_diagnostics(
    output: str,
    project_root: Path,
) -> tuple[SanitizerDiagnostic, ...]:
    """Parse a bounded sanitizer transcript into deterministic diagnostics."""

    if not isinstance(output, str):
        raise SanitizerDiagnosticError("sanitizer output must be text")
    try:
        encoded = output.encode("utf-8")
    except UnicodeError as err:
        raise SanitizerDiagnosticError("sanitizer output is not valid UTF-8 text") from err
    if len(encoded) > MAX_SANITIZER_OUTPUT_BYTES:
        raise SanitizerDiagnosticError("sanitizer output exceeds the bounded UTF-8 byte limit")
    if "\x00" in output:
        raise SanitizerDiagnosticError("sanitizer output contains NUL bytes")
    try:
        root = project_root.resolve(strict=True)
    except (OSError, RuntimeError) as err:
        raise SanitizerDiagnosticError("project root is unavailable") from err

    lines = output.splitlines()
    starters: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        error = _ERROR_RE.match(line)
        if error is not None:
            starters.append((index, error.group("tool"), error.group("summary")))
            continue
        warning = _TSAN_WARNING_RE.match(line)
        if warning is not None:
            starters.append((index, warning.group("tool"), warning.group("summary")))
            continue
        runtime = _RUNTIME_RE.match(line)
        if runtime is not None:
            starters.append((index, "UndefinedBehaviorSanitizer", runtime.group("summary")))
    if not starters:
        for index, line in enumerate(lines):
            summary_match = _SUMMARY_RE.match(line)
            if summary_match is not None:
                starters.append(
                    (index, summary_match.group("tool"), summary_match.group("summary"))
                )
    if len(starters) > MAX_SANITIZER_DIAGNOSTICS:
        raise SanitizerDiagnosticError("sanitizer output exceeds the diagnostic limit")

    source_cache: dict[Path, tuple[str, ...] | None] = {}
    diagnostics: list[SanitizerDiagnostic] = []
    for position, (start, tool, summary_text) in enumerate(starters):
        end = starters[position + 1][0] if position + 1 < len(starters) else len(lines)
        diagnostics.append(_diagnostic(tool, summary_text, lines[start:end], root, source_cache))
    return tuple(diagnostics)
