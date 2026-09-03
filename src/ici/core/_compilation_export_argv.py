"""Privacy-preserving compiler invocation metadata for standalone exports."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ici.core.context import CompilationUnit, canonical_digest
from ici.core.redaction import _redact_compilation_argv, _redact_compilation_path
from ici.core.redaction_values import REDACTED, redact_text

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SAFE_SCALAR = re.compile(r"[A-Za-z0-9+_.:-]{1,128}\Z")
_SAFE_OPTION_NAME = re.compile(
    r"(?:-{1,2}[A-Za-z0-9][A-Za-z0-9+_.-]*=?|/[A-Za-z][A-Za-z0-9+_.-]*:?)\Z"
)
_WRAPPERS = frozenset({"ccache", "distcc", "icecc", "sccache"})
_FAMILIES = {
    "c++": "gcc",
    "c89": "gcc",
    "c99": "gcc",
    "cc": "gcc",
    "clang": "clang",
    "clang++": "clang",
    "clang-cl": "clang-cl",
    "cl": "msvc",
    "gcc": "gcc",
    "g++": "gcc",
}
_VALUE_OPTIONS = frozenset(
    {
        "--sysroot",
        "--target",
        "-D",
        "-I",
        "-U",
        "-isystem",
        "-isysroot",
        "-iquote",
        "-std",
        "-target",
        "-x",
        "/D",
        "/I",
        "/U",
        "/external:I",
        "/imsvc",
    }
)
_JOINED_OPTIONS = (
    "/external:I",
    "-isystem",
    "-isysroot",
    "-iquote",
    "/imsvc",
    "--sysroot=",
    "--target=",
    "-target=",
    "/clang:--target=",
    "/clang:-target=",
    "/std:",
    "-std=",
    "-D",
    "-I",
    "-U",
    "/D",
    "/I",
    "/U",
    "-x",
)
_OPERATIONAL_VALUE_OPTIONS = frozenset({"-MF", "-MQ", "-MT", "-o", "/Fo"})
_OPERATIONAL_JOINED = ("-MF", "-MQ", "-MT", "-o", "/Fo")
_OPERATIONAL_FLAGS = frozenset(
    {"-c", "-M", "-MD", "-MM", "-MMD", "-MP", "-MG", "/c", "/showIncludes"}
)


def _name(value: str) -> str:
    if "\\" in value:
        return PureWindowsPath(value).name
    return PurePosixPath(value).name


def _stem(value: str) -> str:
    stem = _name(value).casefold()
    if stem.endswith(".exe"):
        stem = stem[:-4]
    return re.sub(r"-[0-9]+(?:\.[0-9]+)*\Z", "", stem)


def _compiler_index(argv: tuple[str, ...]) -> tuple[int, list[str]]:
    index = 0
    wrappers: list[str] = []
    if argv and _stem(argv[0]) == "env":
        wrappers.append("env")
        index = 1
        while index < len(argv):
            name, separator, _value = argv[index].partition("=")
            if not separator or _ENV_NAME.fullmatch(name) is None:
                break
            index += 1
    while index < len(argv) and _stem(argv[index]) in _WRAPPERS:
        wrappers.append(_stem(argv[index]))
        index += 1
    return min(index, len(argv) - 1), wrappers


def _safe_compiler_path(value: str, unit: CompilationUnit, root: Path) -> str:
    path = Path(value)
    windows = PureWindowsPath(value)
    if windows.is_absolute() and not path.is_absolute():
        return "[external]"
    if path.is_absolute():
        return _redact_compilation_path(value, root)
    if "/" not in value and "\\" not in value:
        return redact_text(value)
    if "\\" in value and os.name != "nt":
        return "[external]"
    try:
        base = (root / unit.directory).resolve(strict=False)
        absolute = (base / path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return REDACTED
    return _redact_compilation_path(absolute.as_posix(), root)


def compiler_record(unit: CompilationUnit, root: Path) -> dict[str, Any]:
    """Describe the effective compiler without exposing a host path."""

    index, wrappers = _compiler_index(unit.argv)
    raw = unit.argv[index]
    stem = _stem(raw)
    return {
        "family": _FAMILIES.get(stem, "unknown"),
        "name": redact_text(_name(raw)),
        "path": _safe_compiler_path(raw, unit, root),
        "wrappers": wrappers,
    }


def _option_name(token: str) -> str:
    candidate = token
    for separator in ("=", ":"):
        if separator in token:
            prefix = token.split(separator, 1)[0]
            if prefix:
                candidate = prefix + separator
                break
    else:
        for prefix in _JOINED_OPTIONS + _OPERATIONAL_JOINED:
            if token.startswith(prefix) and len(token) > len(prefix):
                candidate = prefix
                break
    return candidate if _SAFE_OPTION_NAME.fullmatch(candidate) is not None else REDACTED


def _is_joined(token: str, prefixes: tuple[str, ...]) -> bool:
    return any(token.startswith(prefix) and len(token) > len(prefix) for prefix in prefixes)


def _target_triple(argv: tuple[str, ...]) -> str | None:
    for index, token in enumerate(argv):
        for prefix in ("--target=", "-target=", "/clang:--target=", "/clang:-target="):
            if token.startswith(prefix):
                value = token[len(prefix) :]
                return value if _SAFE_SCALAR.fullmatch(value) else REDACTED
        if token in {"--target", "-target"} and index + 1 < len(argv):
            value = argv[index + 1]
            return value if _SAFE_SCALAR.fullmatch(value) else REDACTED
    return None


def undefined_names(argv: tuple[str, ...], *, msvc: bool) -> list[str]:
    """Return ordered, syntactically safe preprocessor undefinitions."""

    result: list[str] = []
    option = "/U" if msvc else "-U"
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == option:
            value = argv[index + 1] if index + 1 < len(argv) else ""
            index += 2
        elif token.startswith(option) and len(token) > len(option):
            value = token[len(option) :]
            index += 1
        else:
            index += 1
            continue
        if _ENV_NAME.fullmatch(value):
            result.append(value)
    return result


def _unmodeled_options(
    argv: tuple[str, ...], start: int, root: Path, *, msvc: bool
) -> list[dict[str, Any]]:
    safe_argv = _redact_compilation_argv(argv, root)
    result: list[dict[str, Any]] = []
    index = start
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if token in _VALUE_OPTIONS or token in _OPERATIONAL_VALUE_OPTIONS:
            index += 2
            continue
        if _is_joined(token, _JOINED_OPTIONS) or _is_joined(token, _OPERATIONAL_JOINED):
            index += 1
            continue
        if token in _OPERATIONAL_FLAGS or token.startswith("-Wl,"):
            index += 1
            continue
        is_msvc_option = msvc and token.startswith("/")
        if token.startswith("-") or is_msvc_option:
            result.append(
                {
                    "name": redact_text(_option_name(token)),
                    "order": len(result),
                    "token_digest": canonical_digest(safe_argv[index]),
                }
            )
        index += 1
    return result


def invocation_record(unit: CompilationUnit, root: Path) -> dict[str, Any]:
    """Return a relocation-safe digest plus any semantics not modeled structurally."""

    compiler_index, _wrappers = _compiler_index(unit.argv)
    safe_argv = _redact_compilation_argv(unit.argv, root)
    family = compiler_record(unit, root)["family"]
    unmodeled = _unmodeled_options(
        unit.argv,
        compiler_index + 1,
        root,
        msvc=family in {"msvc", "clang-cl"},
    )
    return {
        "command_style": "windows" if family in {"msvc", "clang-cl"} else "posix",
        "digest": canonical_digest(list(safe_argv)),
        "source": "normalized",
        "target_triple": _target_triple(unit.argv),
        "unmodeled_options": unmodeled,
    }
