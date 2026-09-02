"""Bounded, stable source snapshots for heuristic analysis engines.

The dead-code and duplicate-code engines inspect repository-controlled input.
They share this intake layer so a changed, escaped, oversized, or malformed
source cannot be silently ignored by one engine and accepted by another.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError

MAX_ANALYSIS_SOURCE_FILES = 2_048
MAX_ANALYSIS_SOURCE_CANDIDATES = 8_192
MAX_ANALYSIS_SOURCE_BYTES = 8 * 1024 * 1024
MAX_ANALYSIS_INVENTORY_BYTES = 64 * 1024 * 1024

_LANGUAGES = {
    ".c": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".moc": "cpp",
    ".py": "python",
}
_GENERATED_PARTS = frozenset({"generated", "autogen"})
_VENDOR_PARTS = frozenset(
    {
        "_deps",
        "deps",
        "extern",
        "external",
        "node_modules",
        "subprojects",
        "third-party",
        "third_party",
        "vendor",
    }
)


@dataclass(frozen=True)
class AnalysisSource:
    """One immutable UTF-8 source snapshot inside the project root."""

    path: Path
    file_path: str
    language: str
    text: str
    byte_size: int

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines(keepends=True)


@dataclass(frozen=True)
class ExcludedAnalysisSource:
    """One selected source omitted by the default ownership policy."""

    file_path: str
    reasons: tuple[str, ...]

    @property
    def reason(self) -> str:
        """Return the first blocking reason for compatibility with older callers."""

        return self.reasons[0]


@dataclass(frozen=True)
class AnalysisSourceInventory:
    """Bounded source snapshots plus deterministic exclusion evidence."""

    sources: tuple[AnalysisSource, ...]
    excluded: tuple[ExcludedAnalysisSource, ...]
    total_bytes: int

    @property
    def exclusion_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.excluded:
            for reason in item.reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))


class AnalysisSourceError(ValueError):
    """Fail-closed source intake error with a safe project-relative location."""

    def __init__(self, file_path: str, code: str, message: str) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.code = code
        self.message = message


def _relative_path(root: Path, source: Path) -> tuple[Path, str]:
    lexical = source if source.is_absolute() else root / source
    try:
        # Normalize lexical ``.``/``..`` segments without resolving symlinks.
        # A path that returns to the project is safe to name; the descriptor
        # reader below independently rejects symlink traversal while opening.
        candidate = Path(os.path.abspath(os.fspath(lexical)))
        relative = candidate.relative_to(root)
    except (OSError, RuntimeError, TypeError, ValueError) as err:
        raise AnalysisSourceError(
            ".",
            "outside-project",
            "Selected analysis source is outside the project root",
        ) from err
    if not relative.parts:
        raise AnalysisSourceError(
            ".",
            "not-file",
            "Selected analysis source is not a project file",
        )
    return candidate, relative.as_posix()


def analysis_exclusion_reasons(file_path: str) -> tuple[str, ...]:
    """Classify every generated/third-party property without filesystem access."""

    parts = tuple(part.casefold() for part in Path(file_path).parts)
    directories = parts[:-1]
    name = parts[-1] if parts else ""
    reasons: list[str] = []
    if any(part in _VENDOR_PARTS for part in directories):
        reasons.append("vendor")
    generated = any(
        part in _GENERATED_PARTS or part.endswith("_autogen") for part in directories
    ) or (
        name.startswith(("moc_", "qrc_", "ui_"))
        or name.startswith("mocs_compilation")
        or name.endswith(".moc")
    )
    if generated:
        reasons.append("generated")
    return tuple(reasons)


def analysis_exclusion_reason(file_path: str) -> str | None:
    """Return the first exclusion reason for compatibility with existing callers."""

    reasons = analysis_exclusion_reasons(file_path)
    return reasons[0] if reasons else None


def _positive_limit(name: str, value: object) -> int:
    if type(value) is not int or value <= 0:
        raise AnalysisSourceError(
            ".",
            "invalid-limit",
            f"{name} must be a positive integer",
        )
    return value


def read_analysis_sources(
    root: Path,
    paths: Iterable[Path],
    *,
    include_generated: bool = False,
    include_vendor: bool = False,
    max_files: int = MAX_ANALYSIS_SOURCE_FILES,
    max_candidates: int = MAX_ANALYSIS_SOURCE_CANDIDATES,
    max_file_bytes: int = MAX_ANALYSIS_SOURCE_BYTES,
    max_total_bytes: int = MAX_ANALYSIS_INVENTORY_BYTES,
) -> AnalysisSourceInventory:
    """Read deterministic, stable and bounded UTF-8 project source snapshots."""

    max_files = _positive_limit("max_files", max_files)
    max_candidates = _positive_limit("max_candidates", max_candidates)
    max_file_bytes = _positive_limit("max_file_bytes", max_file_bytes)
    max_total_bytes = _positive_limit("max_total_bytes", max_total_bytes)
    project_root = root.resolve(strict=True)
    selected: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for source in paths:
        candidate, relative = _relative_path(project_root, source)
        if relative in seen:
            continue
        seen.add(relative)
        selected.append((candidate, relative))
        if len(selected) > max_candidates:
            raise AnalysisSourceError(
                ".",
                "too-many-candidates",
                f"Analysis source candidate count exceeds the bounded limit ({max_candidates})",
            )

    selected.sort(key=lambda item: item[1])

    snapshots: list[AnalysisSource] = []
    excluded: list[ExcludedAnalysisSource] = []
    total_bytes = 0
    for candidate, relative in selected:
        reasons = analysis_exclusion_reasons(relative)
        blocked = tuple(
            reason
            for reason in reasons
            if (reason == "generated" and not include_generated)
            or (reason == "vendor" and not include_vendor)
        )
        if blocked:
            excluded.append(ExcludedAnalysisSource(relative, blocked))
            continue

        if len(snapshots) >= max_files:
            raise AnalysisSourceError(
                ".",
                "too-many-files",
                f"Owned analysis source count exceeds the bounded limit ({max_files})",
            )

        language = _LANGUAGES.get(candidate.suffix.casefold())
        if language is None:
            raise AnalysisSourceError(
                relative,
                "unsupported-language",
                f"Unsupported analysis source type: {candidate.suffix or '<none>'}",
            )
        try:
            payload = _read_bounded_regular(
                candidate,
                max_file_bytes,
                containment_root=project_root,
            )
        except FileNotFoundError as err:
            raise AnalysisSourceError(
                relative,
                "missing",
                "Analysis source disappeared before it could be read",
            ) from err
        except _ReadError as err:
            raise AnalysisSourceError(
                relative,
                err.code,
                f"Analysis source could not be read safely: {err.message}",
            ) from err

        total_bytes += len(payload)
        if total_bytes > max_total_bytes:
            raise AnalysisSourceError(
                relative,
                "inventory-too-large",
                f"Analysis source inventory exceeds the bounded limit ({max_total_bytes} bytes)",
            )
        try:
            text = payload.decode("utf-8", errors="strict")
        except UnicodeDecodeError as err:
            raise AnalysisSourceError(
                relative,
                "invalid-utf8",
                "Analysis source is not valid UTF-8",
            ) from err
        if "\x00" in text:
            raise AnalysisSourceError(
                relative,
                "invalid-text",
                "Analysis source contains a NUL character",
            )
        snapshots.append(
            AnalysisSource(
                path=candidate,
                file_path=relative,
                language=language,
                text=text,
                byte_size=len(payload),
            )
        )

    return AnalysisSourceInventory(tuple(snapshots), tuple(excluded), total_bytes)
