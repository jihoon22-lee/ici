"""Bounded compiler command and response-file parsing helpers."""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Any

from ici.core._compile_db_paths import (
    _read_bounded_regular,
    _ReadError,
    _RowError,
    _scoped_path,
)
from ici.core.context import CompilationDiagnostic


def _diagnostic(
    code: str,
    message: str,
    *,
    level: str = "warning",
) -> CompilationDiagnostic:
    return CompilationDiagnostic(code=code, message=message, level=level)


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


def _validate_arguments(
    arguments: object,
    *,
    max_arguments: int,
    max_argument_chars: int,
) -> tuple[str, ...]:
    if not isinstance(arguments, list) or not arguments or len(arguments) > max_arguments:
        raise _RowError(
            "invalid-arguments",
            "The compilation arguments field must contain non-empty strings.",
        )
    if any(not isinstance(value, str) or not value or "\0" in value for value in arguments):
        raise _RowError(
            "invalid-arguments",
            "The compilation arguments field must contain non-empty strings.",
        )
    if sum(len(value) for value in arguments) > max_argument_chars:
        raise _RowError(
            "invalid-arguments",
            "The compilation arguments field must contain non-empty strings.",
        )
    return tuple(arguments)


def _parse_command(command: object, *, max_command_chars: int) -> tuple[str, ...]:
    if not isinstance(command, str) or not command or "\0" in command:
        raise _RowError(
            "missing-command",
            "The compilation entry has neither valid arguments nor a command.",
        )
    if len(command) > max_command_chars:
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


def _parse_argv(
    row: dict[str, Any],
    *,
    max_arguments: int,
    max_argument_chars: int,
    max_command_chars: int,
) -> tuple[str, ...]:
    if "arguments" in row:
        return _validate_arguments(
            row["arguments"],
            max_arguments=max_arguments,
            max_argument_chars=max_argument_chars,
        )
    return _parse_command(row.get("command"), max_command_chars=max_command_chars)


def _response_diagnostic(code: str, message: str) -> CompilationDiagnostic:
    return _diagnostic(code, message, level="error")


def _tokenize_response_file(
    encoded: bytes,
    *,
    max_arguments: int,
    max_argument_chars: int,
) -> tuple[str, ...]:
    try:
        text = encoded.decode("utf-8")
        values = _split_windows_command(text) if os.name == "nt" else tuple(shlex.split(text))
    except (UnicodeError, ValueError) as err:
        raise _RowError(
            "response-file-malformed",
            "A compiler response file could not be decoded safely.",
        ) from err
    if (
        len(values) > max_arguments
        or any(not value or "\0" in value for value in values)
        or sum(len(value) for value in values) > max_argument_chars
    ):
        raise _RowError(
            "response-file-malformed",
            "A compiler response file contains invalid or excessive arguments.",
        )
    return values


def _append_plain_argument(
    expanded: list[str], token: str, character_budget: list[int], max_argument_chars: int
) -> None:
    expanded.append(token)
    character_budget[0] += len(token)
    if character_budget[0] > max_argument_chars:
        raise _RowError(
            "invalid-arguments",
            "Expanded compilation arguments exceed the bounded character count.",
        )


def _load_response_tokens(
    resolved: Path,
    *,
    containment_root: Path,
    byte_budget: list[int],
    response_cache: dict[Path, tuple[tuple[str, ...], CompilationDiagnostic | None]],
    max_response_file_bytes: int,
    max_response_file_total_bytes: int,
    max_arguments: int,
    max_argument_chars: int,
) -> tuple[tuple[str, ...], CompilationDiagnostic | None]:
    cached = response_cache.get(resolved)
    if cached is not None:
        return cached
    result: tuple[tuple[str, ...], CompilationDiagnostic | None]
    try:
        encoded = _read_bounded_regular(
            resolved,
            max_response_file_bytes,
            containment_root=containment_root,
        )
        byte_budget[0] += len(encoded)
        if byte_budget[0] > max_response_file_total_bytes:
            raise _ReadError(
                "too-large",
                "The aggregate compiler response-file input is too large.",
            )
        result = (
            _tokenize_response_file(
                encoded,
                max_arguments=max_arguments,
                max_argument_chars=max_argument_chars,
            ),
            None,
        )
    except FileNotFoundError:
        result = (
            (),
            _response_diagnostic(
                "response-file-missing", "A compiler response file does not exist."
            ),
        )
    except _ReadError as err:
        result = (
            (),
            _response_diagnostic(
                f"response-file-{err.code}",
                "A compiler response file could not be read safely.",
            ),
        )
    except _RowError as err:
        result = ((), _response_diagnostic(err.code, err.message))
    response_cache[resolved] = result
    return result


