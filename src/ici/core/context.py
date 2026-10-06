"""Immutable compilation contracts for one ici analysis run.

The compilation context is input state: whoever plans work around a compile
database reads this validated, immutable description of it. These types are
shared by the compile-DB readers (``ici.core.compile_db``) and the workspace
layer that turns declarations into planned checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def canonical_digest(value: Any) -> str:
    """Hash JSON-compatible data independently of mapping insertion order."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise ValueError(f"value is not canonical JSON data: {err}") from err
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class CompilationDiagnostic:
    """Bounded, reporting-safe evidence from compile database ingestion."""

    code: str
    message: str
    level: str = "warning"
    entry_index: int | None = None
    source: str = ""

    def __post_init__(self) -> None:
        for field_name in ("code", "message", "level", "source"):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"compilation diagnostic {field_name} must be a string")
        if not self.code or not self.message:
            raise ValueError("compilation diagnostic code and message must not be empty")
        if self.level not in {"info", "warning", "error"}:
            raise ValueError(f"unsupported compilation diagnostic level: {self.level!r}")
        if self.entry_index is not None and (
            type(self.entry_index) is not int or self.entry_index < 0
        ):
            raise ValueError("compilation diagnostic entry index must be non-negative")
        if self.source:
            _validate_relative_path(self.source, "compilation diagnostic source", allow_dot=False)


@dataclass(frozen=True)
class CompilationDefine:
    """One compiler preprocessor definition without losing its optional value."""

    name: str
    value: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise ValueError("compilation define name must be a string")
        if not self.name or any(character.isspace() for character in self.name):
            raise ValueError("compilation define name must be a non-empty token")
        if self.value is not None and not isinstance(self.value, str):
            raise ValueError("compilation define value must be a string or null")


@dataclass(frozen=True)
class CompilationSearchPath:
    """One normalized compiler header search path and its trust scope."""

    path: str
    kind: str
    scope: str
    exists: bool

    def __post_init__(self) -> None:
        for field_name in ("path", "kind", "scope"):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"compilation search path {field_name} must be a string")
        if self.kind not in {"include", "system", "quote"}:
            raise ValueError(f"unsupported compilation search path kind: {self.kind!r}")
        if self.scope not in {"project", "external"}:
            raise ValueError(f"unsupported compilation search path scope: {self.scope!r}")
        if type(self.exists) is not bool:
            raise ValueError("compilation search path exists flag must be a boolean")
        if self.scope == "project":
            _validate_relative_path(self.path, "compilation search path", allow_dot=True)
        elif not isinstance(self.path, str) or not self.path:
            raise ValueError("external compilation search path must not be empty")


def _typed_tuple(values: Any, expected: type, description: str) -> tuple[Any, ...]:
    normalized = _collection_tuple(values, description)
    if not all(isinstance(item, expected) for item in normalized):
        raise ValueError(f"{description} must contain {expected.__name__} values")
    return normalized


def _collection_tuple(values: Any, description: str) -> tuple[Any, ...]:
    """Normalize a model collection while turning boundary TypeErrors into ValueErrors."""

    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(values, Iterable):
        raise ValueError(f"{description} must be an iterable collection")
    try:
        return tuple(values)
    except TypeError as err:
        raise ValueError(f"{description} must be an iterable collection") from err


def _validate_compilation_unit_scalars(unit: CompilationUnit) -> None:
    _validate_relative_path(unit.source, "compilation source", allow_dot=False)
    _validate_relative_path(unit.directory, "compilation directory", allow_dot=True)
    if not isinstance(unit.output, str):
        raise ValueError("compilation output must be a string")
    if unit.output:
        _validate_relative_path(unit.output, "compilation output", allow_dot=False)
    if not unit.argv or not all(isinstance(item, str) and item for item in unit.argv):
        raise ValueError("compilation argv must contain non-empty strings")
    for field_name in ("compiler", "language", "standard", "target", "configuration"):
        if not isinstance(getattr(unit, field_name), str):
            raise ValueError(f"compilation {field_name} must be a string")
    if unit.target and (
        len(unit.target) > 512
        or any(character in unit.target for character in ("/", "\\", "\0", "\n", "\r"))
    ):
        raise ValueError("compilation target must be a bounded name")
    if unit.language and unit.language not in {"c", "c++", "objective-c", "objective-c++"}:
        raise ValueError(f"unsupported compilation language: {unit.language!r}")
    if unit.configuration and _DIGEST_RE.fullmatch(unit.configuration) is None:
        raise ValueError("compilation configuration must be a sha256 digest")


