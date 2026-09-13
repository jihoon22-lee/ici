"""``--local-config``: a file that may move paths and nothing else.

SPEC-01 section 3 gives this file one job — point ici at a developer's own
build directory, interpreter or output location — and one prohibition: it may
not change what is checked, which rules, which thresholds or which exclusions.

The prohibition is the reason the file exists at all. Without it, a local
override is a way for one machine's results to disagree with CI's while both
look green, and the disagreement lives in a file CI never reads. So a key
outside the allowlist is refused by name rather than ignored: ignoring it would
leave the author believing it took effect.

Official CI runs with no overlay. That is a caller's choice, not something this
module can enforce, so it is stated in the guide rather than implied here.
"""

from __future__ import annotations

import re
from typing import Any

import tomli

from ici.config.errors import ConfigProblem, NextConfigError, collect
from ici.config.layers import LOCAL_OVERLAY_ALLOWLIST
from ici.config.origin import Origin, Sourced

__all__ = ["read_local"]


def read_local(text: str, *, path: str) -> dict[str, Sourced[str]]:
    """Read an overlay into a flat ``dotted.key -> value`` map.

    Flat rather than structured because every allowed key is a leaf, and a
    structure would invite someone to add a nested section that the allowlist
    then has to learn about separately.
    """

    problems: list[ConfigProblem] = []
    try:
        document = tomli.loads(text)
    except tomli.TOMLDecodeError as error:
        problems.append(ConfigProblem(f"not valid TOML: {error}", Origin(file=path)))
        raise NextConfigError(tuple(problems)) from error

    values: dict[str, Sourced[str]] = {}
    for key, value in _leaves(document):
        origin = Origin(file=path, key=key)
        if not _allowed(key):
            problems.append(
                ConfigProblem(
                    "a local overlay may only adjust paths",
                    origin,
                    hint="allowed: " + ", ".join(sorted(LOCAL_OVERLAY_ALLOWLIST)),
                )
            )
            continue
        if not isinstance(value, str):
            problems.append(
                ConfigProblem(
                    f"{key} must be a path, not {type(value).__name__}",
                    origin,
                )
            )
            continue
        values[key] = Sourced(value=value, origin=origin)

    collect(problems)
    return values


def _leaves(document: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    """Every scalar in the document, with its dotted key."""

    found: list[tuple[str, Any]] = []
    for key, value in document.items():
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            found.extend(_leaves(value, dotted))
            continue
        found.append((dotted, value))
    return found


# A wildcard stands for one key, not any depth: "builds.*.directory" allows
# builds.native.directory and not builds.a.b.directory. fnmatch would let "*"
# swallow the dots and quietly widen the allowlist past what it says.
_WILDCARD = re.compile(r"\\\*")


def _allowed(key: str) -> bool:
    return any(_pattern(entry).fullmatch(key) for entry in LOCAL_OVERLAY_ALLOWLIST)


def _pattern(entry: str) -> re.Pattern[str]:
    return re.compile(_WILDCARD.sub(r"[^.]+", re.escape(entry)))
