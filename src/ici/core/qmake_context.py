"""Canonical qmake compilation-database capture.

qmake does not emit ``compile_commands.json``.  This preflight configures one
owned Release shadow with a compiler wrapper that records the argv and cwd it
actually receives, then executes the original compiler directly.  No Makefile
recipe or captured command is reparsed or executed by ici.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import tempfile
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from ici.core._build_paths import prepare_owned_shadow, shadow_dir
from ici.core._compile_db_paths import _read_bounded_regular, _ReadError
from ici.core._qmake_wrapper import WRAPPER_SOURCE
from ici.core.backend import BACKEND_QMAKE
from ici.core.cmake import ConfigureOptions, build, configure
from ici.core.compile_db import (
    MAX_COMPILE_ARGUMENT_CHARS,
    MAX_COMPILE_ARGUMENTS,
    MAX_COMPILE_DATABASE_BYTES,
    load_compilation_context,
)
from ici.core.context import (
    BuildVariant,
    CompilationContext,
    CompilationDiagnostic,
    ProjectModel,
)

MAX_QMAKE_CAPTURE_BYTES = MAX_COMPILE_DATABASE_BYTES
MAX_QMAKE_CAPTURE_RECORDS = 200_000
MAX_QMAKE_MAKEFILE_BYTES = 4 * 1024 * 1024
MAX_QMAKE_MAKEFILES = 4096
_QMAKE_DATABASE = "build/ici-qmake-build/compile_commands.json"
_CAPTURE_ENV = "ICI_QMAKE_CAPTURE_PATH"
_SOURCE_SUFFIXES = frozenset({".c", ".cc", ".cpp", ".cxx", ".m", ".mm"})
_COMPILER_NAME_RE = re.compile(r"(?:g\+\+|gcc|clang\+\+|clang|c\+\+|cc)(?:-[0-9]+(?:\.[0-9]+)*)?\Z")
_OPTIONS_WITH_VALUE = frozenset(
    {
        "-D",
        "-I",
        "-MF",
        "-MQ",
        "-MT",
        "-include",
        "-imacros",
        "-iquote",
        "-isystem",
        "-isysroot",
        "-o",
        "-x",
        "--sysroot",
    }
)


def _diagnostic(code: str, message: str, *, level: str = "error") -> CompilationDiagnostic:
    return CompilationDiagnostic(code=code, message=message, level=level)


def _with_diagnostics(
    context: CompilationContext,
    *diagnostics: CompilationDiagnostic,
) -> CompilationContext:
    return replace(context, diagnostics=(*context.diagnostics, *diagnostics))


def _generated_config(config: dict[str, Any]) -> dict[str, Any]:
    generated = dict(config)
    project_value = config.get("project", {})
    project = dict(project_value) if isinstance(project_value, dict) else {}
    project["compile_database"] = _QMAKE_DATABASE
    generated["project"] = project
    return generated


def _create_capture_files(directory: Path) -> tuple[Path, Path]:
    interpreter = Path(sys.executable).resolve(strict=True)
    if any(character.isspace() for character in str(directory)) or any(
        character.isspace() for character in str(interpreter)
    ):
        raise ValueError("capture helper path contains whitespace")
    wrapper = directory / "compiler-wrapper"
    journal = directory / "capture.jsonl"
    wrapper.write_text(f"#!{interpreter}\n{WRAPPER_SOURCE}", encoding="utf-8")
    wrapper.chmod(0o700)
    descriptor = os.open(journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    return wrapper, journal


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-standard JSON constant")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _source_operand(root: Path, shadow: Path, row: dict[str, Any]) -> str | None:
    directory_value = row.get("directory")
    arguments = row.get("arguments")
    if not isinstance(directory_value, str) or not directory_value or "\0" in directory_value:
        return None
    if not isinstance(arguments, list) or not arguments or len(arguments) > MAX_COMPILE_ARGUMENTS:
        return None
    if not all(
        isinstance(argument, str) and argument and "\0" not in argument for argument in arguments
    ):
        return None
    if sum(len(argument) for argument in arguments) > MAX_COMPILE_ARGUMENT_CHARS:
        return None
    try:
        directory = Path(directory_value).resolve(strict=True)
        directory.relative_to(root)
        directory.relative_to(shadow)
    except (OSError, RuntimeError, ValueError):
        return None

    candidates: dict[Path, str] = {}
    skip_next = False
    for argument in arguments[1:]:
        if skip_next:
            skip_next = False
            continue
        if argument in _OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if argument.startswith(("-", "@")):
            continue
        if Path(argument).suffix.casefold() not in _SOURCE_SUFFIXES:
            continue
        try:
            candidate = (directory / argument).resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            continue
        if not stat.S_ISREG(candidate.stat().st_mode):
            continue
        # qmake may compile project sources or moc sources generated inside its
        # owned shadow. Both are part of the exact build invocation.
        candidates[candidate] = argument
    if len(candidates) != 1:
        return None
    return next(iter(candidates.values()))


def _capture_rows(root: Path, shadow: Path, journal: Path) -> list[dict[str, Any]]:
    encoded = _read_bounded_regular(journal, MAX_QMAKE_CAPTURE_BYTES)
    rows: list[dict[str, Any]] = []
    lines = encoded.splitlines()
    if not lines or len(lines) > MAX_QMAKE_CAPTURE_RECORDS:
        raise ValueError("capture journal has an invalid record count")
    for line in lines:
        if not line or len(line) > 1024 * 1024:
            raise ValueError("capture journal contains an invalid record")
        row = json.loads(
            line.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
        if not isinstance(row, dict):
            raise ValueError("capture journal record is not an object")
        source = _source_operand(root, shadow, row)
        if source is None:
            raise ValueError("compile source operand is ambiguous or outside the project")
        rows.append(
            {
                "arguments": row["arguments"],
                "directory": row["directory"],
                "file": source,
            }
        )
    rows.sort(key=lambda row: (row["directory"], row["file"], row["arguments"]))
    return rows


def _remove_analysis_shadow(root: Path) -> None:
    target = shadow_dir(root, BACKEND_QMAKE, "-build")
    build_root = root / "build"
    if target.is_symlink():
        raise OSError("qmake analysis shadow must not be a symbolic link")
    if target.exists():
        resolved_build = build_root.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(resolved_build)
        if not resolved_target.is_dir():
            raise OSError("qmake analysis shadow is not a directory")
        shutil.rmtree(resolved_target)


def _reset_analysis_shadow(root: Path) -> Path:
    target = shadow_dir(root, BACKEND_QMAKE, "-build")
    _remove_analysis_shadow(root)
    prepared, error = prepare_owned_shadow(root, target)
    if prepared is None:
        raise OSError(error)
    return prepared


def _makefile_paths(shadow: Path) -> list[Path]:
    paths: list[Path] = []
    visited = 0
    for directory, directories, filenames in os.walk(shadow, followlinks=False):
        directories[:] = sorted(
            name for name in directories if not (Path(directory) / name).is_symlink()
        )
        visited += len(directories) + len(filenames)
        if visited > MAX_QMAKE_MAKEFILES * 8:
            raise ValueError("qmake metadata tree exceeds its entry limit")
        for name in sorted(filenames):
            if not name.startswith("Makefile"):
                continue
            lexical = Path(directory) / name
            if lexical.is_symlink():
                raise ValueError("qmake metadata must not be a symbolic link")
            candidate = lexical.resolve(strict=True)
            candidate.relative_to(shadow)
            paths.append(candidate)
            if len(paths) > MAX_QMAKE_MAKEFILES:
                raise ValueError("qmake metadata exceeds its file limit")
    return paths


def _resolve_compiler(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        raise ValueError("qmake compiler is not a single argv token")
    if _COMPILER_NAME_RE.fullmatch(Path(value).name) is None:
        raise ValueError("qmake compiler is not a recognized C/C++ driver")
    resolved_value = value if Path(value).is_absolute() else shutil.which(value)
    if resolved_value is None:
        raise ValueError("qmake compiler is unavailable")
    resolved = Path(resolved_value).resolve(strict=True)
    if any(character.isspace() for character in str(resolved)):
        raise ValueError("qmake compiler path contains whitespace")
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode) or not os.access(resolved, os.X_OK):
        raise ValueError("qmake compiler is not an executable regular file")
    return str(resolved)


def _qmake_compilers(shadow: Path) -> tuple[str, str]:
    values: dict[str, set[str]] = {"CC": set(), "CXX": set()}
    total_bytes = 0
    for path in _makefile_paths(shadow):
        encoded = _read_bounded_regular(path, MAX_QMAKE_MAKEFILE_BYTES)
        total_bytes += len(encoded)
        if total_bytes > MAX_QMAKE_CAPTURE_BYTES:
            raise ValueError("qmake metadata exceeds its aggregate size limit")
        try:
            text = encoded.decode("utf-8")
        except UnicodeError as error:
            raise ValueError("qmake metadata is not UTF-8") from error
        if "\0" in text:
            raise ValueError("qmake metadata contains a null byte")
        for line in text.splitlines():
            name, separator, value = line.partition("=")
            key = name.strip()
            if separator and key in values:
                values[key].add(value.strip())
    if any(len(values[key]) != 1 for key in values):
        raise ValueError("qmake metadata does not declare one consistent compiler pair")
    return _resolve_compiler(next(iter(values["CXX"]))), _resolve_compiler(next(iter(values["CC"])))


def _write_database(root: Path, shadow: Path, rows: list[dict[str, Any]]) -> None:
    safe_shadow, error = prepare_owned_shadow(root, shadow)
    if safe_shadow is None or safe_shadow != shadow:
        raise OSError(error or "qmake shadow identity changed")
    encoded = json.dumps(
        rows,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > MAX_COMPILE_DATABASE_BYTES:
        raise OSError("captured compilation database exceeds its size limit")
    descriptor, temporary_value = tempfile.mkstemp(
        prefix=".ici-compile-commands-",
        dir=safe_shadow,
    )
    temporary = Path(temporary_value)
    try:
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("compilation database write did not progress")
                view = view[written:]
        finally:
            os.close(descriptor)
        os.replace(temporary, safe_shadow / "compile_commands.json")
    finally:
        with suppress(OSError):
            temporary.unlink()


def _load_generated_context(root: Path, config: dict[str, Any]) -> CompilationContext:
    return replace(
        load_compilation_context(root, _generated_config(config)),
        origin="qmake",
        generator="qmake",
        unity_build=None,
    )


def _with_capture_coverage(
    context: CompilationContext,
    project: ProjectModel,
) -> CompilationContext:
    captured = {unit.source for unit in context.units}
    missing = sorted(set(project.compilable_cpp_sources) - captured)
    if not missing:
        return context
    return _with_diagnostics(
        context,
        _diagnostic(
            "qmake-capture-incomplete",
            f"The qmake capture missed {len(missing)} production translation unit(s).",
        ),
    )


def prepare_qmake_compilation_context(
    root: Path,
    config: dict[str, Any],
    project: ProjectModel,
) -> CompilationContext:
    """Return an existing database or capture qmake's actual compiler argv."""

    existing = load_compilation_context(root, config)
    if existing.database_path is not None:
        return existing
    if not project.compilable_cpp_sources or project.backend != BACKEND_QMAKE:
        return existing
    if os.name != "posix":
        return _with_diagnostics(
            existing,
            _diagnostic(
                "qmake-capture-unsupported",
                "Exact qmake compiler capture is currently available only on POSIX hosts.",
                level="warning",
            ),
        )

    canonical_root = root.resolve(strict=False)
    preserve_shadow = False
    try:
        with tempfile.TemporaryDirectory(prefix="ici-qmake-capture-") as capture_directory:
            wrapper, journal = _create_capture_files(Path(capture_directory))
            _reset_analysis_shadow(canonical_root)
            probe = configure(
                canonical_root,
                ConfigureOptions(
                    BuildVariant.RELEASE,
                    analysis_database=True,
                ),
            )
            if not probe.configured:
                return _with_diagnostics(
                    existing,
                    _diagnostic(
                        "qmake-configure-failed",
                        "Canonical qmake configure did not complete.",
                    ),
                )
            cxx, cc = _qmake_compilers(probe.shadow)
            session = configure(
                canonical_root,
                ConfigureOptions(
                    BuildVariant.RELEASE,
                    analysis_database=True,
                    qmake_capture_wrapper=str(wrapper),
                    qmake_capture_cxx=cxx,
                    qmake_capture_cc=cc,
                ),
            )
            if not session.configured:
                return _with_diagnostics(
                    existing,
                    _diagnostic(
                        "qmake-capture-configure-failed",
                        "Canonical qmake capture configure did not complete.",
                    ),
                )
            if not build(session, env={_CAPTURE_ENV: str(journal)}):
                return _with_diagnostics(
                    existing,
                    _diagnostic(
                        "qmake-capture-build-failed",
                        "The canonical qmake capture build did not complete.",
                    ),
                )
            rows = _capture_rows(canonical_root, session.shadow, journal)
            _write_database(canonical_root, session.shadow, rows)
        context = _with_capture_coverage(
            _load_generated_context(canonical_root, config),
            project,
        )
        preserve_shadow = True
        return context
    except (OSError, RecursionError, RuntimeError, TypeError, UnicodeError, ValueError, _ReadError):
        return _with_diagnostics(
            existing,
            _diagnostic(
                "qmake-capture-unavailable",
                "Exact qmake compiler capture could not be produced safely; analysis remains lower confidence.",
                level="warning",
            ),
        )
    finally:
        if not preserve_shadow:
            with suppress(OSError, RuntimeError, ValueError):
                _remove_analysis_shadow(canonical_root)


__all__ = [
    "MAX_QMAKE_CAPTURE_BYTES",
    "MAX_QMAKE_CAPTURE_RECORDS",
    "MAX_QMAKE_MAKEFILES",
    "MAX_QMAKE_MAKEFILE_BYTES",
    "prepare_qmake_compilation_context",
]