def _validate_compilation_sysroot(unit: CompilationUnit) -> None:
    for field_name in ("sysroot", "sysroot_scope"):
        if not isinstance(getattr(unit, field_name), str):
            raise ValueError(f"compilation {field_name} must be a string")
    if unit.sysroot_scope not in {"", "project", "external"}:
        raise ValueError(f"unsupported compilation sysroot scope: {unit.sysroot_scope!r}")
    if bool(unit.sysroot) != bool(unit.sysroot_scope):
        raise ValueError("compilation sysroot and scope must be declared together")
    if unit.sysroot_scope == "project":
        _validate_relative_path(unit.sysroot, "compilation sysroot", allow_dot=True)
    elif unit.sysroot and not isinstance(unit.sysroot, str):
        raise ValueError("external compilation sysroot must be a string")


@dataclass(frozen=True)
class CompilationUnit:
    """One normalized compile invocation with immutable provenance."""

    source: str
    directory: str
    argv: tuple[str, ...]
    output: str = ""
    compiler: str = ""
    language: str = ""
    standard: str = ""
    target: str = ""
    defines: tuple[CompilationDefine, ...] = ()
    include_paths: tuple[CompilationSearchPath, ...] = ()
    sysroot: str = ""
    sysroot_scope: str = ""
    configuration: str = ""
    diagnostics: tuple[CompilationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        argv = _collection_tuple(self.argv, "compilation argv")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("compilation argv must contain non-empty strings")
        object.__setattr__(self, "argv", argv)
        defines = _typed_tuple(self.defines, CompilationDefine, "compilation defines")
        include_paths = _typed_tuple(
            self.include_paths,
            CompilationSearchPath,
            "compilation include paths",
        )
        diagnostics = _typed_tuple(
            self.diagnostics,
            CompilationDiagnostic,
            "compilation unit diagnostics",
        )
        object.__setattr__(self, "defines", defines)
        object.__setattr__(self, "include_paths", include_paths)
        object.__setattr__(self, "diagnostics", diagnostics)
        _validate_compilation_unit_scalars(self)
        _validate_compilation_sysroot(self)


@dataclass(frozen=True)
class CompilationContext:
    """Immutable owner of compilation database observations."""

    units: tuple[CompilationUnit, ...] = ()
    database_path: str | None = None
    database_digest: str = ""
    origin: str = ""
    generator: str = ""
    unity_build: bool | None = None
    diagnostics: tuple[CompilationDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        units = _typed_tuple(self.units, CompilationUnit, "compilation context units")
        if self.database_path is not None:
            if not isinstance(self.database_path, str):
                raise ValueError("compilation database path must be a string or null")
            _validate_relative_path(self.database_path, "compilation database", allow_dot=False)
        if not isinstance(self.database_digest, str):
            raise ValueError("compilation database digest must be a string")
        if self.database_digest and _DIGEST_RE.fullmatch(self.database_digest) is None:
            raise ValueError("compilation database digest must be a sha256 digest")
        if not isinstance(self.origin, str) or self.origin not in {
            "",
            "configured",
            "discovered",
            "cmake",
            "qmake",
        }:
            raise ValueError(f"unsupported compilation database origin: {self.origin!r}")
        if not isinstance(self.generator, str) or len(self.generator) > 512:
            raise ValueError("compilation generator must be a bounded string")
        if self.unity_build is not None and type(self.unity_build) is not bool:
            raise ValueError("compilation unity build flag must be boolean or null")
        diagnostics = _typed_tuple(
            self.diagnostics,
            CompilationDiagnostic,
            "compilation context diagnostics",
        )
        object.__setattr__(self, "units", units)
        object.__setattr__(self, "diagnostics", diagnostics)


def _validate_relative_path(value: str, description: str, *, allow_dot: bool) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{description} path must be non-empty project-relative POSIX form")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{description} path must be relative and contained: {value!r}")
    canonical = path.as_posix()
    if canonical != value or (not allow_dot and canonical == "."):
        raise ValueError(f"{description} path must be canonical: {value!r}")
