"""Shared validation primitives for untrusted quality-zoo inputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCENARIO_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ContractError(ValueError):
    """An input violated a checked quality-zoo contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def load_json_object(
    path: Path, *, label: str, max_bytes: int = 16 * 1024 * 1024
) -> dict[str, Any]:
    """Load one UTF-8 JSON object without accepting duplicate keys."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractError("duplicate-json-key", f"{label} repeats {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ContractError("invalid-json-number", f"{label} contains {value}")

    try:
        if not path.is_file() or path.is_symlink():
            raise ContractError("unsafe-file", f"{label} is not a regular file")
        if path.stat().st_size > max_bytes:
            raise ContractError(
                "json-too-large", f"{label} exceeds the {max_bytes}-byte limit"
            )
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ContractError("read-failed", f"cannot read {label}: {error}") from error
    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except ContractError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ContractError("invalid-json", f"cannot parse {label}: {error}") from error
    if not isinstance(payload, dict):
        raise ContractError("invalid-json-root", f"{label} must be an object")
    return payload


def sha256_file(path: Path, *, max_bytes: int | None = None) -> tuple[str, int]:
    """Hash a regular file through a bounded streaming read."""

    try:
        if not path.is_file() or path.is_symlink():
            raise ContractError(
                "unsafe-file", f"not a regular non-symlink file: {path}"
            )
        size = path.stat().st_size
    except OSError as error:
        raise ContractError("stat-failed", f"cannot inspect {path}: {error}") from error
    if max_bytes is not None and size > max_bytes:
        raise ContractError(
            "file-too-large", f"{path} is {size} bytes; limit is {max_bytes}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise ContractError("read-failed", f"cannot hash {path}: {error}") from error
    return digest.hexdigest(), size


def require_string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise ContractError("invalid-field", f"{label} must be a string")
    return value


def require_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("invalid-field", f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ContractError("invalid-field", f"{label} must be >= {minimum}")
    return value


def require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ContractError("invalid-field", f"{label} must be a boolean")
    return value


def contained_path(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    """Resolve a POSIX-like relative path beneath root."""

    if not relative or "\\" in relative or WINDOWS_DRIVE_RE.match(relative):
        raise ContractError("unsafe-path", f"invalid relative path: {relative!r}")
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ContractError("unsafe-path", f"path escapes its root: {relative!r}")
    root = root.resolve(strict=True)
    try:
        resolved = (root / raw).resolve(strict=must_exist)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise ContractError(
            "unsafe-path", f"path is not contained: {relative!r}"
        ) from error
    return resolved
