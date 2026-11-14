from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from runner import run
from runner.candidate_overrides import (
    NATIVE,
    PYZ,
    REVIEWED_EXTRA_OVERRIDES,
    candidate_format,
)
from runner.common import ContractError
from runner.report_contract import evaluate_contract

from tests.helpers import (
    engine,
    report,
    write_fake_ici,
    write_manifest,
    write_scenario,
)

SCENARIO = "cpp.static-build-context"
FROZEN = "build/ici-cmake-build/compile_commands.json"
# The value the native candidate wrote (/tmp/ici-rust-replacement-20261028/w-runtime-sbc-database-path.log).
NATIVE_PATH = ".ici/rust-v1/build/legacy-244210e48437b655/release/compile_commands.json"
CORPUS = Path(__file__).resolve().parents[1]


def expectation(scenario_id: str = SCENARIO, **extra: object) -> dict[str, object]:
    return {
        "schema": 1,
        "scenario_id": scenario_id,
        "class": "red",
        "project_root": ".",
        "profile": "deep",
        "command": ["verify", "--profile", "deep", "--no-cache"],
        "suite_status": "PASS",
        "producer_version": "1.2.3",
        "engines": [
            {
                "name": "compile_db",
                "status": "PASS",
                "extra": {"database_path": FROZEN, "production_units": 5, **extra},
            }
        ],
        "findings": [],
        "forbidden_findings": [],
        "required_capabilities": [],
        "skip_policy": "forbid",
    }


def observed(database_path: str, production_units: int = 5) -> dict[str, object]:
    return report(
        [
            engine(
                "compile_db",
                extra={"database_path": database_path, "production_units": production_units},
            )
        ]
    )


class CandidateOverrideTests(unittest.TestCase):
    def test_native_value_matching_the_reviewed_pattern_passes(self) -> None:
        result = evaluate_contract(observed(NATIVE_PATH), expectation(), candidate_format=NATIVE)
        self.assertEqual(result.verdict, "PASS", result.errors)

    def test_native_value_outside_the_pattern_is_an_engine_extra_failure(self) -> None:
        for value in (
            FROZEN,
            ".ici/rust-v1/build/legacy-244210e4/release/compile_commands.json",
            ".ici/rust-v1/build/legacy-244210e48437b655/asan/compile_commands.json",
            "/abs/" + NATIVE_PATH,
            NATIVE_PATH + "\n",
        ):
            result = evaluate_contract(observed(value), expectation(), candidate_format=NATIVE)
            self.assertEqual(result.verdict, "FAIL", value)
            self.assertEqual(
                [failure.as_dict() for failure in result.failures],
                [
                    {
                        "engine": "compile_db",
                        "kind": "engine-extra",
                        "detail": "engine compile_db extra.database_path does not match "
                        "the reviewed native pattern",
                    }
                ],
            )
        result = evaluate_contract(
            report([engine("compile_db", extra={"production_units": 5})]),
            expectation(),
            candidate_format=NATIVE,
        )
        self.assertEqual(result.verdict, "FAIL")

    def test_pyz_candidate_keeps_the_exact_frozen_answer(self) -> None:
        passed = evaluate_contract(observed(FROZEN), expectation(), candidate_format=PYZ)
        self.assertEqual(passed.verdict, "PASS", passed.errors)
        failed = evaluate_contract(observed(NATIVE_PATH), expectation())
        self.assertEqual(failed.verdict, "FAIL")
        self.assertEqual(
            failed.errors, ("engine compile_db extra.database_path differs",)
        )

    def test_no_other_field_or_scenario_is_overridden(self) -> None:
        other_key = evaluate_contract(
            observed(NATIVE_PATH, production_units=4), expectation(), candidate_format=NATIVE
        )
        self.assertEqual(
            other_key.errors, ("engine compile_db extra.production_units differs",)
        )
        other_scenario = evaluate_contract(
            observed(NATIVE_PATH),
            expectation("cpp.malformed-compile-db"),
            candidate_format=NATIVE,
        )
        self.assertEqual(
            other_scenario.errors, ("engine compile_db extra.database_path differs",)
        )
        self.assertEqual(
            [
                (item.candidate_format, item.scenario_id, item.engine, item.key)
                for item in REVIEWED_EXTRA_OVERRIDES
            ],
            [(NATIVE, SCENARIO, "compile_db", "database_path")],
        )

    def test_an_answer_that_no_longer_holds_the_reviewed_frozen_value_is_refused(self) -> None:
        stale = expectation()
        stale["engines"][0]["extra"]["database_path"] = "build/other/compile_commands.json"
        with self.assertRaises(ContractError) as raised:
            evaluate_contract(observed(NATIVE_PATH), stale, candidate_format=NATIVE)
        self.assertEqual(raised.exception.code, "override-stale")
        with self.assertRaises(ContractError) as raised:
            evaluate_contract(observed(FROZEN), expectation(), candidate_format="wheel")
        self.assertEqual(raised.exception.code, "candidate-format")

    def test_every_bound_corpus_answer_carries_the_reviewed_frozen_value(self) -> None:
        for override in REVIEWED_EXTRA_OVERRIDES:
            root = CORPUS / "scenarios" / override.scenario_id.replace(".", "/", 1)
            selector = json.loads((root / "scenario.json").read_text(encoding="utf-8"))
            for relative in sorted(set(selector["expectations"].values())):
                answer = json.loads((root / relative).read_text(encoding="utf-8"))
                engines = {item["name"]: item for item in answer["engines"]}
                self.assertEqual(
                    engines[override.engine]["extra"][override.key],
                    override.frozen_value,
                    relative,
                )

    def test_candidate_format_is_read_from_the_executable_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            elf = root / "ici"
            elf.write_bytes(b"\x7fELF\x02\x01\x01" + b"\0" * 64)
            self.assertEqual(candidate_format(elf), NATIVE)
            pyz = root / "ici.pyz"
            with zipfile.ZipFile(pyz, "w") as archive:
                archive.writestr("__main__.py", "print('ici')\n")
            pyz.write_bytes(b"#!/bin/sh\n" + pyz.read_bytes())
            self.assertEqual(candidate_format(pyz), PYZ)
            with self.assertRaises(ContractError):
                candidate_format(root / "missing")

    def test_runner_records_the_candidate_format_and_holds_a_script_to_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            scenario_root, scenario = write_scenario(root, scenario_id=SCENARIO)
            scenario.update(expectation())
            scenario["engines"][0]["status"] = "PASS"
            (scenario_root / "scenario.json").write_text(json.dumps(scenario), encoding="utf-8")
            manifest = root / "manifest.json"
            write_manifest(manifest, [(SCENARIO, "scenario")])
            fake = root / "ici"
            write_fake_ici(fake, observed(NATIVE_PATH), version="1.2.3")
            aggregate = run.run_manifest(
                manifest, [], fake, root / "out", timeout_seconds=30
            )
            self.assertEqual(aggregate["ici"]["format"], PYZ)
            self.assertEqual(aggregate["results"][0]["ici_format"], PYZ)
            self.assertEqual(
                aggregate["results"][0]["errors"],
                ["engine compile_db extra.database_path differs"],
            )


if __name__ == "__main__":
    unittest.main()
