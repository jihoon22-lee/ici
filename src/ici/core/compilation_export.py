"""Minimal, redacted public export of ici's measured compilation context."""

from __future__ import annotations

import copy
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path, PureWindowsPath
from typing import Any

from ici import __version__
from ici.core._compilation_export_argv import (
    compiler_record,
    invocation_record,
    undefined_names,
)
from ici.core._compilation_export_io import atomic_write, validate_output
from ici.core._compilation_export_project import discover_export_project
from ici.core.compile_db import load_compilation_context
from ici.core.context import (
    CompilationContext,
    CompilationDiagnostic,
    CompilationUnit,
    ProjectModel,
    canonical_digest,
    discover_project_model,
)
from ici.core.path_utils import resolve_project_path
from ici.core.redaction import (
    _SECRET_DEFINE_RE,
    _redact_compilation_path,
    _redact_path_assignment,
)
from ici.core.redaction_values import REDACTED, redact_text

SCHEMA_VERSION = "ici.compilation-export/v1"
MAX_EXPORT_BYTES = 32 * 1024 * 1024
_SAFE_STANDARD = re.compile(r"[A-Za-z0-9+_.-]{0,64}\Z")
_SAFE_GENERATOR = re.compile(r"[A-Za-z0-9+_. ()-]{1,128}\Z")
_SUPPORTED_ORIGINS = frozenset({"cmake", "configured", "discovered", "qmake"})


