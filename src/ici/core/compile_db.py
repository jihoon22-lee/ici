"""Safe, bounded ingestion of Clang compilation databases.

The loader never invokes a shell or compiler. It preserves the original argv
for later adapters while publishing normalized, immutable metadata into the
shared analysis context.
"""

from __future__ import annotations

import hashlib
import json
import os  # noqa: F401 - preserved for callers that monkeypatch the facade module
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core._compile_db_commands import (
    _expand_response_files as _expand_response_files_impl,
)
from ici.core._compile_db_commands import (
    _parse_argv as _parse_argv_impl,
)
from ici.core._compile_db_commands import (
    _split_windows_command,
)
from ici.core._compile_db_metadata import (
    _compiler_name,
    _extract_defines,
    _extract_includes,
    _extract_language,
    _extract_standard,
    _extract_sysroot,
    _normalize_output,
    _source_operand_diagnostics,
)
from ici.core._compile_db_paths import (
    _is_dir,
    _is_file,
    _read_bounded_regular,
    _ReadError,
    _RowError,
    _scoped_path,
    _select_database,
)
from ici.core.context import (
    CompilationContext,
    CompilationDefine,
    CompilationDiagnostic,
    CompilationUnit,
    canonical_digest,
)

MAX_COMPILE_DATABASE_BYTES = 32 * 1024 * 1024
MAX_COMPILE_DATABASE_ENTRIES = 200_000
MAX_COMPILE_ARGUMENTS = 32_768
MAX_COMPILE_ARGUMENT_CHARS = 1024 * 1024
MAX_COMPILE_COMMAND_CHARS = 4 * 1024 * 1024
MAX_RESPONSE_FILE_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_FILE_DEPTH = 4
__all__ = ["CompilationDefine", "_split_windows_command", "load_compilation_context"]


def _diagnostic(
    code: str,
    message: str,
    *,
    entry_index: int | None = None,
    source: str = "",
    level: str = "warning",
) -> CompilationDiagnostic:
    return CompilationDiagnostic(
        code=code,
        message=message,
        level=level,
        entry_index=entry_index,
        source=source,
    )


def _parse_argv(row: dict[str, Any]) -> tuple[str, ...]:
    """Parse one row while honoring facade-level resource limits."""

    return _parse_argv_impl(
        row,
        max_arguments=MAX_COMPILE_ARGUMENTS,
        max_argument_chars=MAX_COMPILE_ARGUMENT_CHARS,
        max_command_chars=MAX_COMPILE_COMMAND_CHARS,
    )


def _expand_response_files(
    argv: tuple[str, ...],
    *,
    root: Path,
    directory: Path,
    depth: int = 0,
    active: tuple[Path, ...] = (),
    byte_budget: list[int] | None = None,
    character_budget: list[int] | None = None,
) -> tuple[tuple[str, ...], list[CompilationDiagnostic]]:
    """Expand response files using the current facade-level resource limits."""

    return _expand_response_files_impl(
        argv,
        root=root,
        directory=directory,
        depth=depth,
        active=active,
        byte_budget=byte_budget,
        character_budget=character_budget,
        max_arguments=MAX_COMPILE_ARGUMENTS,
        max_argument_chars=MAX_COMPILE_ARGUMENT_CHARS,
        max_response_file_bytes=MAX_RESPONSE_FILE_BYTES,
        max_response_file_depth=MAX_RESPONSE_FILE_DEPTH,
    )


