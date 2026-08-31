"""Compiler metadata extraction for normalized compilation units."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ici.core._compile_db_paths import _is_dir, _RowError, _scoped_path
from ici.core.context import (
    CompilationDefine,
    CompilationDiagnostic,
    CompilationSearchPath,
)

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


def _diagnostic(
    code: str,
    message: str,
    *,
    level: str = "warning",
) -> CompilationDiagnostic:
    return CompilationDiagnostic(code=code, message=message, level=level)


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
                msvc_value = token.split(":", 1)[1]
                if msvc_value:
                    standard = msvc_value
                else:
                    diagnostics.append(
                        _diagnostic("missing-flag-value", "The /std flag has no value.")
                    )
                index += 1
                continue
        standard_value, next_index = _option_value(argv, index, "-std", joined=True)
        if standard_value is None:
            diagnostics.append(_diagnostic("missing-flag-value", "The -std flag has no value."))
        elif standard_value:
            standard = standard_value
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
