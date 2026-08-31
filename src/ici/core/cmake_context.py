"""Canonical CMake compilation-database preflight.

The preflight owns one uninstrumented CMake shadow and finishes before the
immutable :class:`AnalysisContext` and cache keys are created.  It never
executes commands recovered from ``compile_commands.json``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core.cmake import BACKEND_CMAKE, ConfigureOptions, build, configure
from ici.core.compile_db import load_compilation_context
from ici.core.context import (
    BuildVariant,
    CompilationContext,
    CompilationDiagnostic,
    ProjectModel,
)

MAX_CMAKE_CACHE_BYTES = 4 * 1024 * 1024
_CMAKE_DATABASE = "build/ici-cmake-build/compile_commands.json"
_CMAKE_CACHE = "build/ici-cmake-build/CMakeCache.txt"
_TRUE_VALUES = frozenset({"1", "on", "true", "yes", "y"})
_FALSE_VALUES = frozenset({"0", "off", "false", "no", "n", "ignore", "notfound", ""})


def _diagnostic(code: str, message: str, *, level: str = "error") -> CompilationDiagnostic:
    return CompilationDiagnostic(code=code, message=message, level=level)


def _with_diagnostics(
    context: CompilationContext,
    *diagnostics: CompilationDiagnostic,
) -> CompilationContext:
    return replace(context, diagnostics=(*context.diagnostics, *diagnostics))


def _cache_values(encoded: bytes) -> dict[str, str]:
    try:
        text = encoded.decode("utf-8")
    except UnicodeError as err:
        raise ValueError("CMake cache is not UTF-8") from err
    if "\0" in text:
        raise ValueError("CMake cache contains a null byte")
    values: dict[str, str] = {}
    wanted = {"CMAKE_GENERATOR", "CMAKE_UNITY_BUILD", "CMAKE_EXPORT_COMPILE_COMMANDS"}
    for line in text.splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line or ":" not in line:
            continue
        declaration, value = line.split("=", 1)
        name, _kind = declaration.split(":", 1)
        if name not in wanted:
            continue
        if name in values or len(value) > 512 or any(ord(character) < 32 for character in value):
            raise ValueError("CMake cache metadata is malformed")
        values[name] = value
    return values


def _cmake_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES or normalized.endswith("-notfound"):
        return False
    return None


def _generator_supported(generator: str) -> bool:
    return generator.startswith("Ninja") or generator.endswith("Makefiles")


def _read_cmake_metadata(root: Path) -> tuple[str, bool | None, tuple[CompilationDiagnostic, ...]]:
    cache = root / _CMAKE_CACHE
    try:
        values = _cache_values(_read_bounded_regular(cache, MAX_CMAKE_CACHE_BYTES))
    except FileNotFoundError:
        return (
            "",
            None,
            (_diagnostic("cmake-cache-missing", "The canonical CMake cache was not generated."),),
        )
    except (_ReadError, ValueError):
        return (
            "",
            None,
            (_diagnostic("cmake-cache-invalid", "The canonical CMake cache is not safe to read."),),
        )

    generator = values.get("CMAKE_GENERATOR", "")
    unity_value = values.get("CMAKE_UNITY_BUILD")
    unity = _cmake_bool(unity_value)
    diagnostics: list[CompilationDiagnostic] = []
    if not generator:
        diagnostics.append(
            _diagnostic("cmake-generator-missing", "The canonical CMake generator is unknown.")
        )
    elif not _generator_supported(generator):
        diagnostics.append(
            _diagnostic(
                "cmake-generator-unsupported",
                "The selected CMake generator cannot provide exact compile commands.",
            )
        )
    if values.get("CMAKE_EXPORT_COMPILE_COMMANDS", "").casefold() not in _TRUE_VALUES:
        diagnostics.append(
            _diagnostic(
                "cmake-export-disabled",
                "The canonical CMake configure did not enable compile-command export.",
            )
        )
    if unity_value is not None and unity is None:
        diagnostics.append(
            _diagnostic(
                "cmake-unity-unknown",
                "The canonical CMake unity-build setting could not be interpreted safely.",
            )
        )
    return generator, unity, tuple(diagnostics)


def _generated_config(config: dict[str, Any]) -> dict[str, Any]:
    generated = dict(config)
    project_value = config.get("project", {})
    project = dict(project_value) if isinstance(project_value, dict) else {}
    project["compile_database"] = _CMAKE_DATABASE
    generated["project"] = project
    return generated


def _load_generated_context(
    root: Path,
    config: dict[str, Any],
    *,
    generator: str,
    unity_build: bool | None,
    diagnostics: tuple[CompilationDiagnostic, ...],
) -> CompilationContext:
    context = load_compilation_context(root, _generated_config(config))
    return replace(
        context,
        origin="cmake",
        generator=generator,
        unity_build=unity_build,
        diagnostics=(*context.diagnostics, *diagnostics),
    )


def _has_unity_units(context: CompilationContext) -> bool:
    for unit in context.units:
        parts = Path(unit.source).parts
        if any(part.casefold() == "unity" for part in parts):
            return True
        if Path(unit.source).name.casefold().startswith("unity_"):
            return True
    return False


def _needs_generation_build(context: CompilationContext) -> bool:
    prefix = "build/ici-cmake-build/"
    return any(
        diagnostic.code == "stale-source" and unit.source.startswith(prefix)
        for unit in context.units
        for diagnostic in unit.diagnostics
    )


def prepare_cmake_compilation_context(
    root: Path,
    config: dict[str, Any],
    project: ProjectModel,
) -> CompilationContext:
    """Return an existing DB or generate one in the canonical release shadow.

    Explicit and auto-discovered databases always win.  CMake is only invoked
    for a C/C++ project whose selected backend is the root CMake project.
    """

    existing = load_compilation_context(root, config)
    if existing.database_path is not None:
        return existing
    if not project.compilable_cpp_sources or project.backend != BACKEND_CMAKE:
        return existing

    try:
        session = configure(
            root,
            ConfigureOptions(BuildVariant.RELEASE, analysis_database=True),
        )
    except (OSError, RuntimeError, ValueError):
        return _with_diagnostics(
            existing,
            _diagnostic("cmake-configure-error", "Canonical CMake configure could not start."),
        )
    if not session.configured:
        return _with_diagnostics(
            existing,
            _diagnostic("cmake-configure-failed", "Canonical CMake configure did not complete."),
        )

    generator, unity_build, metadata_diagnostics = _read_cmake_metadata(root)
    context = _load_generated_context(
        root,
        config,
        generator=generator,
        unity_build=unity_build,
        diagnostics=metadata_diagnostics,
    )
    if _needs_generation_build(context):
        if build(session):
            context = _load_generated_context(
                root,
                config,
                generator=generator,
                unity_build=unity_build,
                diagnostics=metadata_diagnostics,
            )
        else:
            context = _with_diagnostics(
                context,
                _diagnostic(
                    "cmake-generation-failed",
                    "CMake could not generate sources required by the compilation database.",
                ),
            )

    if unity_build or _has_unity_units(context):
        context = replace(context, unity_build=True)
        context = _with_diagnostics(
            context,
            _diagnostic(
                "cmake-unity-build",
                "Unity compilation prevents exact source-level compile-command coverage.",
            ),
        )
    return context


__all__ = ["MAX_CMAKE_CACHE_BYTES", "prepare_cmake_compilation_context"]
