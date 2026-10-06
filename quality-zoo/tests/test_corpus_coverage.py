"""The corpus must pin absence for every check whose presence it pins.

A known-answer corpus that only asserts what a check *does* report grows
one-sided: it catches a check that stops detecting, and misses one that
starts over-detecting. Both are regressions, and the second is the one a user
notices first, because it arrives as noise in a report they trusted.

These tests read the schema-3 (``ici.next.run``) expectations themselves
rather than a hand-kept list, so a new scenario added without a matching
absence fails here instead of quietly widening the gap.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "scenarios"


def _expectations() -> list[tuple[Path, dict]]:
    pairs = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(CORPUS.glob("*/*/expectations/*.json"))
    ]
    return [(path, doc) for path, doc in pairs if doc.get("schema") == 3]


def _providers(items: list[dict]) -> set[str]:
    """Providers a predicate list names; unattributed predicates are ignored."""

    return {item["provider"] for item in items if item.get("provider")}


def _expected(expectation: dict) -> dict:
    return expectation.get("expected") or {}


class CorpusCoverageTests(unittest.TestCase):
    def test_every_provider_with_an_expected_finding_also_has_a_pinned_absence(self) -> None:
        present: dict[str, set[str]] = {}
        absent: set[str] = set()
        for path, expectation in _expectations():
            scenario = path.parents[1].name
            expected = _expected(expectation)
            for provider in _providers(expected.get("findings", [])):
                present.setdefault(provider, set()).add(scenario)
            absent |= _providers(expected.get("forbidden_findings", []))

        self.assertTrue(present, "the corpus expects no findings at all")
        missing = {provider: sorted(scenarios) for provider, scenarios in present.items() if provider not in absent}
        self.assertEqual(
            missing,
            {},
            "these providers are pinned for what they report but not for what they must stay "
            f"quiet about: {missing}",
        )

    def test_a_forbidden_predicate_constrains_something(self) -> None:
        """A bare `{}` would forbid every finding and pass by accident.

        A predicate that constrains nothing matches everything, so a scenario
        carrying one would fail for reasons unrelated to the check it meant to
        guard — or, if the run happened to be empty, pass while asserting
        nothing.
        """

        for path, expectation in _expectations():
            for index, item in enumerate(_expected(expectation).get("forbidden_findings", [])):
                with self.subTest(path=str(path), index=index):
                    self.assertTrue(
                        item,
                        f"{path.name} forbidden_findings[{index}] constrains nothing",
                    )

    def test_every_scenario_expectation_declares_both_directions(self) -> None:
        """Both keys must be present, even when one is empty.

        An expectation missing `forbidden_findings` entirely reads as "absence
        was not considered" rather than "absence was considered and there is
        nothing to pin". The runner treats them the same; a reader does not.
        """

        for path, expectation in _expectations():
            with self.subTest(path=str(path)):
                expected = _expected(expectation)
                self.assertIn("findings", expected)
                self.assertIn("forbidden_findings", expected)


if __name__ == "__main__":
    unittest.main()
