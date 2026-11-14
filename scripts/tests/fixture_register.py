"""Read the fixture register ``tests/fixtures/manifest.toml`` (WP03 #201).

Every fixture this repository owns is declared there with its kind, the tools it
declares, what it should produce, and where it came from. After the R9 cutover the
remaining rows are the ici-next wire fixtures the Rust tests read and the Quality
Zoo scenarios; the register and its contract tests in this directory keep those
declarations complete and honest.

The pytest-era tool probes (``require``/``missing_requirements`` and the
``ICI_REQUIRE_BUILD_ADAPTERS`` strict switch) guarded Python product tests and
were removed with them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import tomli

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "manifest.toml"
SCHEMA_VERSION = "ici.fixture-manifest/v1"


class ManifestError(RuntimeError):
    """The register itself is wrong."""


@dataclass(frozen=True)
class Fixture:
    """One entry of the register."""

    id: str
    path: Path
    kind: str
    languages: tuple[str, ...]
    requires: tuple[dict[str, Any], ...]
    cost: str
    provenance: str
    license: str
    expectation: str
    role: str = ""
    safety: str = ""
    exercised_by: tuple[str, ...] = field(default_factory=tuple)
    # Names of corpus-hygiene patterns this fixture intentionally trips, e.g. a
    # known-answer scenario whose bait is a credential-shaped assignment. An
    # entry here never silences the other patterns for the fixture.
    hygiene_allow: tuple[str, ...] = field(default_factory=tuple)


def _require_str(value: Any, field_name: str, fixture_id: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"fixture {fixture_id!r}: {field_name} must be a non-empty string")
    return value


def _fixture_from_entry(entry: dict[str, Any]) -> Fixture:
    fixture_id = entry.get("id")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise ManifestError("every fixture entry needs a non-empty id")
    kind = _require_str(entry.get("kind"), "kind", fixture_id)
    if kind not in ("real-tool", "data"):
        raise ManifestError(f"fixture {fixture_id!r}: kind must be 'real-tool' or 'data'")
    requires = entry.get("requires", [])
    if not isinstance(requires, list):
        raise ManifestError(f"fixture {fixture_id!r}: requires must be a list")
    return Fixture(
        id=fixture_id,
        path=REPO_ROOT / _require_str(entry.get("path"), "path", fixture_id),
        kind=kind,
        languages=tuple(entry.get("languages", ())),
        requires=tuple(requires),
        cost=_require_str(entry.get("cost"), "cost", fixture_id),
        provenance=_require_str(entry.get("provenance"), "provenance", fixture_id),
        license=_require_str(entry.get("license"), "license", fixture_id),
        expectation=_require_str(entry.get("expectation"), "expectation", fixture_id),
        role=entry.get("role", ""),
        safety=entry.get("safety", ""),
        exercised_by=tuple(entry.get("exercised_by", ())),
        hygiene_allow=tuple(entry.get("hygiene_allow", ())),
    )


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Fixture]:
    """Parse the register once per process, keyed by fixture id."""

    with MANIFEST_PATH.open("rb") as stream:
        document = tomli.load(stream)
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ManifestError(f"expected schema_version {SCHEMA_VERSION!r}, found {version!r}")
    entries = document.get("fixture")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("the register has no fixture entries")

    fixtures: dict[str, Fixture] = {}
    for entry in entries:
        fixture = _fixture_from_entry(entry)
        if fixture.id in fixtures:
            raise ManifestError(f"duplicate fixture id {fixture.id!r}")
        fixtures[fixture.id] = fixture
    return fixtures


def fixture(fixture_id: str) -> Fixture:
    """Look one up, failing loudly on a typo."""

    try:
        return load_manifest()[fixture_id]
    except KeyError:
        known = ", ".join(sorted(load_manifest()))
        raise ManifestError(f"no fixture {fixture_id!r} in the register; known: {known}") from None
