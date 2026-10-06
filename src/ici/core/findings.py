"""Canonical finding paths, region validation, and fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from ici.core.models import SourceLocation

FINGERPRINT_VERSION = "ici-fingerprint/v1"


def _slash_path(value: str | Path) -> str:
    return str(value).replace("\\", "/")


def _comparison_value(value: str) -> str:
    # Windows paths are case-insensitive at least for the drive component. A
    # case-folded comparison also lets reports produced on Windows and WSL use
    # one canonical project-relative identity.
    return value.casefold() if re.match(r"^[A-Za-z]:/", value) else value


def _collapse_relative(value: str) -> str:
    parts: list[str] = []
    for part in PurePosixPath(value).parts:
        if part in ("", ".", "/"):
            continue
        if re.fullmatch(r"[A-Za-z]:", part):
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"path escapes the project root: {value!r}")
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) or "."


def canonical_project_path(file_path: str | Path, project_root: str | Path | None = None) -> str:
    """Return a lexical, slash-separated project-relative path.

    No filesystem resolution is used, so a checkout path, symlink layout, or
    source file that no longer exists cannot change a finding identity.
    Absolute inputs require an explicit root and must stay inside it.
    """

    value = _slash_path(file_path).rstrip("/") or "."
    if re.match(r"^[A-Za-z]:(?!/)", value):
        raise ValueError(f"drive-relative finding path is ambiguous: {value!r}")
    root_value = _slash_path(project_root) if project_root is not None else ""
    root = root_value.rstrip("/") or ("/" if root_value.startswith("/") else "")
    is_absolute = value.startswith("/") or bool(re.match(r"^[A-Za-z]:/", value))

    if is_absolute:
        if not root:
            raise ValueError(f"absolute finding path requires project_root: {value!r}")
        value_cmp = _comparison_value(value)
        root_cmp = _comparison_value(root)
        if value_cmp == root_cmp:
            value = "."
        elif root == "/":
            value = value.lstrip("/")
        elif value_cmp.startswith(root_cmp + "/"):
            value = value[len(root) + 1 :]
        else:
            raise ValueError(f"finding path is outside project_root: {value!r}")

    return _collapse_relative(value)


def validate_source_region(
    *,
    start_line: int,
    end_line: int | None,
    start_column: int | None,
    end_column: int | None,
    context: str = "source location",
) -> None:
    """Enforce the 1-indexed region invariant promised by the v3 schema."""

    values = {
        "start_line": start_line,
        "end_line": end_line,
        "start_column": start_column,
        "end_column": end_column,
    }
    for name, value in values.items():
        if value is None and name != "start_line":
            continue
        if type(value) is not int or value < 1:
            raise ValueError(f"{context} {name} must be a 1-indexed integer: {value!r}")
    if end_line is not None and end_line < start_line:
        raise ValueError(
            f"{context} end_line must not precede start_line: {end_line} < {start_line}"
        )
    if (
        start_column is not None
        and end_column is not None
        and end_line in (None, start_line)
        and end_column < start_column
    ):
        raise ValueError(
            f"{context} end_column must not precede start_column on one line: "
            f"{end_column} < {start_column}"
        )


def finding_fingerprint(
    rule_id: str,
    location: SourceLocation,
    *,
    symbol: str = "",
) -> str:
    """Build a deterministic identity independent of checkout root and separators."""

    region: dict[str, int | None] | None = None
    if not symbol.strip():
        region = {
            "start_line": location.start_line,
            "end_line": location.end_line,
            "start_column": location.start_column,
            "end_column": location.end_column,
        }
    payload = {
        "version": FINGERPRINT_VERSION,
        "rule_id": rule_id,
        "path": location.path,
        "symbol": symbol.strip(),
        "region": region,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
