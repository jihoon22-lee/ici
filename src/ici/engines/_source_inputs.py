"""Bounded, stable source snapshots for heuristic analysis engines.

The dead-code and duplicate-code engines inspect repository-controlled input.
They share this intake layer so a changed, escaped, oversized, or malformed
source cannot be silently ignored by one engine and accepted by another.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError

MAX_ANALYSIS_SOURCE_FILES = 2_048
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
    reason: str


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
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return dict(sorted(counts.items()))


class AnalysisSourceError(ValueError):
    """Fail-closed source intake error with a safe project-relative location."""

    def __init__(self, file_path: str, code: str, message: str) -> None:
        super().__init__(message)
        self.file_path = file_path
        self.code = code
        self.message = message


def _relative_path(root: Path, source: Path) -> tuple[Path, str]:
    candidate = source if source.is_absolute() else root / source
    try:
        relative = candidate.relative_to(root)
    except ValueError as err:
        raise AnalysisSourceError(
            ".",
            "outside-project",
            "Selected analysis source is outside the project root",
        ) from err
    if not relative.parts or ".." in relative.parts:
        raise AnalysisSourceError(
            ".",
            "outside-project" if ".." in relative.parts else "not-file",
            "Selected analysis source is outside the project root"
            if ".." in relative.parts
            else "Selected analysis source is not a project file",
        )
    return candidate, relative.as_posix()


def analysis_exclusion_reason(file_path: str) -> str | None:
    """Classify generated and third-party paths without filesystem access."""

    parts = tuple(part.casefold() for part in Path(file_path).parts)
    directories = parts[:-1]
    name = parts[-1] if parts else ""
    if any(part in _VENDOR_PARTS for part in directories):
        return "vendor"
    if any(part in _GENERATED_PARTS or part.endswith("_autogen") for part in directories):
        return "generated"
    if (
        name.startswith(("moc_", "qrc_", "ui_"))
        or name.startswith("mocs_compilation")
        or name.endswith(".moc")
    ):
        return "generated"
    return None


def read_analysis_sources(
    root: Path,
    paths: Iterable[Path],
    *,
    include_generated: bool = False,
    include_vendor: bool = False,
    max_files: int = MAX_ANALYSIS_SOURCE_FILES,
    max_file_bytes: int = MAX_ANALYSIS_SOURCE_BYTES,
    max_total_bytes: int = MAX_ANALYSIS_INVENTORY_BYTES,
) -> AnalysisSourceInventory:
    """Read deterministic, stable and bounded UTF-8 project source snapshots."""

    project_root = root.resolve(strict=True)
    selected: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for source in paths:
        candidate, relative = _relative_path(project_root, source)
        if relative in seen:
            continue
        seen.add(relative)
        selected.append((candidate, relative))
        if len(selected) > max_files:
            raise AnalysisSourceError(
                ".",
                "too-many-files",
                f"Analysis source count exceeds the bounded limit ({max_files})",
            )

    snapshots: list[AnalysisSource] = []
    excluded: list[ExcludedAnalysisSource] = []
    total_bytes = 0
    for candidate, relative in selected:
        reason = analysis_exclusion_reason(relative)
        if reason == "generated" and not include_generated:
            excluded.append(ExcludedAnalysisSource(relative, reason))
            continue
        if reason == "vendor" and not include_vendor:
            excluded.append(ExcludedAnalysisSource(relative, reason))
            continue

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