class CompilationExportError(ValueError):
    """A safe, user-facing export failure with a stable process exit code."""

    def __init__(self, message: str, *, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def config_with_database(
    config: dict[str, Any], root: Path, database: str | None
) -> dict[str, Any]:
    """Return a config copy with a validated project-relative database override."""

    effective = copy.deepcopy(config)
    if database is None:
        return effective
    windows = PureWindowsPath(database)
    if Path(database).is_absolute() or windows.is_absolute() or windows.drive or "\\" in database:
        raise CompilationExportError(
            "--database must be a project-relative POSIX path",
            exit_code=2,
        )
    try:
        resolved = resolve_project_path(root, database)
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise CompilationExportError(f"unsafe --database path: {error}", exit_code=2) from error
    if not relative or relative == ".":
        raise CompilationExportError(
            "--database must identify a file below the project root",
            exit_code=2,
        )
    project = effective.setdefault("project", {})
    if not isinstance(project, dict):
        raise CompilationExportError(
            "effective project configuration is not a table",
            exit_code=2,
        )
    project["compile_database"] = relative
    return effective


def _path(value: str, root: Path) -> str:
    return _redact_compilation_path(value, root)


def _diagnostic(value: CompilationDiagnostic, root: Path) -> dict[str, Any]:
    return {
        "code": redact_text(value.code),
        "entry_index": value.entry_index,
        "level": value.level,
        "message": redact_text(value.message),
        "source": _path(value.source, root),
    }


def _safe_path_value(value: str, root: Path, directory: str) -> str:
    plain = redact_text(value)
    safe = _redact_path_assignment(value, root)
    if safe != plain or not any(separator in value for separator in ("/", "\\")):
        return safe
    if "\\" in value and os.name != "nt":
        return REDACTED
    left, separator, candidate = value.partition("=")
    path_text = candidate if separator and any(mark in candidate for mark in ("/", "\\")) else value
    quote = (
        path_text[0]
        if len(path_text) >= 2 and path_text[0] in "\"'" and path_text[-1] == path_text[0]
        else ""
    )
    raw_path = path_text[1:-1] if quote else path_text
    try:
        base = (root / directory).resolve(strict=False)
        absolute = (base / raw_path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return REDACTED
    redacted_path = _path(absolute.as_posix(), root)
    redacted = f"{quote}{redacted_path}{quote}"
    return f"{redact_text(left)}={redacted}" if separator and path_text == candidate else redacted


def _definition(value: Any, root: Path, directory: str) -> dict[str, Any]:
    name = redact_text(value.name)
    if value.value is None:
        return {"name": name, "value": None, "value_state": "absent"}
    if _SECRET_DEFINE_RE.search(value.name):
        return {"name": name, "value": REDACTED, "value_state": "redacted"}
    plain = redact_text(value.value)
    safe = _safe_path_value(value.value, root, directory)
    return {
        "name": name,
        "value": safe,
        "value_state": "measured" if safe == plain else "redacted",
    }


def _standard(value: str) -> tuple[str, bool]:
    if _SAFE_STANDARD.fullmatch(value) is not None:
        return redact_text(value), False
    return REDACTED, True


def _generator(value: str) -> tuple[str | None, bool]:
    if not value:
        return None, False
    if _SAFE_GENERATOR.fullmatch(value) is not None:
        return redact_text(value), False
    return REDACTED, True


def _include_records(value: CompilationUnit, root: Path) -> list[dict[str, Any]]:
    return [
        {
            "exists": item.exists if item.scope == "project" else None,
            "kind": item.kind,
            "order": index,
            "path": _path(item.path, root),
            "scope": item.scope,
        }
        for index, item in enumerate(value.include_paths)
    ]


def _unit_inconclusive(
    value: CompilationUnit,
    compiler: dict[str, Any],
    invocation: dict[str, Any],
    definitions: list[dict[str, Any]],
    includes: list[dict[str, Any]],
    sysroot: dict[str, Any] | None,
    *,
    standard_redacted: bool,
) -> bool:
    return any(
        (
            any(item["scope"] == "external" for item in includes),
            bool(sysroot and sysroot["scope"] == "external"),
            any(item["value_state"] == "redacted" for item in definitions),
            standard_redacted,
            compiler["family"] == "unknown",
            bool(invocation["unmodeled_options"]),
            bool(value.diagnostics),
        )
    )


def _unit(value: CompilationUnit, root: Path) -> dict[str, Any]:
    compiler = compiler_record(value, root)
    invocation = invocation_record(value, root)
    definitions = [_definition(item, root, value.directory) for item in value.defines]
    includes = _include_records(value, root)
    sysroot = (
        {"path": _path(value.sysroot, root), "scope": value.sysroot_scope}
        if value.sysroot
        else None
    )
    standard, standard_redacted = _standard(value.standard)
    public_configuration = {
        "compiler": compiler,
        "defines": definitions,
        "directory": _path(value.directory, root),
        "include_paths": includes,
        "invocation": invocation,
        "language": value.language,
        "output": _path(value.output, root),
        "source": _path(value.source, root),
        "standard": standard,
        "sysroot": sysroot,
        "target": redact_text(value.target) or None,
        "undefines": undefined_names(
            value.argv,
            msvc=compiler["family"] in {"msvc", "clang-cl"},
        ),
    }
    inconclusive = _unit_inconclusive(
        value,
        compiler,
        invocation,
        definitions,
        includes,
        sysroot,
        standard_redacted=standard_redacted,
    )
    return {
        **public_configuration,
        "comparison_state": "inconclusive" if inconclusive else "comparable",
        "configuration_digest": canonical_digest(public_configuration),
        "diagnostics": [_diagnostic(item, root) for item in value.diagnostics],
    }


def _units(context: CompilationContext, root: Path) -> list[dict[str, Any]]:
    units = [_unit(item, root) for item in context.units]
    units.sort(
        key=lambda item: (
            item["source"],
            item["target"] or "",
            item["configuration_digest"],
            item["directory"],
            item["output"],
        )
    )
    configuration_counts = Counter((item["source"], item["configuration_digest"]) for item in units)
    source_configurations: dict[str, set[str]] = defaultdict(set)
    for item in units:
        source_configurations[item["source"]].add(item["configuration_digest"])
    for index, item in enumerate(units):
        item["state"] = {
            "duplicate": configuration_counts[(item["source"], item["configuration_digest"])] > 1,
            "source_configuration_count": len(source_configurations[item["source"]]),
            "unit_index": index,
        }
    return units


def _validate_context(context: CompilationContext) -> str:
    database_path = context.database_path
    if database_path is None or not context.database_digest:
        raise CompilationExportError(
            "no measured compilation database is available; use --database or --prepare",
            exit_code=2,
        )
    if context.origin not in _SUPPORTED_ORIGINS:
        raise CompilationExportError("compilation context has no trusted database origin")
    fatal = [item for item in context.diagnostics if item.level == "error"]
    fatal.extend(
        diagnostic
        for unit in context.units
        for diagnostic in unit.diagnostics
        if diagnostic.level == "error"
    )
    if fatal:
        codes = ", ".join(sorted({item.code for item in fatal}))
        raise CompilationExportError(f"compilation context contains fatal diagnostics: {codes}")
    if not context.units:
        raise CompilationExportError(
            "the measured compilation database contains no usable units",
            exit_code=2,
        )
    if any(not unit.compiler for unit in context.units):
        raise CompilationExportError("compilation context contains an unidentified compiler")
    return database_path


def _comparison_state(context: CompilationContext, units: list[dict[str, Any]]) -> str:
    if context.diagnostics or context.unity_build:
        return "inconclusive"
    if any(unit["comparison_state"] == "inconclusive" for unit in units):
        return "inconclusive"
    return "comparable"


def _semantic_digest(
    context: CompilationContext,
    units: list[dict[str, Any]],
    generator: str | None,
) -> str:
    return canonical_digest(
        {
            "generator": generator,
            "origin": context.origin,
            "units": [
                {
                    "configuration_digest": unit["configuration_digest"],
                    "source": unit["source"],
                    "target": unit["target"],
                }
                for unit in units
            ],
            "unity_build": context.unity_build,
        }
    )


def compilation_export_payload(
    project: ProjectModel,
    context: CompilationContext,
) -> dict[str, Any]:
    """Build the standalone v1 export after fail-closed context validation."""

    database_path = _validate_context(context)
    root = project.root
    units = _units(context, root)
    generator, generator_redacted = _generator(context.generator)
    comparison_state = _comparison_state(context, units)
    if generator_redacted:
        comparison_state = "inconclusive"
    return {
        "compilation": {
            "comparison_state": comparison_state,
            "database_path": _path(database_path, root),
            "diagnostics": [_diagnostic(item, root) for item in context.diagnostics],
            "generator": generator,
            "origin": context.origin,
            "semantic_digest": _semantic_digest(context, units, generator),
            "source_bytes_digest": context.database_digest,
            "units": units,
            "unity_build": context.unity_build,
        },
        "evidence": "MEASURED",
        "producer": {"name": "ici", "version": __version__},
        "project": {
            "backend": project.backend,
            "name": redact_text(project.name),
            "type": project.project_type,
            "version": redact_text(project.version),
        },
        "schema_version": SCHEMA_VERSION,
    }


def render_compilation_export(payload: dict[str, Any], *, pretty: bool = False) -> bytes:
    """Serialize a bounded export deterministically."""

    options: dict[str, Any] = {
        "allow_nan": False,
        "ensure_ascii": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    try:
        encoded = (json.dumps(payload, **options) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CompilationExportError(f"could not serialize compilation export: {error}") from error
    if len(encoded) > MAX_EXPORT_BYTES:
        raise CompilationExportError(
            f"compilation export exceeds the {MAX_EXPORT_BYTES} byte output limit"
        )
    return encoded


def write_compilation_export(path: Path, encoded: bytes) -> None:
    """Atomically write to a target returned by :func:`validate_export_output`."""

    if not isinstance(encoded, bytes):
        raise CompilationExportError("compilation export output must be bytes")
    if len(encoded) > MAX_EXPORT_BYTES:
        raise CompilationExportError(
            f"compilation export exceeds the {MAX_EXPORT_BYTES} byte output limit"
        )
    try:
        atomic_write(path, encoded)
    except OSError as error:
        raise CompilationExportError(
            f"could not write compilation export {path}: {error}"
        ) from error


def validate_export_output(
    root: Path,
    output: str,
    database_path: str,
) -> Path | None:
    """Reject output paths that would overwrite an input or project policy file."""

    try:
        return validate_output(root, output, database_path)
    except OSError as error:
        raise CompilationExportError(
            f"could not inspect --output path: {error}",
            exit_code=2,
        ) from error
    except (RuntimeError, ValueError) as error:
        raise CompilationExportError(str(error), exit_code=2) from error


def load_export_context(
    root: Path,
    config: dict[str, Any],
    *,
    prepare: bool = False,
) -> tuple[ProjectModel, CompilationContext]:
    """Discover the project and optionally prepare its canonical compilation database."""

    if not prepare:
        project = discover_export_project(root, config)
        return project, load_compilation_context(root, config)
    project = discover_project_model(root, config)
    if project.backend == "qmake":
        from ici.core.qmake_context import prepare_qmake_compilation_context

        context = prepare_qmake_compilation_context(root, config, project)
    else:
        from ici.core.cmake_context import prepare_cmake_compilation_context

        context = prepare_cmake_compilation_context(root, config, project)
    return project, context
