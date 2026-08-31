"""Safe, bounded ingestion of Clang compilation databases.

The loader never invokes a shell or compiler. It preserves the original argv
for later adapters while publishing normalized, immutable metadata into the
shared analysis context.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
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
MAX_COMPILE_ARGUMENTS = 32_768
MAX_COMPILE_ARGUMENT_CHARS = 1024 * 1024
MAX_COMPILE_COMMAND_CHARS = 4 * 1024 * 1024
MAX_RESPONSE_FILE_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_FILE_DEPTH = 4
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


@dataclass(frozen=True)
class _ReadError(Exception):
    code: str
    message: str


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
    if os.name != "nt" and ("\\" in value or bool(PureWindowsPath(value).drive)):
        raise _RowError(
            "foreign-path-syntax",
            "A compilation path uses foreign platform syntax.",
        )
    candidate = Path(value)
    lexical = candidate if candidate.is_absolute() else base / candidate
    try:
        resolved = lexical.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as err:
        raise _RowError("invalid-path", "A compilation path could not be resolved.") from err
    try:
        relative = _relative_text(resolved, root)
    except ValueError:
        return resolved.as_posix(), "external", resolved
    return relative, "project", resolved


def _select_database(root: Path, config: dict[str, Any]) -> tuple[str | None, bool]:
    project = config.get("project", {})
    explicit = project.get("compile_database") if isinstance(project, dict) else None
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit:
            return None, True
        if os.name != "nt" and ("\\" in explicit or bool(PureWindowsPath(explicit).drive)):
            return None, True
        try:
            resolved = (root / explicit).resolve(strict=False)
            relative = resolved.relative_to(root).as_posix()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None, True
        if not relative or relative == ".":
            return None, True
        return relative, True
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


def _read_bounded_regular(path: Path, limit: int) -> bytes:
    """Read one stable regular file through a no-follow descriptor."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as err:
        raise _ReadError("unreadable", "The file could not be opened safely.") from err
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _ReadError("not-file", "The selected path is not a regular file.")
        if before.st_size > limit:
            raise _ReadError("too-large", "The file exceeds the bounded input size.")
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise _ReadError("too-large", "The file exceeds the bounded input size.")
        after = os.fstat(descriptor)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity or total != after.st_size:
            raise _ReadError("changed", "The file changed while it was being read.")
        return b"".join(chunks)
    except OSError as err:
        raise _ReadError("unreadable", "The file could not be read safely.") from err
    finally:
        os.close(descriptor)


