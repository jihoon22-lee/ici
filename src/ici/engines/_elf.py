"""Parse bounded GNU readelf evidence without loading or executing a binary."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADER_RE = re.compile(r"^\s*(Class|Machine|Type):\s*(.+?)\s*$", re.MULTILINE)
_NEEDED_RE = re.compile(r"\(NEEDED\).*?\[(.*?)\]")
_RPATH_RE = re.compile(r"\((RPATH|RUNPATH)\).*?\[(.*?)\]")
_VERSION_RE = re.compile(r"\b(GLIBCXX|GLIBC|CXXABI)_(\d+(?:\.\d+)+)\b")
_SECTION_RE = re.compile(r"\]\s+(\.\S+)\s+")


class ElfParseError(ValueError):
    """Raised when readelf output lacks the required structural facts."""


@dataclass(frozen=True)
class ElfFacts:
    elf_class: str
    machine: str
    elf_type: str
    needed: tuple[str, ...]
    rpath: tuple[str, ...]
    runpath: tuple[str, ...]
    glibc: tuple[str, ...]
    glibcxx: tuple[str, ...]
    cxxabi: tuple[str, ...]
    stripped: bool


def version_key(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError as err:
        raise ElfParseError(f"invalid ABI version: {value!r}") from err


def maximum_version(values: tuple[str, ...]) -> str:
    return max(values, key=version_key) if values else ""


def parse_readelf(text: str) -> ElfFacts:
    """Parse one combined ``readelf -h -S -d -V --wide`` transcript."""

    if not isinstance(text, str) or len(text) > 8 * 1024 * 1024 or "\x00" in text:
        raise ElfParseError("readelf output exceeds the bounded text contract")
    headers = {name.casefold(): value.strip() for name, value in _HEADER_RE.findall(text)}
    if not all(headers.get(name) for name in ("class", "machine", "type")):
        raise ElfParseError("readelf output is missing ELF header facts")
    paths: dict[str, list[str]] = {"RPATH": [], "RUNPATH": []}
    for kind, value in _RPATH_RE.findall(text):
        paths[kind].extend(part for part in value.split(":") if part)
    versions: dict[str, set[str]] = {"GLIBC": set(), "GLIBCXX": set(), "CXXABI": set()}
    for namespace, value in _VERSION_RE.findall(text):
        version_key(value)
        versions[namespace].add(value)
    sections = set(_SECTION_RE.findall(text))
    return ElfFacts(
        elf_class=headers["class"].split()[0],
        machine=headers["machine"],
        elf_type=headers["type"].split()[0],
        needed=tuple(sorted(set(_NEEDED_RE.findall(text)))),
        rpath=tuple(paths["RPATH"]),
        runpath=tuple(paths["RUNPATH"]),
        glibc=tuple(sorted(versions["GLIBC"], key=version_key)),
        glibcxx=tuple(sorted(versions["GLIBCXX"], key=version_key)),
        cxxabi=tuple(sorted(versions["CXXABI"], key=version_key)),
        stripped=".symtab" not in sections,
    )
