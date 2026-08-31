"""Safe, bounded ingestion of Clang compilation databases.

The loader never invokes a shell or compiler. It preserves the original argv
for later adapters while publishing normalized, immutable metadata into the
shared analysis context.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ici.core.context import (
    CompilationContext,
    CompilationDefine,
    CompilationDiagnostic,
    CompilationSearchPath,
    CompilationUnit,
    canonical_digest,
)

MAX_COMPILE_DATABASE_BYTES = 32 * 1024 * 1024
MAX_COMPILE_DATABASE_ENTRIES = 200_000
_SOURCE_LANGUAGES = {
    ".c": "c",
    ".cc": "c++",
    ".cpp": "c++",
    ".cxx": "c++",
    ".m": "objective-c",
    ".mm": "objective-c++",
}
_LANGUAGE_ALIASES = {
    "c": "c",
    "c-header": "c",
    "c++": "c++",
    "c++-header": "c++",
    "objective-c": "objective-c",
    "objective-c++": "objective-c++",
}


@dataclass(frozen=True)
class _RowError(Exception):
    code: str
    message: str
    source: str = ""


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


def _relative_text(path: Path, root: Path, *, allow_dot: bool = True) -> str:
    relative = path.relative_to(root).as_posix()
    if not relative and allow_dot:
        return "."
    return relative


def _scoped_path(root: Path, base: Path, value: str) -> tuple[str, str, Path]:
    candidate = Path(value)
    lexical = candidate if candidate.is_absolute() else base / candidate
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as err:
        raise _RowError("invalid-path", "A compilation path could not be resolved.") from err
    try:
        relative = _relative_text(resolved, root)
    except ValueError:
        return str(resolved), "external", resolved
    return relative, "project", resolved


def _select_database(root: Path, config: dict[str, Any]) -> tuple[str | None, bool]:
    project = config.get("project", {})
    explicit = project.get("compile_database") if isinstance(project, dict) else None
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit or "\\" in explicit:
            return None, True
        path = PurePosixPath(explicit)
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != explicit
            or path == Path(".")
        ):
            return None, True
        return path.as_posix(), True
    for candidate in ("compile_commands.json", "build/compile_commands.json"):
        try:
            if (root / candidate).exists():
                return candidate, False
        except OSError:
            continue
    return None, False


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _split_windows_command(command: str) -> tuple[str, ...]:
    """Parse the Microsoft C runtime argv convention without executing it."""

    argv: list[str] = []
    length = len(command)
    index = 0
    while index < length:
        while index < length and command[index] in " \t":
            index += 1
        if index >= length:
            break
        value: list[str] = []
        quoted = False
        started = False
        while index < length and (quoted or command[index] not in " \t"):
            started = True
            slashes = 0
            while index < length and command[index] == "\\":
                slashes += 1
                index += 1
            if index < length and command[index] == '"':
                value.extend("\\" for _ in range(slashes // 2))
                if slashes % 2:
                    value.append('"')
                elif quoted and index + 1 < length and command[index + 1] == '"':
                    value.append('"')
                    index += 1
                else:
                    quoted = not quoted
                index += 1
                continue
            value.extend("\\" for _ in range(slashes))
            if index < length and (quoted or command[index] not in " \t"):
                value.append(command[index])
                index += 1
        if quoted:
            raise ValueError("unclosed quote")
        if started:
            argv.append("".join(value))
    return tuple(argv)


def _parse_argv(row: dict[str, Any]) -> tuple[str, ...]:
    if "arguments" in row:
        arguments = row["arguments"]
        if (
            not isinstance(arguments, list)
            or not arguments
            or any(not isinstance(value, str) or not value or "\0" in value for value in arguments)
        ):
            raise _RowError(
                "invalid-arguments",
                "The compilation arguments field must contain non-empty strings.",
            )
        return tuple(arguments)
    command = row.get("command")
    if not isinstance(command, str) or not command or "\0" in command:
        raise _RowError(
            "missing-command",
            "The compilation entry has neither valid arguments nor a command.",
        )
    try:
        parsed = _split_windows_command(command) if os.name == "nt" else tuple(shlex.split(command))
    except ValueError as err:
        raise _RowError("invalid-command", "The compilation command could not be parsed.") from err
    if not parsed or any(not value for value in parsed):
        raise _RowError("invalid-command", "The compilation command produced an invalid argv.")
    return parsed


def _option_value(
    argv: tuple[str, ...],
    index: int,
    option: str,
    *,
    joined: bool,
) -> tuple[str | None, int]:
    token = argv[index]
    if token == option:
        if index + 1 >= len(argv):
            return None, index + 1
        return argv[index + 1], index + 2
    if joined and token.startswith(option) and len(token) > len(option):
        value = token[len(option) :]
        if value.startswith("="):
            value = value[1:]
        return value, index + 1
    return "", index + 1


def _extract_standard(argv: tuple[str, ...]) -> tuple[str, list[CompilationDiagnostic]]:
    standard = ""
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        value, next_index = _option_value(argv, index, "-std", joined=True)
        if value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -std flag has no value."))
        elif value:
            standard = value
        index = next_index
    return standard, diagnostics


def _extract_language(
    argv: tuple[str, ...], source: str
) -> tuple[str, list[CompilationDiagnostic]]:
    language = ""
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        value, next_index = _option_value(argv, index, "-x", joined=False)
        if value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -x flag has no value."))
        elif value:
            normalized = _LANGUAGE_ALIASES.get(value.casefold())
            if normalized is None:
                diagnostics.append(
                    _diagnostic("unknown-language", "The -x language value is not recognized.")
                )
            else:
                language = normalized
        index = next_index
    return language or _SOURCE_LANGUAGES.get(Path(source).suffix.casefold(), ""), diagnostics


def _extract_defines(
    argv: tuple[str, ...],
) -> tuple[tuple[CompilationDefine, ...], list[CompilationDiagnostic]]:
    definitions: list[CompilationDefine] = []
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        value, next_index = _option_value(argv, index, "-D", joined=True)
        if value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -D flag has no value."))
        elif value:
            name, separator, definition_value = value.partition("=")
            if not name or any(character.isspace() for character in name):
                diagnostics.append(
                    _diagnostic("invalid-define", "A compiler definition has an invalid name.")
                )
            else:
                definitions.append(
                    CompilationDefine(name=name, value=definition_value if separator else None)
                )
        index = next_index
    return tuple(definitions), diagnostics


def _extract_includes(
    argv: tuple[str, ...], root: Path, directory: Path
) -> tuple[tuple[CompilationSearchPath, ...], list[CompilationDiagnostic]]:
    paths: list[CompilationSearchPath] = []
    diagnostics: list[CompilationDiagnostic] = []
    options = (("-I", "include", True), ("-isystem", "system", True), ("-iquote", "quote", True))
    index = 1
    while index < len(argv):
        matched = False
        for option, kind, joined in options:
            value, next_index = _option_value(argv, index, option, joined=joined)
            if value == "":
                continue
            matched = True
            index = next_index
            if value is None:
                diagnostics.append(
                    _diagnostic("missing-flag-value", f"The {option} flag has no value.")
                )
                break
            path, scope, resolved = _scoped_path(root, directory, value)
            exists = _is_dir(resolved)
            paths.append(CompilationSearchPath(path=path, kind=kind, scope=scope, exists=exists))
            if not exists:
                diagnostics.append(
                    _diagnostic(
                        "missing-include-dir",
                        "A configured compiler include directory does not exist.",
                    )
                )
            break
        if not matched:
            index += 1
    return tuple(paths), diagnostics


def _extract_sysroot(
    argv: tuple[str, ...], root: Path, directory: Path
) -> tuple[str, str, list[CompilationDiagnostic]]:
    sysroot = ""
    scope = ""
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        matched = False
        for option, joined in (("--sysroot", True), ("-isysroot", False)):
            value, next_index = _option_value(argv, index, option, joined=joined)
            if value == "":
                continue
            matched = True
            index = next_index
            if value is None:
                diagnostics.append(
                    _diagnostic("missing-flag-value", f"The {option} flag has no value.")
                )
                break
            sysroot, scope, _resolved = _scoped_path(root, directory, value)
            break
        if not matched:
            index += 1
    return sysroot, scope, diagnostics


def _argv_output(argv: tuple[str, ...]) -> tuple[str, list[CompilationDiagnostic]]:
    output = ""
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        value, next_index = _option_value(argv, index, "-o", joined=True)
        if value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -o flag has no value."))
        elif value:
            output = value
        index = next_index
    return output, diagnostics


def _normalize_output(
    row: dict[str, Any], argv: tuple[str, ...], root: Path, directory: Path
) -> tuple[str, list[CompilationDiagnostic]]:
    argv_value, diagnostics = _argv_output(argv)
    declared = row.get("output")
    if declared is not None and (not isinstance(declared, str) or not declared):
        diagnostics.append(
            _diagnostic("invalid-output", "The compilation output field is not a string.")
        )
        declared = None
    if declared and argv_value and declared != argv_value:
        diagnostics.append(
            _diagnostic(
                "output-mismatch",
                "The output field and compiler -o argument identify different paths.",
            )
        )
    selected = declared or argv_value
    if not selected:
        return "", diagnostics
    normalized, scope, _resolved = _scoped_path(root, directory, selected)
    if scope == "external":
        diagnostics.append(
            _diagnostic("output-outside-project", "The compilation output is outside the project.")
        )
        return "", diagnostics
    return normalized, diagnostics


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

    argv = _parse_argv(row)
    unit_diagnostics: list[CompilationDiagnostic] = []
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
        compiler=argv[0],
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
        details = database.stat()
    except FileNotFoundError:
        if explicit:
            return _database_failure(
                selected,
                "database-missing",
                "The configured compilation database does not exist.",
            )
        return CompilationContext()
    except OSError:
        return _database_failure(
            selected,
            "database-unreadable",
            "The compilation database metadata could not be read.",
        )
    if not _is_file(database):
        return _database_failure(
            selected,
            "database-not-file",
            "The compilation database path is not a regular file.",
        )
    if details.st_size > MAX_COMPILE_DATABASE_BYTES:
        return _database_failure(
            selected,
            "database-too-large",
            "The compilation database exceeds the bounded input size.",
        )
    try:
        encoded = database.read_bytes()
        details_after = database.stat()
        if (
            details.st_size,
            details.st_mtime_ns,
            details.st_ino,
        ) != (
            details_after.st_size,
            details_after.st_mtime_ns,
            details_after.st_ino,
        ):
            return _database_failure(
                selected,
                "database-changed",
                "The compilation database changed while it was being read.",
            )
        payload = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
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
        else:
            units.append(unit)
    units.sort(key=lambda item: (item.source, item.directory, item.configuration))
    return CompilationContext(
        units=tuple(units),
        database_path=selected,
        database_digest="sha256:" + hashlib.sha256(encoded).hexdigest(),
        diagnostics=tuple(diagnostics),
    )