def _expand_response_token(
    token: str,
    *,
    root: Path,
    directory: Path,
    depth: int,
    active: tuple[Path, ...],
    byte_budget: list[int],
    character_budget: list[int],
    response_cache: dict[Path, tuple[tuple[str, ...], CompilationDiagnostic | None]],
    max_arguments: int,
    max_argument_chars: int,
    max_response_file_bytes: int,
    max_response_file_total_bytes: int,
    max_response_file_depth: int,
) -> tuple[tuple[str, ...], list[CompilationDiagnostic]]:
    if token == "@":
        return (), [
            _response_diagnostic(
                "response-file-invalid", "A compiler response-file token has no path."
            )
        ]
    if depth >= max_response_file_depth:
        return (), [
            _response_diagnostic(
                "response-file-depth",
                "Compiler response-file nesting exceeds the bounded depth.",
            )
        ]
    try:
        _path, scope, resolved = _scoped_path(root, directory, token[1:])
    except _RowError as err:
        return (), [_response_diagnostic(err.code, err.message)]
    if scope != "project":
        return (), [
            _response_diagnostic(
                "response-file-outside-project",
                "A compiler response file resolves outside the project.",
            )
        ]
    if resolved in active:
        return (), [
            _response_diagnostic(
                "response-file-cycle",
                "Compiler response files contain a recursive cycle.",
            )
        ]
    nested, diagnostic = _load_response_tokens(
        resolved,
        containment_root=root,
        byte_budget=byte_budget,
        response_cache=response_cache,
        max_response_file_bytes=max_response_file_bytes,
        max_response_file_total_bytes=max_response_file_total_bytes,
        max_arguments=max_arguments,
        max_argument_chars=max_argument_chars,
    )
    if diagnostic is not None:
        return (), [diagnostic]
    return _expand_response_files(
        nested,
        root=root,
        directory=resolved.parent,
        depth=depth + 1,
        active=(*active, resolved),
        byte_budget=byte_budget,
        character_budget=character_budget,
        response_cache=response_cache,
        max_arguments=max_arguments,
        max_argument_chars=max_argument_chars,
        max_response_file_bytes=max_response_file_bytes,
        max_response_file_total_bytes=max_response_file_total_bytes,
        max_response_file_depth=max_response_file_depth,
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
    response_cache: dict[Path, tuple[tuple[str, ...], CompilationDiagnostic | None]] | None = None,
    max_arguments: int,
    max_argument_chars: int,
    max_response_file_bytes: int,
    max_response_file_total_bytes: int,
    max_response_file_depth: int,
) -> tuple[tuple[str, ...], list[CompilationDiagnostic]]:
    """Expand bounded, contained response files without invoking a compiler."""

    byte_budget = byte_budget if byte_budget is not None else [0]
    character_budget = character_budget if character_budget is not None else [0]
    response_cache = response_cache if response_cache is not None else {}
    expanded: list[str] = []
    diagnostics: list[CompilationDiagnostic] = []
    for token in argv:
        if token.startswith("@"):
            nested_values, nested_diagnostics = _expand_response_token(
                token,
                root=root,
                directory=directory,
                depth=depth,
                active=active,
                byte_budget=byte_budget,
                character_budget=character_budget,
                response_cache=response_cache,
                max_arguments=max_arguments,
                max_argument_chars=max_argument_chars,
                max_response_file_bytes=max_response_file_bytes,
                max_response_file_total_bytes=max_response_file_total_bytes,
                max_response_file_depth=max_response_file_depth,
            )
            expanded.extend(nested_values)
            diagnostics.extend(nested_diagnostics)
        else:
            _append_plain_argument(expanded, token, character_budget, max_argument_chars)
        if len(expanded) > max_arguments:
            raise _RowError(
                "invalid-arguments",
                "Expanded compilation arguments exceed the bounded count.",
            )
    return tuple(expanded), diagnostics
