"""Value types and the cross-target rule for GNU ELF dead-function evidence.

Separated from the linker adapter because it is the one part with no processes,
no filesystem and no tools in it: given what each link discarded, decide what
that means for the program. Keeping it pure also keeps it honest — the rule can
be read and tested without standing up a build.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _LinkCommand:
    """One accepted link, and the objects it puts into the image."""

    target: str
    path: Path
    argv: tuple[str, ...]
    driver: Path
    driver_name: str
    driver_version: str
    objects: tuple[Path, ...]
    output: Path
    digest: str


@dataclass(frozen=True)
class _DiscardedSection:
    """One function section GNU ld discarded while linking one target."""

    target: str
    object_path: Path
    section: str
    command_digest: str
    driver_name: str
    driver_version: str


def discarded_by_every_linking_target(
    removals: list[_DiscardedSection],
    linked: list[_LinkCommand],
) -> tuple[list[_DiscardedSection], int]:
    """Keep only sections every target that linked their object discarded.

    Each relink answers "is this function reachable from *this* target's entry
    point". Collecting those answers as a union answers a different question:
    one executable discarding a helper that another executable calls would be
    reported as removable, and removing it would break the second build.

    A section is retained only when every accepted target that linked its object
    file discarded it. That is still not whole-program reachability — dynamic
    lookup and exported symbols stay excluded, as the module docstring says —
    but it is sound across the set of targets actually linked, which the union
    was not.
    """

    if len(linked) < 2:
        return removals, 0
    linking_targets: dict[Path, set[str]] = {}
    for command in linked:
        for object_path in command.objects:
            linking_targets.setdefault(object_path, set()).add(command.target)
    discarding_targets: dict[tuple[Path, str], set[str]] = {}
    for removal in removals:
        discarding_targets.setdefault((removal.object_path, removal.section), set()).add(
            removal.target
        )

    kept = 0
    retained: list[_DiscardedSection] = []
    seen: set[tuple[Path, str]] = set()
    for removal in removals:
        key = (removal.object_path, removal.section)
        required = linking_targets.get(removal.object_path, {removal.target})
        if not required <= discarding_targets.get(key, set()):
            kept += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        retained.append(removal)
    return retained, kept
