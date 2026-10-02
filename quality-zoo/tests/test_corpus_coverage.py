"""The corpus must pin absence for every engine whose presence it pins.

A known-answer corpus that only asserts what an engine *does* report grows
one-sided: it catches an engine that stops detecting, and misses one that
starts over-detecting. Both are regressions, and the second is the one a user
notices first, because it arrives as noise in a report they trusted.

These tests read the scenarios themselves rather than a hand-kept list, so a
new engine scenario added without a matching absence fails here instead of
quietly widening the gap.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "scenarios"


def _expectations() -> list[tuple[Path, dict]]:
    return [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(CORPUS.glob("*/*/expectations/*.json"))
    ]


def _engines(items: list[dict]) -> set[str]:
    """Engines a predicate list names; unattributed predicates are ignored."""

    return {item["engine"] for item in items if item.get("engine")}


class CorpusCoverageTests(unittest.TestCase):
    def test_every_engine_with_an_expected_finding_also_has_a_pinned_absence(self) -> None:
        present: dict[str, set[str]] = {}
        absent: set[str] = set()
        for path, expectation in _expectations():
            scenario = path.parents[1].name
            for engine in _engines(expectation.get("findings", [])):
                present.setdefault(engine, set()).add(scenario)
            absent |= _engines(expectation.get("forbidden_findings", []))

        self.assertTrue(present, "the corpus expects no findings at all")
        missing = {engine: sorted(scenarios) for engine, scenarios in present.items() if engine not in absent}
        self.assertEqual(
            missing,
            {},
            "these engines are pinned for what they report but not for what they must stay "
            f"quiet about: {missing}",
        )

    def test_a_forbidden_predicate_names_an_engine_or_a_location(self) -> None:
        """A bare `{}` would forbid every finding and pass by accident.

        A predicate that constrains nothing matches everything, so a scenario
        carrying one would fail for reasons unrelated to the engine it meant to
        guard — or, if the run happened to be empty, pass while asserting
        nothing.
        """

        for path, expectation in _expectations():
            for index, item in enumerate(expectation.get("forbidden_findings", [])):
                with self.subTest(path=str(path), index=index):
                    self.assertTrue(
                        set(item) - {"count"},
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
                self.assertIn("findings", expectation)
                self.assertIn("forbidden_findings", expectation)


if __name__ == "__main__":
    unittest.main()
