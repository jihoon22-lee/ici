from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runner import bind_candidate, run
from runner.common import ContractError

from tests.helpers import write_manifest, write_scenario

REFERENCE = "c179986f3d0eac110cf436a961458e56c0f4a218517e9883ff47cd67addc1cde"
CANDIDATE = "a" * 64


def selector_scenario(root: Path) -> tuple[Path, dict]:
    scenario_root, scenario = write_scenario(
        root,
        expected_status="WARN",
        producer_version="0.13.0",
        findings=[{"engine": "example", "rule_id": "ici.example", "tool_rule_id": ""}],
    )
    (scenario_root / "expectations").mkdir()
    (scenario_root / "expectations/reference.json").write_text(
        json.dumps(scenario, indent=1) + "\n", encoding="utf-8"
    )
    selector = {
        "schema": 2,
        "scenario_id": scenario["scenario_id"],
        "expectations": {REFERENCE: "expectations/reference.json"},
    }
    (scenario_root / "scenario.json").write_text(
        json.dumps(selector, indent=2) + "\n", encoding="utf-8"
    )
    manifest = root / "manifest.json"
    write_manifest(manifest, [(scenario["scenario_id"], "scenario")])
    return manifest, scenario


class BindCandidateTests(unittest.TestCase):
    def test_binding_copies_every_answer_and_changes_only_the_producer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, reference = selector_scenario(root)
            written = bind_candidate.bind(
                manifest,
                reference_sha256=REFERENCE,
                sha256=CANDIDATE,
                producer_version="0.14.0",
                name="candidate-native-0.14.0",
            )
            self.assertEqual(len(written), 2)
            selector = json.loads((root / "scenario/scenario.json").read_text())
            self.assertEqual(
                selector["expectations"],
                {
                    REFERENCE: "expectations/reference.json",
                    CANDIDATE: "expectations/candidate-native-0.14.0.json",
                },
            )
            derived = json.loads(
                (root / "scenario/expectations/candidate-native-0.14.0.json").read_text()
            )
            self.assertEqual(derived["producer_version"], "0.14.0")
            self.assertEqual(
                {**derived, "producer_version": reference["producer_version"]}, reference
            )
            # The runner now selects the derived answer for the bound digest.
            loaded = run._load_scenario(reference["scenario_id"], root / "scenario", CANDIDATE)
            self.assertEqual(loaded, derived)

    def test_binding_refuses_rebinding_missing_references_and_bad_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = selector_scenario(root)
            arguments = {
                "reference_sha256": REFERENCE,
                "sha256": CANDIDATE,
                "producer_version": "0.14.0",
                "name": "candidate-native",
            }
            bind_candidate.bind(manifest, **arguments)
            before = (root / "scenario/scenario.json").read_text()
            cases = [
                ({}, "already-bound"),
                ({"sha256": "b" * 64, "reference_sha256": "c" * 64}, "reference-missing"),
                ({"sha256": REFERENCE}, "invalid-digest"),
                ({"sha256": "B" * 64}, "invalid-digest"),
                ({"sha256": "b" * 64, "name": "../escape"}, "invalid-name"),
            ]
            for overrides, code in cases:
                with self.subTest(code=code), self.assertRaises(ContractError) as raised:
                    bind_candidate.bind(manifest, **{**arguments, **overrides})
                self.assertEqual(raised.exception.code, code)
            # Nothing was rewritten by a refused binding.
            self.assertEqual((root / "scenario/scenario.json").read_text(), before)

    def test_binding_never_replaces_a_differing_expectation_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest, _ = selector_scenario(root)
            (root / "scenario/expectations/candidate-native.json").write_text(
                '{"edited": true}\n', encoding="utf-8"
            )
            with self.assertRaises(ContractError) as raised:
                bind_candidate.bind(
                    manifest,
                    reference_sha256=REFERENCE,
                    sha256=CANDIDATE,
                    producer_version="0.14.0",
                    name="candidate-native",
                )
            self.assertEqual(raised.exception.code, "expectation-exists")

    def test_executable_digest_bound_admits_the_measured_native_release_binary(self) -> None:
        # R8: the musl release binary measured 36,130,096 bytes; the bound stays fixed.
        self.assertGreater(run.MAX_ICI_BIN_BYTES, 36_130_096)
        self.assertEqual(run.MAX_ICI_BIN_BYTES, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
