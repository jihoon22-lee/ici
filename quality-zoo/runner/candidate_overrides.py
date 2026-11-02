"""Reviewed per-candidate-format answers for single engine ``extra`` fields.

A known answer is exact. The only exception is a field a reviewed, recorded intended
difference makes format-specific: the native Rust candidate keeps its owned shadow build trees
under ``.ici/rust-v1/build/legacy-<digest>/<variant>`` where the frozen pyz used
``build/ici-cmake-build`` (docs/design/rust-replacement/closure/cpp.json rows
``capability:build-artifact-abi`` and ``capability:legacy-command-config-result-projection``).
A native expectation is the verbatim v0.13.0 answer (``runner.bind_candidate``), so the runner,
not the answer file, substitutes the reviewed native predicate for exactly that field.

Each override names the candidate format, scenario, engine, extra key, the exact frozen value
it replaces and an anchored pattern the native value must fully match. It fails closed: the
override is refused when the expected value is not the recorded frozen value (the answer
changed and needs a new review), a non-native candidate is held to the frozen value, and a
native value that stops matching its pattern is an ``engine-extra`` failure. There is no
wildcard: every other field of every scenario stays exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runner.common import ContractError

PYZ = "pyz"
NATIVE = "native"
CANDIDATE_FORMATS = {PYZ, NATIVE}
ELF_MAGIC = b"\x7fELF"


@dataclass(frozen=True)
class ExtraOverride:
    candidate_format: str
    scenario_id: str
    engine: str
    key: str
    frozen_value: Any
    pattern: re.Pattern[str]
    reason: str


REVIEWED_EXTRA_OVERRIDES: tuple[ExtraOverride, ...] = (
    ExtraOverride(
        candidate_format=NATIVE,
        scenario_id="cpp.static-build-context",
        engine="compile_db",
        key="database_path",
        frozen_value="build/ici-cmake-build/compile_commands.json",
        # legacy-<first 16 hex of sha256(component id)>/<variant> (crates/ici-cli/src/
        # export_context.rs) under the native `.ici/rust-v1/build` namespace; the native
        # candidate wrote .ici/rust-v1/build/legacy-244210e48437b655/release/compile_commands.json
        # (/tmp/ici-rust-replacement-20261028/w-runtime-sbc-database-path.log).
        pattern=re.compile(
            r"\.ici/rust-v1/build/legacy-[0-9a-f]{16}/release/compile_commands\.json"
        ),
        reason=(
            "Recorded native shadow-tree namespace: owned legacy CMake builds live in "
            ".ici/rust-v1/build/legacy-<digest>/<variant> (frozen build/ici-cmake-build); "
            "docs/design/rust-replacement/closure/cpp.json capability:build-artifact-abi and "
            "capability:legacy-command-config-result-projection; root ruling 2026-11-02"
        ),
    ),
)


def candidate_format(executable: Path) -> str:
    """``native`` for an ELF executable (the Rust core), ``pyz`` for any other candidate."""

    try:
        with executable.open("rb") as stream:
            magic = stream.read(len(ELF_MAGIC))
    except OSError as error:
        raise ContractError("unsafe-ici-bin", f"cannot read ICI_BIN: {error}") from error
    return NATIVE if magic == ELF_MAGIC else PYZ


def extra_override(
    candidate: str, scenario_id: str, engine: str, key: str
) -> ExtraOverride | None:
    if candidate not in CANDIDATE_FORMATS:
        raise ContractError("candidate-format", f"unknown candidate format {candidate!r}")
    matches = [
        item
        for item in REVIEWED_EXTRA_OVERRIDES
        if (item.candidate_format, item.scenario_id, item.engine, item.key)
        == (candidate, scenario_id, engine, key)
    ]
    return matches[0] if matches else None


def extra_matches(
    override: ExtraOverride | None, expected: Any, observed: Any
) -> tuple[bool, str]:
    """Whether ``observed`` satisfies the answer, and the detail suffix of a mismatch."""

    if override is None:
        return observed == expected, "differs"
    if expected != override.frozen_value:
        raise ContractError(
            "override-stale",
            f"{override.scenario_id} {override.engine} extra.{override.key} answer "
            f"{expected!r} is not the reviewed frozen value {override.frozen_value!r}",
        )
    if isinstance(observed, str) and override.pattern.fullmatch(observed):
        return True, ""
    return False, f"does not match the reviewed {override.candidate_format} pattern"
