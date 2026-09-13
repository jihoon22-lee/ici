"""Primitives shared by the result and event codecs.

Split out so that neither codec file grows past the size ici's own `line`
engine warns at, and so the envelope check has exactly one implementation:
a result and an event must reject a wrong ``schema_id`` the same way, or a
consumer learns to trust one and not the other.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "SchemaError",
    "UnsupportedSchemaError",
    "check_envelope",
    "dumps",
    "read_enum",
    "require_mapping",
]


class SchemaError(ValueError):
    """The payload could not be read as a result or event."""


class UnsupportedSchemaError(SchemaError):
    """A recognisable envelope of a version this build cannot read.

    Separate from ``SchemaError`` because the two need different answers: a
    malformed document is a bug or a truncated file, while an unsupported
    version means the producer is newer and the consumer should say so rather
    than attempt a partial read.
    """


def require_mapping(value: object, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError(f"{description} must be a JSON object, got {type(value).__name__}")
    return value


def check_envelope(payload: dict[str, Any], schema_id: str, version: int) -> None:
    """Check the envelope before reading anything else.

    Reports the value found, not just that it was wrong: "expected
    ici.next.run, found ici-result-v3" tells the caller they handed over a
    legacy report, which is the actual mistake.
    """

    found_id = payload.get("schema_id")
    if found_id != schema_id:
        raise SchemaError(f"expected schema_id {schema_id!r}, found {found_id!r}")
    found_version = payload.get("schema_version")
    if not isinstance(found_version, int) or isinstance(found_version, bool):
        raise SchemaError(f"schema_version must be an integer, found {found_version!r}")
    if found_version != version:
        raise UnsupportedSchemaError(
            f"{schema_id} version {found_version} cannot be read by this build, "
            f"which implements version {version}"
        )


def read_enum(value: object, enum_type: type, description: str) -> Any:
    """Convert to an enum member, naming the allowed values on failure."""

    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as err:
        allowed = sorted(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise SchemaError(f"{description} must be one of {allowed}, found {value!r}") from err


def dumps(payload: dict[str, Any]) -> str:
    """Serialize deterministically.

    Sorted keys and fixed separators mean the same value always produces the
    same bytes. ``allow_nan=False`` matters more than it looks: Python would
    otherwise emit bare ``NaN``, which is not JSON, and a consumer in another
    language would fail on a file ici called valid.
    """

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
