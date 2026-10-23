"""Native identities for process evidence, independent of execution labels."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ici.execution.process import ProcessSpec

_JOINED_PATH_OPTIONS = (
    "-isystem",
    "-idirafter",
    "-iquote",
    "-include",
    "-imacros",
    "-isysroot",
    "-I",
    "-L",
    "-F",
    "-B",
    "@",
)


def process_fingerprint(provider: str, spec: ProcessSpec, root: Path | None, *details: str) -> str:
    """Keep the measured invocation and diagnostic, never its consumer task ID.

    Capture bounds and freshness timestamps are execution bookkeeping. The
    workspace root is supplied by the provider registry; without it, absolute
    paths stay absolute rather than guessing which directory is the project.
    """

    payload = (
        provider,
        *invocation_identity(spec.argv, spec.cwd, root),
        details,
    )
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def invocation_identity(
    argv: tuple[str, ...], cwd: Path | None, root: Path | None
) -> tuple[str | None, tuple[str, ...]]:
    """Preserve every option while relocating explicit workspace references."""

    return (
        _relative(str(cwd), root) if cwd is not None else None,
        tuple(_argument(arg, root) for arg in argv),
    )


def _argument(value: str, root: Path | None) -> str:
    if value.startswith("-") and "=" in value:
        key, _, argument = value.partition("=")
        if len(argument) > 1 and argument[0] == argument[-1] and argument[0] in "\"'":
            argument = argument[0] + _relative(argument[1:-1], root) + argument[-1]
        else:
            argument = _relative(argument, root)
        return key + "=" + argument
    for option in _JOINED_PATH_OPTIONS:
        if value.startswith(option):
            return option + _relative(value[len(option) :], root)
    return _relative(value, root)


def _relative(value: str, root: Path | None) -> str:
    path = Path(os.path.normpath(value))
    if root is None or not path.is_absolute():
        return value
    try:
        relative = path.relative_to(root)
    except ValueError:
        return value
    return "${workspace}" if relative == Path(".") else "${workspace}/" + relative.as_posix()
