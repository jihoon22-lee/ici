"""Read ``tests/fixtures/manifest.toml`` and answer "can this fixture run here".

WP03 (#201) steps 1 and 4. The register lives in TOML; this module turns its
``requires`` entries into probes and caches the answers for a session.

The reason a guard reads from here instead of naming tools inline is
``cpp/cmake_project``. Its CMakeLists calls ``find_package(Qt6 REQUIRED)``, and
the tests that used it guarded on cmake, ctest and gcov. cmake being installed
says nothing about Qt6, so on a machine without Qt6 the guard saw nothing
missing, the test ran, and cmake failed during configure. The test reported a
failure where the honest answer was "not run here".

``qmake_project`` is the contrast worth keeping in view: qmake ships with Qt, so
finding the program really does imply the libraries. One toolchain can be probed
by name and the other cannot, which is exactly why the requirement is data
rather than a convention.

Nothing here requires a shell initialisation file (#201 step 2): probes call
executables directly, and ``_cmake_package_available`` runs cmake in a temporary
directory with no profile involved.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

import pytest
import tomli

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "manifest.toml"
SCHEMA_VERSION = "ici.fixture-manifest/v1"

# The same switch the build-adapter tests already used: CI sets it so that a
# missing toolchain is a failure there, while a developer machine skips.
STRICT_ENV = "ICI_REQUIRE_BUILD_ADAPTERS"

# How long cmake gets to answer whether a package is findable. The probe
# configures an empty project, so this is generous.
_PROBE_TIMEOUT = 60.0


class ManifestError(RuntimeError):
    """The register itself is wrong, which is never a skip."""


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

    @property
    def needs_native_tools(self) -> bool:
        return self.kind == "real-tool"


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
    )


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Fixture]:
    """Parse the register once per session, keyed by fixture id."""

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
    """Look one up, failing loudly rather than skipping on a typo."""

    try:
        return load_manifest()[fixture_id]
    except KeyError:
        known = ", ".join(sorted(load_manifest()))
        raise ManifestError(f"no fixture {fixture_id!r} in the register; known: {known}") from None


@cache
def _executable_available(name: str) -> bool:
    return shutil.which(name) is not None


@cache
def _cmake_package_available(package: str, components: tuple[str, ...]) -> bool:
    """Ask cmake whether ``find_package`` would succeed.

    There is no file named Qt6 to look for, and no environment variable that
    reliably says it is installed, so the only honest probe is to configure a
    project that asks for it. The project is two lines in a temporary directory
    and nothing is built.
    """

    if not _executable_available("cmake"):
        return False
    parts = f" COMPONENTS {' '.join(components)}" if components else ""
    project = (
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(ici_probe LANGUAGES CXX)\n"
        f"find_package({package} REQUIRED{parts})\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory)
        (source / "CMakeLists.txt").write_text(project, encoding="utf-8")
        try:
            completed = subprocess.run(
                ["cmake", "-S", str(source), "-B", str(source / "build")],
                capture_output=True,
                timeout=_PROBE_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
    return completed.returncode == 0


def _describe(requirement: dict[str, Any]) -> str:
    if "executable" in requirement:
        return str(requirement["executable"])
    if "any_executable" in requirement:
        return " or ".join(str(item) for item in requirement["any_executable"])
    if "cmake_package" in requirement:
        components = requirement.get("components") or ()
        suffix = f" ({', '.join(components)})" if components else ""
        return f"cmake package {requirement['cmake_package']}{suffix}"
    return repr(requirement)


def _satisfied(requirement: dict[str, Any], fixture_id: str) -> bool:
    if "executable" in requirement:
        return _executable_available(str(requirement["executable"]))
    if "any_executable" in requirement:
        return any(_executable_available(str(item)) for item in requirement["any_executable"])
    if "cmake_package" in requirement:
        return _cmake_package_available(
            str(requirement["cmake_package"]),
            tuple(str(item) for item in requirement.get("components") or ()),
        )
    raise ManifestError(f"fixture {fixture_id!r}: unknown requirement {requirement!r}")


def missing_requirements(fixture_id: str) -> list[str]:
    """Name what this machine lacks, in the register's order."""

    entry = fixture(fixture_id)
    return [
        _describe(requirement)
        for requirement in entry.requires
        if not _satisfied(requirement, fixture_id)
    ]


def require(fixture_id: str) -> Fixture:
    """Skip, or fail under the strict switch, when the fixture cannot run here.

    Returns the entry so a caller can reach ``path`` without a second lookup.
    """

    entry = fixture(fixture_id)
    missing = missing_requirements(fixture_id)
    if not missing:
        return entry
    message = f"fixture {fixture_id} needs: {', '.join(missing)}"
    if os.environ.get(STRICT_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)