def _windows_quote(
    command: str,
    index: int,
    slashes: int,
    quoted: bool,
    value: list[str],
) -> tuple[int, bool]:
    value.extend("\\" for _ in range(slashes // 2))
    if slashes % 2:
        value.append('"')
    elif quoted and index + 1 < len(command) and command[index + 1] == '"':
        value.append('"')
        index += 1
    else:
        quoted = not quoted
    return index + 1, quoted


def _windows_argument(command: str, index: int) -> tuple[str, int]:
    value: list[str] = []
    quoted = False
    while index < len(command) and (quoted or command[index] not in " \t"):
        slashes = 0
        while index < len(command) and command[index] == "\\":
            slashes += 1
            index += 1
        if index < len(command) and command[index] == '"':
            index, quoted = _windows_quote(command, index, slashes, quoted, value)
            continue
        value.extend("\\" for _ in range(slashes))
        if index < len(command) and (quoted or command[index] not in " \t"):
            value.append(command[index])
            index += 1
    if quoted:
        raise ValueError("unclosed quote")
    return "".join(value), index


def _split_windows_command(command: str) -> tuple[str, ...]:
    """Parse the Microsoft C runtime argv convention without executing it."""

    argv: list[str] = []
    index = 0
    while index < len(command):
        while index < len(command) and command[index] in " \t":
            index += 1
        if index < len(command):
            value, index = _windows_argument(command, index)
            argv.append(value)
    return tuple(argv)


def _parse_argv(row: dict[str, Any]) -> tuple[str, ...]:
    if "arguments" in row:
        arguments = row["arguments"]
        if (
            not isinstance(arguments, list)
            or not arguments
            or len(arguments) > MAX_COMPILE_ARGUMENTS
            or any(not isinstance(value, str) or not value or "\0" in value for value in arguments)
            or sum(len(value) for value in arguments if isinstance(value, str))
            > MAX_COMPILE_ARGUMENT_CHARS
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
    if len(command) > MAX_COMPILE_COMMAND_CHARS:
        raise _RowError(
            "invalid-command",
            "The compilation command exceeds the bounded input size.",
        )
    try:
        parsed = _split_windows_command(command) if os.name == "nt" else tuple(shlex.split(command))
    except ValueError as err:
        raise _RowError("invalid-command", "The compilation command could not be parsed.") from err
    if not parsed or any(not value for value in parsed):
        raise _RowError("invalid-command", "The compilation command produced an invalid argv.")
    return parsed


def _response_diagnostic(code: str, message: str) -> CompilationDiagnostic:
    return _diagnostic(code, message, level="error")


def _tokenize_response_file(encoded: bytes) -> tuple[str, ...]:
    try:
        text = encoded.decode("utf-8")
        values = _split_windows_command(text) if os.name == "nt" else tuple(shlex.split(text))
    except (UnicodeError, ValueError) as err:
        raise _RowError(
            "response-file-malformed",
            "A compiler response file could not be decoded safely.",
        ) from err
    if (
        len(values) > MAX_COMPILE_ARGUMENTS
        or any(not value or "\0" in value for value in values)
        or sum(len(value) for value in values) > MAX_COMPILE_ARGUMENT_CHARS
    ):
        raise _RowError(
            "response-file-malformed",
            "A compiler response file contains invalid or excessive arguments.",
        )
    return values


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
    """Expand bounded, contained response files without invoking a compiler."""

    if byte_budget is None:
        byte_budget = [0]
    if character_budget is None:
        character_budget = [0]
    expanded: list[str] = []
    diagnostics: list[CompilationDiagnostic] = []
    for token in argv:
        if not token.startswith("@"):
            expanded.append(token)
            character_budget[0] += len(token)
            if character_budget[0] > MAX_COMPILE_ARGUMENT_CHARS:
                raise _RowError(
                    "invalid-arguments",
                    "Expanded compilation arguments exceed the bounded character count.",
                )
            continue
        if token == "@":
            diagnostics.append(
                _response_diagnostic(
                    "response-file-invalid",
                    "A compiler response-file token has no path.",
                )
            )
            continue
        if depth >= MAX_RESPONSE_FILE_DEPTH:
            diagnostics.append(
                _response_diagnostic(
                    "response-file-depth",
                    "Compiler response-file nesting exceeds the bounded depth.",
                )
            )
            continue
        try:
            _path, scope, resolved = _scoped_path(root, directory, token[1:])
        except _RowError as err:
            diagnostics.append(_response_diagnostic(err.code, err.message))
            continue
        if scope != "project":
            diagnostics.append(
                _response_diagnostic(
                    "response-file-outside-project",
                    "A compiler response file resolves outside the project.",
                )
            )
            continue
        if resolved in active:
            diagnostics.append(
                _response_diagnostic(
                    "response-file-cycle",
                    "Compiler response files contain a recursive cycle.",
                )
            )
            continue
        try:
            encoded = _read_bounded_regular(resolved, MAX_RESPONSE_FILE_BYTES)
            byte_budget[0] += len(encoded)
            if byte_budget[0] > MAX_RESPONSE_FILE_BYTES:
                raise _ReadError(
                    "too-large",
                    "The aggregate compiler response-file input is too large.",
                )
            nested = _tokenize_response_file(encoded)
        except FileNotFoundError:
            diagnostics.append(
                _response_diagnostic(
                    "response-file-missing",
                    "A compiler response file does not exist.",
                )
            )
            continue
        except _ReadError as err:
            diagnostics.append(
                _response_diagnostic(
                    f"response-file-{err.code}",
                    "A compiler response file could not be read safely.",
                )
            )
            continue
        except _RowError as err:
            diagnostics.append(_response_diagnostic(err.code, err.message))
            continue
        nested_values, nested_diagnostics = _expand_response_files(
            nested,
            root=root,
            directory=resolved.parent,
            depth=depth + 1,
            active=(*active, resolved),
            byte_budget=byte_budget,
            character_budget=character_budget,
        )
        expanded.extend(nested_values)
        diagnostics.extend(nested_diagnostics)
        if len(expanded) > MAX_COMPILE_ARGUMENTS:
            raise _RowError(
                "invalid-arguments",
                "Expanded compilation arguments exceed the bounded count.",
            )
    return tuple(expanded), diagnostics


def _compiler_name(value: str) -> str:
    """Return a non-sensitive compiler basename for structured reporting."""

    posix_name = PurePosixPath(value).name
    windows_name = PureWindowsPath(value).name
    return windows_name if "\\" in value else posix_name


def _uses_msvc(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    name = _compiler_name(argv[0]).casefold()
    return name in {"cl", "cl.exe", "clang-cl", "clang-cl.exe"}


def _msvc_option_value(
    argv: tuple[str, ...],
    index: int,
    option: str,
) -> tuple[str | None, int] | None:
    token = argv[index]
    if token == option:
        if index + 1 >= len(argv) or argv[index + 1].startswith(("/", "-")):
            return None, index + 1
        return argv[index + 1], index + 2
    if token.startswith(option) and len(token) > len(option):
        return token[len(option) :], index + 1
    return None


def _option_value(
    argv: tuple[str, ...],
    index: int,
    option: str,
    *,
    joined: bool,
) -> tuple[str | None, int]:
    token = argv[index]
    if token == option:
        if index + 1 >= len(argv) or _looks_like_option(argv[index + 1]):
            return None, index + 1
        return argv[index + 1], index + 2
    if joined and token.startswith(option) and len(token) > len(option):
        if option in {"-std", "--sysroot"} and not token.startswith(option + "="):
            return "", index + 1
        if option == "-o" and token.startswith("-output"):
            return "", index + 1
        if option in {"-isystem", "-iquote", "-isysroot"} and token[len(option)] not in "=./\\":
            return "", index + 1
        value = token[len(option) :]
        if value.startswith("="):
            value = value[1:]
        return value, index + 1
    return "", index + 1


def _looks_like_option(value: str) -> bool:
    return value == "--" or (value.startswith("-") and value != "-")


def _extract_standard(argv: tuple[str, ...]) -> tuple[str, list[CompilationDiagnostic]]:
    standard = ""
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        if argv[index] == "--":
            break
        if _uses_msvc(argv):
            token = argv[index]
            if token.casefold().startswith("/std:"):
                value = token.split(":", 1)[1]
                if value:
                    standard = value
                else:
                    diagnostics.append(
                        _diagnostic("missing-flag-value", "The /std flag has no value.")
                    )
                index += 1
                continue
        value, next_index = _option_value(argv, index, "-std", joined=True)
        if value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -std flag has no value."))
        elif value:
            standard = value
        index = next_index
    return standard, diagnostics


def _language_value(
    value: str | None,
    current: str,
    diagnostics: list[CompilationDiagnostic],
) -> str:
    if value is None:
        diagnostics.append(_diagnostic("missing-flag-value", "The -x flag has no value."))
        return current
    if not value:
        return current
    normalized = _LANGUAGE_ALIASES.get(value.casefold())
    if normalized is not None:
        return normalized
    diagnostics.append(_diagnostic("unknown-language", "The -x language value is not recognized."))
    return current


def _extract_language(
    argv: tuple[str, ...], source: str
) -> tuple[str, list[CompilationDiagnostic]]:
    language = ""
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        if argv[index] == "--":
            break
        if _uses_msvc(argv):
            lowered = argv[index].casefold()
            if lowered.startswith("/tc"):
                language = "c"
                index += 1
                continue
            if lowered.startswith("/tp"):
                language = "c++"
                index += 1
                continue
        value, next_index = _option_value(argv, index, "-x", joined=True)
        language = _language_value(value, language, diagnostics)
        index = next_index
    return language or _SOURCE_LANGUAGES.get(Path(source).suffix.casefold(), ""), diagnostics


def _parse_define(value: str) -> tuple[CompilationDefine | None, CompilationDiagnostic | None]:
    name, separator, definition_value = value.partition("=")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
        return None, _diagnostic("invalid-define", "A compiler definition has an invalid name.")
    return (
        CompilationDefine(name=name, value=definition_value if separator else None),
        None,
    )


def _append_define(
    value: str,
    definitions: list[CompilationDefine],
    diagnostics: list[CompilationDiagnostic],
) -> None:
    definition, diagnostic = _parse_define(value)
    if definition is not None:
        definitions.append(definition)
    if diagnostic is not None:
        diagnostics.append(diagnostic)


def _extract_defines(
    argv: tuple[str, ...],
) -> tuple[tuple[CompilationDefine, ...], list[CompilationDiagnostic]]:
    definitions: list[CompilationDefine] = []
    diagnostics: list[CompilationDiagnostic] = []
    index = 1
    while index < len(argv):
        if argv[index] == "--":
            break
        if _uses_msvc(argv):
            matched = _msvc_option_value(argv, index, "/D")
            if matched is not None:
                value, index = matched
                if value is None:
                    diagnostics.append(
                        _diagnostic("missing-flag-value", "The /D flag has no value.")
                    )
                else:
                    _append_define(value, definitions, diagnostics)
                continue
        value, next_index = _option_value(argv, index, "-D", joined=True)
        if value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -D flag has no value."))
        elif value:
            _append_define(value, definitions, diagnostics)
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
        if argv[index] == "--":
            break
        if _uses_msvc(argv):
            matched_msvc = False
            for option, kind in (("/external:I", "system"), ("/I", "include")):
                matched_value = _msvc_option_value(argv, index, option)
                if matched_value is None:
                    continue
                value, index = matched_value
                matched_msvc = True
                if value is None:
                    diagnostics.append(
                        _diagnostic("missing-flag-value", f"The {option} flag has no value.")
                    )
                    break
                path, scope, resolved = _scoped_path(root, directory, value)
                exists = _is_dir(resolved)
                paths.append(
                    CompilationSearchPath(path=path, kind=kind, scope=scope, exists=exists)
                )
                if not exists:
                    diagnostics.append(
                        _diagnostic(
                            "missing-include-dir",
                            "A configured compiler include directory does not exist.",
                        )
                    )
                break
            if matched_msvc:
                continue
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
        if argv[index] == "--":
            break
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
        if argv[index] == "--":
            break
        if _uses_msvc(argv):
            matched = _msvc_option_value(argv, index, "/Fo")
            if matched is not None:
                value, index = matched
                if value is None:
                    diagnostics.append(
                        _diagnostic("missing-flag-value", "The /Fo flag has no value.")
                    )
                elif value:
                    output = value
                continue
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
    normalized_declared = _normalize_output_value(declared, root, directory, diagnostics)
    normalized_argv = _normalize_output_value(argv_value, root, directory, diagnostics)
    if normalized_declared and normalized_argv and normalized_declared != normalized_argv:
        diagnostics.append(
            _diagnostic(
                "output-mismatch",
                "The output field and compiler -o argument identify different paths.",
            )
        )
    return normalized_declared or normalized_argv, diagnostics


def _normalize_output_value(
    value: str | None,
    root: Path,
    directory: Path,
    diagnostics: list[CompilationDiagnostic],
) -> str:
    if not value:
        return ""
    normalized, scope, resolved = _scoped_path(root, directory, value)
    if scope == "external":
        diagnostics.append(
            _diagnostic("output-outside-project", "The compilation output is outside the project.")
        )
        return ""
    if normalized == "." or _is_dir(resolved):
        diagnostics.append(
            _diagnostic("invalid-output", "The compilation output does not identify a file path.")
        )
        return ""
    return normalized


def _source_operand_diagnostics(
    argv: tuple[str, ...],
    *,
    root: Path,
    directory: Path,
    source: str,
) -> list[CompilationDiagnostic]:
    diagnostics: list[CompilationDiagnostic] = []
    for index, token in enumerate(argv[:-1]):
        if token not in {"-c", "/c"}:
            continue
        candidate = argv[index + 1]
        if _looks_like_option(candidate):
            continue
        try:
            normalized, scope, _resolved = _scoped_path(root, directory, candidate)
        except _RowError:
            normalized, scope = "", "external"
        if scope != "project" or normalized != source:
            diagnostics.append(
                _diagnostic(
                    "source-mismatch",
                    "The compiler source operand does not match the compilation file field.",
                )
            )
        break
    return diagnostics


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