def _parse_row(
    row: object,
    *,
    index: int,
    root: Path,
    database_parent: Path,
) -> CompilationUnit:
    if not isinstance(row, dict):
        raise _RowError("invalid-entry", "A compilation database entry is not an object.")
    if "file" not in row:
        raise _RowError("missing-file", "A compilation database entry has no file field.")
    if "directory" not in row:
        raise _RowError(
            "missing-directory",
            "A compilation database entry has no directory field.",
        )
    file_value = row["file"]
    directory_value = row["directory"]
    if not isinstance(file_value, str) or not file_value:
        raise _RowError("invalid-file", "The compilation file field is not a string.")
    if not isinstance(directory_value, str) or not directory_value:
        raise _RowError(
            "invalid-directory",
            "The compilation directory field is not a string.",
        )

    directory, directory_scope, resolved_directory = _scoped_path(
        root, database_parent, directory_value
    )
    if directory_scope == "external":
        raise _RowError(
            "directory-outside-project",
            "The compilation working directory is outside the project.",
        )
    source, source_scope, resolved_source = _scoped_path(root, resolved_directory, file_value)
    if source_scope == "external":
        raise _RowError(
            "source-outside-project",
            "The compilation source resolves outside the project.",
        )
    if source == "." or _is_dir(resolved_source):
        raise _RowError(
            "invalid-source-path",
            "The compilation source does not identify a file path.",
        )

    argv = _parse_argv(row)
    argv, response_diagnostics = _expand_response_files(
        argv,
        root=root,
        directory=resolved_directory,
    )
    unit_diagnostics: list[CompilationDiagnostic] = []
    if not _is_dir(resolved_directory):
        unit_diagnostics.append(
            _diagnostic(
                "missing-directory",
                "The compilation working directory does not exist.",
                entry_index=index,
                source=source,
            )
        )
    if not _is_file(resolved_source):
        unit_diagnostics.append(
            _diagnostic(
                "stale-source",
                "The compilation source no longer exists.",
                entry_index=index,
                source=source,
            )
        )
    language, language_diagnostics = _extract_language(argv, source)
    standard, standard_diagnostics = _extract_standard(argv)
    definitions, define_diagnostics = _extract_defines(argv)
    include_paths, include_diagnostics = _extract_includes(argv, root, resolved_directory)
    sysroot, sysroot_scope, sysroot_diagnostics = _extract_sysroot(argv, root, resolved_directory)
    output, output_diagnostics = _normalize_output(row, argv, root, resolved_directory)
    for diagnostic in (
        *response_diagnostics,
        *_source_operand_diagnostics(
            argv,
            root=root,
            directory=resolved_directory,
            source=source,
        ),
        *language_diagnostics,
        *standard_diagnostics,
        *define_diagnostics,
        *include_diagnostics,
        *sysroot_diagnostics,
        *output_diagnostics,
    ):
        unit_diagnostics.append(
            CompilationDiagnostic(
                code=diagnostic.code,
                message=diagnostic.message,
                level=diagnostic.level,
                entry_index=index,
                source=source,
            )
        )
    configuration = canonical_digest({"directory": directory, "argv": list(argv), "output": output})
    return CompilationUnit(
        source=source,
        directory=directory,
        argv=argv,
        output=output,
        compiler=_compiler_name(argv[0]),
        language=language,
        standard=standard,
        defines=definitions,
        include_paths=include_paths,
        sysroot=sysroot,
        sysroot_scope=sysroot_scope,
        configuration=configuration,
        diagnostics=tuple(unit_diagnostics),
    )


def _database_failure(path: str, code: str, message: str) -> CompilationContext:
    return CompilationContext(
        database_path=path,
        diagnostics=(_diagnostic(code, message, level="error"),),
    )


def _reject_json_constant(value: str) -> None:
    del value
    raise ValueError("non-standard JSON constant")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def load_compilation_context(root: Path, config: dict[str, Any]) -> CompilationContext:
    """Load the selected compilation database into an immutable context.

    An absent auto-discovered database is optional. An explicitly configured
    missing database, malformed database, or unsafe path is retained as
    diagnostic evidence instead of raising from verification preflight.
    """

    project_root = root.resolve(strict=False)
    selected, explicit = _select_database(project_root, config)
    if selected is None:
        if explicit:
            return _database_failure(
                "compile_commands.json",
                "invalid-database-setting",
                "The configured compilation database path is invalid.",
            )
        return CompilationContext()
    lexical = project_root / PurePosixPath(selected)
    try:
        database = lexical.resolve(strict=False)
        database.relative_to(project_root)
    except (OSError, RuntimeError, ValueError):
        return _database_failure(
            selected,
            "database-outside-project",
            "The compilation database resolves outside the project.",
        )
    try:
        encoded = _read_bounded_regular(database, MAX_COMPILE_DATABASE_BYTES)
    except FileNotFoundError:
        if explicit:
            return _database_failure(
                selected,
                "database-missing",
                "The configured compilation database does not exist.",
            )
        return CompilationContext()
    except _ReadError as err:
        messages = {
            "not-file": "The compilation database path is not a regular file.",
            "too-large": "The compilation database exceeds the bounded input size.",
            "changed": "The compilation database changed while it was being read.",
            "unreadable": "The compilation database could not be read safely.",
        }
        return _database_failure(selected, f"database-{err.code}", messages[err.code])
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, ValueError, RecursionError):
        return _database_failure(
            selected,
            "database-malformed",
            "The compilation database could not be decoded as JSON.",
        )
    if not isinstance(payload, list):
        return _database_failure(
            selected,
            "database-not-array",
            "The compilation database root must be an array.",
        )
    if len(payload) > MAX_COMPILE_DATABASE_ENTRIES:
        return _database_failure(
            selected,
            "database-too-many-entries",
            "The compilation database exceeds the bounded entry count.",
        )

    units: list[CompilationUnit] = []
    diagnostics: list[CompilationDiagnostic] = []
    for index, row in enumerate(payload):
        try:
            unit = _parse_row(
                row,
                index=index,
                root=project_root,
                database_parent=database.parent,
            )
        except _RowError as err:
            diagnostics.append(
                _diagnostic(
                    err.code,
                    err.message,
                    entry_index=index,
                    source=err.source,
                    level="error",
                )
            )
        except (TypeError, ValueError):
            diagnostics.append(
                _diagnostic(
                    "invalid-entry-value",
                    "A compilation database entry contains an invalid value.",
                    entry_index=index,
                    level="error",
                )
            )
        else:
            units.append(unit)
    units.sort(key=lambda item: (item.source, item.directory, item.configuration))
    return CompilationContext(
        units=tuple(units),
        database_path=selected,
        database_digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
        diagnostics=tuple(diagnostics),
    )
