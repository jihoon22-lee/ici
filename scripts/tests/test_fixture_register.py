"""The fixture register stays complete and honest (WP03 #201).

Ported from the pytest suite at the R9 cutover. The register now holds the
ici-next wire fixtures the Rust tests read and the Quality Zoo scenarios. Removed
with what they checked: the ``examples/`` fixture-directory and probe tests,
the clean-control invariant (its controls lived under ``examples/``), the
``ICI_REQUIRE_BUILD_ADAPTERS`` skip/fail switch, and the ``tests/toolcontract.py``
recorder tests.
"""

from __future__ import annotations

import unittest

import tomli
from fixture_register import REPO_ROOT, ManifestError, fixture, load_manifest

NEXT_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "ici-next"
QUALITY_ZOO_SCENARIOS = REPO_ROOT / "quality-zoo" / "scenarios"


class TheRegisterIsCompleteTest(unittest.TestCase):
    """A register that misses a fixture is worse than none: it looks authoritative."""

    def test_every_ici_next_fixture_file_is_registered(self) -> None:
        on_disk = {path.name for path in NEXT_FIXTURE_DIR.glob("*.json")}
        registered = {
            entry.path.name
            for entry in load_manifest().values()
            if entry.path.parent == NEXT_FIXTURE_DIR
        }

        self.assertEqual(on_disk, registered)

    def test_every_registered_path_exists(self) -> None:
        missing = [entry.id for entry in load_manifest().values() if not entry.path.exists()]

        self.assertEqual(missing, [])

    def test_every_entry_names_its_provenance_and_licence(self) -> None:
        """#201: the corpus must be re-runnable from this repository alone, which
        means knowing where each piece came from."""

        for entry in load_manifest().values():
            self.assertTrue(entry.provenance, entry.id)
            self.assertTrue(entry.license, entry.id)

    def test_every_entry_says_what_it_should_produce(self) -> None:
        for entry in load_manifest().values():
            self.assertTrue(entry.expectation, entry.id)

    def test_data_fixtures_declare_no_tool_requirements(self) -> None:
        """The real/mock split is only meaningful if 'data' really means no tools."""

        for entry in load_manifest().values():
            if entry.kind == "data":
                self.assertEqual(entry.requires, (), entry.id)

    def test_real_tool_fixtures_declare_at_least_one(self) -> None:
        """Except quality-zoo rows: a corpus scenario's honest requirement set
        is whatever its own ``[doctor] required_tools`` declares, and a pure
        source-analysis scenario legitimately declares nothing. The mirroring
        tests below hold those rows to that declaration instead."""
        for entry in load_manifest().values():
            if entry.kind == "real-tool" and QUALITY_ZOO_SCENARIOS not in entry.path.parents:
                self.assertTrue(entry.requires, entry.id)

    def test_every_corpus_scenario_directory_is_registered(self) -> None:
        """The corpus register is only authoritative if it misses nothing the
        corpus runner could select."""
        on_disk = {
            f"{language.name}/{scenario.name}"
            for language in QUALITY_ZOO_SCENARIOS.iterdir()
            if language.is_dir()
            for scenario in language.iterdir()
            if scenario.is_dir()
        }
        registered = {
            f"{entry.path.parent.name}/{entry.path.name}"
            for entry in load_manifest().values()
            if QUALITY_ZOO_SCENARIOS in entry.path.parents
        }

        self.assertEqual(on_disk, registered)

    def test_corpus_requires_cover_their_scenarios_declared_tools(self) -> None:
        """A corpus row's ``requires`` must cover exactly the ``[doctor]
        required_tools`` the scenario's own ici.toml declares — no phantom
        requirements hiding a gap, and none missing."""
        for entry in load_manifest().values():
            if QUALITY_ZOO_SCENARIOS not in entry.path.parents:
                continue
            config = tomli.loads((entry.path / "ici.toml").read_text(encoding="utf-8"))
            declared = set(config.get("doctor", {}).get("required_tools", ()))
            probed = {req["executable"] for req in entry.requires if "executable" in req}
            probed |= {name for req in entry.requires for name in req.get("any_executable", ())}
            # A cmake_package probe necessarily runs cmake, so it covers a
            # declared cmake requirement too.
            covered = declared - probed
            if any("cmake_package" in req for req in entry.requires):
                covered -= {"cmake"}

            self.assertFalse(covered, f"{entry.id}: required_tools {sorted(covered)} not probed")
            self.assertLessEqual(
                probed, declared, f"{entry.id}: phantom probes {sorted(probed - declared)}"
            )
            if not declared:
                self.assertEqual(entry.requires, (), entry.id)

    def test_corpus_package_probes_match_their_cmakelists(self) -> None:
        """A cmake_package probe must be what the fixture's own CMakeLists asks for."""
        for entry in load_manifest().values():
            if QUALITY_ZOO_SCENARIOS not in entry.path.parents:
                continue
            for requirement in entry.requires:
                if "cmake_package" not in requirement:
                    continue
                text = (entry.path / "CMakeLists.txt").read_text(encoding="utf-8")
                self.assertIn(f"find_package({requirement['cmake_package']}", text, entry.id)
                for component in requirement.get("components") or ():
                    self.assertIn(component, text, entry.id)

    def test_an_unknown_id_is_an_error(self) -> None:
        """A typo in a lookup must not quietly pass."""

        with self.assertRaisesRegex(ManifestError, "no fixture"):
            fixture("cpp/does-not-exist")


if __name__ == "__main__":
    unittest.main()
