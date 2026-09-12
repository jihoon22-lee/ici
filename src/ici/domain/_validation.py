"""Pure validation primitives for the ici-next domain models.

Every helper here is a total function over in-memory values. Nothing in this
module touches the filesystem, spawns a process, reads the environment, or
imports anything outside the standard library — that restriction is what makes
the domain safe to construct in a test, and `tests/test_domain_boundaries.py`
enforces it rather than trusting this docstring.

The helpers return the normalized value instead of returning ``None``, so a
model's ``__post_init__`` reads as a list of assignments and stays shallow. That
keeps cyclomatic complexity in the models low, which matters because ici gates
its own complexity.

Errors are always ``ValueError``. Callers upstream turn that into an exit code
2 diagnostic (SPEC-04 section 3), and a single exception type means they never
have to enumerate boundary ``TypeError`` cases.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import TypeVar

__all__ = [
    "require_digest",
    "require_env_overlay",
    "require_identifier",
    "require_non_negative",
    "require_positive",
    "require_relative_path",
    "require_text",
    "require_tuple",
    "require_unique_identifiers",
]

T = TypeVar("T")

# Lowercase, dot/dash/underscore separated. Deliberately narrow: these ids end
# up in result JSON, in cache keys and in file names, so anything that would
# need quoting or case-folding later is rejected at construction.
_IDENTIFIER_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?")

# "sha256:" plus exactly 64 lowercase hex characters. SPEC-04 section 1 uses
# abbreviated digests in its illustrative envelope; those are not valid values
# and must not pass validation just because they look like one.
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def require_text(value: object, description: str) -> str:
    """Return a non-empty string with no leading or trailing whitespace."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{description} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{description} must not be padded with whitespace: {value!r}")
    return value


def require_identifier(value: object, description: str) -> str:
    """Return a stable identifier usable in JSON, cache keys and file names."""

    text = require_text(value, description)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError(
            f"{description} must be lowercase alphanumeric with . _ - separators: {text!r}"
        )
    return text


def require_relative_path(value: object, description: str, *, allow_dot: bool = False) -> str:
    """Return a canonical, contained, project-relative POSIX path.

    Rejects absolute paths, ``..`` traversal, backslashes and any spelling that
    is not already canonical. Refusing a non-canonical spelling rather than
    normalizing it keeps one path one string, which is what lets a digest over
    the model mean something.
    """

    text = require_text(value, description)
    if "\\" in text:
        raise ValueError(f"{description} must use POSIX separators: {text!r}")
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{description} must be relative and contained: {text!r}")
    if path.as_posix() != text:
        raise ValueError(f"{description} must be canonical: {text!r}")
    if not allow_dot and text == ".":
        raise ValueError(f"{description} must name a path below the root")
    return text


def require_digest(value: object, description: str) -> str:
    """Return a full ``sha256:<64 hex>`` digest."""

    text = require_text(value, description)
    if _DIGEST_RE.fullmatch(text) is None:
        raise ValueError(f"{description} must be a full sha256:<64 hex> digest: {text!r}")
    return text


def require_tuple(values: object, item_type: type[T], description: str) -> tuple[T, ...]:
    """Return a tuple of ``item_type``, rejecting strings and mappings.

    A bare string is iterable, so accepting one would silently turn ``"gui"``
    into four single-character components. That mistake is quiet and expensive,
    hence the explicit rejection.
    """

    if isinstance(values, (str, bytes, bytearray, Mapping)) or not isinstance(values, Iterable):
        raise ValueError(f"{description} must be an iterable collection, not a scalar")
    try:
        normalized = tuple(values)
    except TypeError as err:
        raise ValueError(f"{description} must be an iterable collection") from err
    for item in normalized:
        if not isinstance(item, item_type):
            raise ValueError(f"{description} must contain only {item_type.__name__} values")
    return normalized


def require_unique_identifiers(values: Iterable[str], description: str) -> tuple[str, ...]:
    """Return the identifiers in order, rejecting duplicates."""

    ordered = tuple(values)
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in ordered:
        if item in seen:
            duplicates.append(item)
        seen.add(item)
    if duplicates:
        raise ValueError(f"{description} contains duplicate ids: {sorted(set(duplicates))}")
    return ordered


def require_non_negative(value: object, description: str) -> int:
    """Return a non-negative integer, rejecting bools."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{description} must be an integer")
    if value < 0:
        raise ValueError(f"{description} must not be negative: {value}")
    return value


def require_positive(value: object, description: str) -> float:
    """Return a strictly positive, finite number, rejecting bools."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{description} must be finite: {value!r}")
    if number <= 0:
        raise ValueError(f"{description} must be greater than zero: {value!r}")
    return number


def require_env_overlay(value: object, description: str) -> tuple[tuple[str, str], ...]:
    """Return environment overrides as sorted, unique ``(name, value)`` pairs.

    Stored as pairs rather than a dict for two reasons: a frozen dataclass
    holding a dict is only shallowly immutable, and SPEC-04 requires
    deterministic serialization, which a sorted sequence gives for free.

    The overlay carries only what a task adds on top of the inherited
    environment. WP01 measured why that distinction matters: clearing
    ``SSL_CERT_FILE`` in the name of isolation silently removes the CA file for
    a statically linked OpenSSL, so a task states its additions and never
    implies a replacement of the whole environment.
    """

    if isinstance(value, Mapping):
        items = tuple(value.items())
    else:
        items = require_tuple(value, tuple, description)
    pairs: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError(f"{description} must contain (name, value) pairs")
        name = require_text(item[0], f"{description} name")
        if not isinstance(item[1], str):
            raise ValueError(f"{description} value for {name!r} must be a string")
        pairs.append((name, item[1]))
    names = [name for name, _ in pairs]
    if len(set(names)) != len(names):
        raise ValueError(f"{description} must not set a variable twice")
    return tuple(sorted(pairs))
