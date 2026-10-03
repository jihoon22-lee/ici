from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from runner import report_contract, run
from runner.common import ContractError, sha256_file

from tests.helpers import (
    PRODUCER_VERSION,
    engine,
    report,
    write_fake_ici,
    write_manifest,
    write_scenario,
)

CANDIDATE_DIGEST = "e90140186f84ff17c4cda039d34c241b99f9ba74b07ea3b089a542c4bb620d9b"
PREVIOUS_CANDIDATE_DIGEST = (
    "23d9922b94b2ba34ab8884cd2d39c8eda358ccb32d0925af5c0a3d52a7ddc893"
)
EARLIER_CANDIDATE_DIGESTS = (
    "50d41d36775394f66f6620091f42a7a0333ee90758e19449a848d7ee0875a93c",
)


class RunContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def fixture(
        self,
        *,
        status: str = "PASS",
        report_status: str | None = None,
        producer_version: str = PRODUCER_VERSION,
        report_version: str | None = None,
        verify_exit: int = 0,
        report_payload: dict[str, object] | None = None,
        write_reports: bool = True,
        command: list[str] | None = None,
        profile: str = "fast",
        label: str = "fixture",
        version: str = PRODUCER_VERSION,
        version_output: str | None = None,
        version_exit: int = 0,
        stdout: str = "fake stdout\n",
        stderr: str = "fake stderr\n",
        output_bytes: int = 0,
    ) -> tuple[Path, Path, Path, Path, dict[str, object]]:
        base = self.root / label
        base.mkdir()
        scenario_root, scenario = write_scenario(
            base,
            scenario_id="python.example",
            profile=profile,
            command=command,
            expected_status=status,
            producer_version=producer_version,
        )
        if report_payload is None and write_reports:
            if report_status is None:
                report_status = status
            if report_version is None:
                report_version = producer_version
            report_payload = report(
                [engine("example", status=report_status)],
                suite_status=report_status,
                producer_version=report_version,
            )
        manifest = base / "manifest.json"
        write_manifest(manifest, [("python.example", "scenario")])
        fake = base / "ici"
        write_fake_ici(
            fake,
            report_payload,
            version=version,
            version_output=version_output,
            version_exit=version_exit,
            verify_exit=verify_exit,
            stdout=stdout,
            stderr=stderr,
            output_bytes=output_bytes,
        )
        output = base / "output"
        return manifest, scenario_root, fake, output, scenario

    def assert_error(self, callable_obj, code: str, *args, **kwargs) -> ContractError:
        with self.assertRaises(ContractError) as raised:
            callable_obj(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_validate_command_accepts_only_the_documented_argv(self) -> None:
        for value in (
            ["verify", "--profile", "fast"],
            ["verify", "--profile", "standard", "--no-cache"],
            ["verify", "--profile", "deep", "--no-cache"],
        ):
            with self.subTest(value=value):
                self.assertEqual(run._validate_command(value), value)

        for value in (
            None,
            "verify --profile fast",
            ["verify"],
            ["run", "--profile", "fast"],
            ["verify", "--profile", "unsafe"],
            ["verify", "--profile", "fast", "--shell"],
            ["verify", "--profile", "fast", "--no-cache", "extra"],
            ["verify", "--profile", 1],
        ):
            with self.subTest(value=value):
                self.assert_error(run._validate_command, "unsafe-command", value)

    def test_reject_symlinks_covers_root_and_nested_entries(self) -> None:
        target = self.root / "target"
        target.mkdir()
        root_link = self.root / "root-link"
        root_link.symlink_to(target, target_is_directory=True)
        self.assert_error(run._reject_symlinks, "unsafe-scenario", root_link)

        scenario = self.root / "scenario"
        scenario.mkdir()
        outside = self.root / "outside"
        outside.write_text("outside", encoding="utf-8")
        (scenario / "leak").symlink_to(outside)
        self.assert_error(run._reject_symlinks, "unsafe-scenario", scenario)

    def test_load_registry_accepts_valid_entries_and_rejects_manifest_boundaries(
        self,
    ) -> None:
        base = self.root / "registry"
        base.mkdir()
        scenario = base / "scenario"
        scenario.mkdir()
        manifest = base / "manifest.json"
        write_manifest(manifest, [("python.example", "scenario")])

        root, registry = run._load_registry(manifest)

        self.assertEqual(root, base)
        self.assertEqual(registry["python.example"], scenario.resolve())

        invalid_cases = (
            (
                "schema",
                {
                    "schema": 2,
                    "scenarios": [{"id": "python.example", "path": "scenario"}],
                },
                "manifest-schema",
            ),
            ("empty", {"schema": 1, "scenarios": []}, "manifest-scenarios"),
            (
                "extra",
                {
                    "schema": 1,
                    "scenarios": [
                        {"id": "python.example", "path": "scenario", "extra": 1}
                    ],
                },
                "manifest-entry",
            ),
            (
                "bad-id",
                {"schema": 1, "scenarios": [{"id": "Python", "path": "scenario"}]},
                "manifest-entry",
            ),
            (
                "duplicate",
                {
                    "schema": 1,
                    "scenarios": [
                        {"id": "python.example", "path": "scenario"},
                        {"id": "python.example", "path": "scenario"},
                    ],
                },
                "manifest-entry",
            ),
            (
                "escape",
                {
                    "schema": 1,
                    "scenarios": [{"id": "python.example", "path": "../outside"}],
                },
                "unsafe-path",
            ),
        )
        for name, payload, code in invalid_cases:
            with self.subTest(name=name):
                invalid_manifest = base / f"{name}.json"
                invalid_manifest.write_text(json.dumps(payload), encoding="utf-8")
                self.assert_error(run._load_registry, code, invalid_manifest)

    def test_load_scenario_enforces_identity_class_command_project_and_profile(
        self,
    ) -> None:
        _, scenario_root, _, _, scenario = self.fixture()
        self.assertEqual(run._load_scenario("python.example", scenario_root), scenario)

        cases = (
            ("schema", {"schema": 2}, "scenario-schema"),
            ("identity", {"scenario_id": "other"}, "scenario-schema"),
            ("class", {"class": "unknown"}, "scenario-schema"),
            ("command", {"command": ["sh", "-c", "unsafe"]}, "unsafe-command"),
            ("profile", {"profile": "deep"}, "scenario-profile"),
            ("project", {"project_root": "scenario.json"}, "scenario-project"),
            ("escape", {"project_root": "../"}, "unsafe-path"),
        )
        for name, updates, code in cases:
            with self.subTest(name=name):
                altered = copy.deepcopy(scenario)
                altered.update(updates)
                (scenario_root / "scenario.json").write_text(
                    json.dumps(altered), encoding="utf-8"
                )
                self.assert_error(
                    run._load_scenario, code, "python.example", scenario_root
                )
                (scenario_root / "scenario.json").write_text(
                    json.dumps(scenario), encoding="utf-8"
                )

        symlink_root = self.root / "scenario-link"
        symlink_root.symlink_to(scenario_root, target_is_directory=True)
        self.assert_error(
            run._load_scenario, "unsafe-scenario", "python.example", symlink_root
        )

    def test_digest_bound_scenario_selects_one_exact_expectation(self) -> None:
        manifest, scenario_root, fake, output, scenario = self.fixture()
        digest, _ = sha256_file(fake)
        expectations = scenario_root / "expectations"
        expectations.mkdir()
        (expectations / "expected.json").write_text(
            json.dumps(scenario), encoding="utf-8"
        )
        selector = {
            "schema": 2,
            "scenario_id": "python.example",
            "expectations": {digest: "expectations/expected.json"},
        }
        selector_path = scenario_root / "scenario.json"
        selector_path.write_text(json.dumps(selector), encoding="utf-8")

        self.assertEqual(
            run._load_scenario("python.example", scenario_root, digest), scenario
        )
        aggregate = run.run_manifest(manifest, [], fake, output, timeout_seconds=5)
        self.assertEqual(aggregate["contract_verdict"], "PASS")
        self.assertEqual(aggregate["results"][0]["ici_sha256"], digest)

        for label, update, code in (
            (
                "unknown",
                {"expectations": {"f" * 64: "expectations/expected.json"}},
                "unsupported-ici",
            ),
            ("empty", {"expectations": {}}, "scenario-schema"),
            (
                "bad-digest",
                {"expectations": {"not-a-digest": "expectations/expected.json"}},
                "scenario-schema",
            ),
            ("escape", {"expectations": {digest: "../expected.json"}}, "unsafe-path"),
            (
                "unselected-escape",
                {
                    "expectations": {
                        digest: "expectations/expected.json",
                        "f" * 64: "../expected.json",
                    }
                },
                "unsafe-path",
            ),
            ("extra", {"extra": True}, "scenario-schema"),
        ):
            with self.subTest(label=label):
                altered = copy.deepcopy(selector)
                altered.update(update)
                selector_path.write_text(json.dumps(altered), encoding="utf-8")
                self.assert_error(
                    run._load_scenario,
                    code,
                    "python.example",
                    scenario_root,
                    digest,
                )

        selector_path.write_text(json.dumps(selector), encoding="utf-8")
        self.assert_error(
            run._load_scenario,
            "unsupported-ici",
            "python.example",
            scenario_root,
        )

    def test_checked_in_registry_has_valid_digest_bound_expectations(self) -> None:
        quality_zoo_root = Path(__file__).resolve().parents[1]
        _, registry = run._load_registry(quality_zoo_root / "manifest.json")
        self.assertEqual(
            set(registry),
            {
                "cpp.asan-use-after-free",
                "cpp.lsan-memory-leak",
                "cpp.qt-missing-parent-constructor",
                "cpp.sanitizer-clean",
                "cpp.ubsan-signed-overflow",
                "python.dead-private-function",
            },
        )

        for scenario_id, scenario_root in registry.items():
            selector = json.loads(
                (scenario_root / "scenario.json").read_text(encoding="utf-8")
            )
            with self.subTest(scenario_id=scenario_id):
                self.assertEqual(selector["schema"], 2)
                self.assertTrue(selector["expectations"])
            for digest in selector["expectations"]:
                with self.subTest(scenario_id=scenario_id, digest=digest):
                    expectation = run._load_scenario(scenario_id, scenario_root, digest)
                    report_contract._validate_expectation(expectation)

        runtime_sanitizer_ids = {
            "cpp.asan-use-after-free",
            "cpp.lsan-memory-leak",
            "cpp.sanitizer-clean",
            "cpp.ubsan-signed-overflow",
        }
        for scenario_id in runtime_sanitizer_ids:
            selector = json.loads(
                (registry[scenario_id] / "scenario.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(selector["expectations"]),
                {
                    "8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4",
                    "e7f1a2ce7147057538873a802715c7bf2b12e530a85070af862e02e378caceb8",
                    "985c81a63363356619207870cddb0d8cd9854a46925a3e0a745e54bd543d5b51",
                    "424108397858470b1209bc2749b580a858fb06c8b09aaa2e4772c94e43690bb5",
                    *EARLIER_CANDIDATE_DIGESTS,
                    PREVIOUS_CANDIDATE_DIGEST,
                    CANDIDATE_DIGEST,
                },
            )

        qt_selector = json.loads(
            (registry["cpp.qt-missing-parent-constructor"] / "scenario.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(qt_selector["expectations"]),
            {
                "8e6237302ff3b6198cad86c97dd6bcd666ecab9204e9e19209e2e310c7fd18f4",
                "985c81a63363356619207870cddb0d8cd9854a46925a3e0a745e54bd543d5b51",
                "424108397858470b1209bc2749b580a858fb06c8b09aaa2e4772c94e43690bb5",
                *EARLIER_CANDIDATE_DIGESTS,
                PREVIOUS_CANDIDATE_DIGEST,
                CANDIDATE_DIGEST,
            },
        )

    def test_candidate_registry_keeps_stable_slice_and_adds_candidate_corpus(
        self,
    ) -> None:
        quality_zoo_root = Path(__file__).resolve().parents[1]
        _, registry = run._load_registry(quality_zoo_root / "candidate-manifest.json")
        self.assertEqual(
            set(registry),
            {
                "cpp.asan-use-after-free",
                "cpp.lsan-memory-leak",
                "cpp.malformed-compile-db",
                "cpp.quality-coverage",
                "cpp.qt-missing-parent-constructor",
                "cpp.sanitizer-clean",
                "cpp.static-build-context",
                "cpp.tsan-data-race",
                "cpp.tsan-synchronized",
                "cpp.ubsan-signed-overflow",
                "python.compatibility-package-metadata",
                "python.dead-private-function",
                "python.import-cycle-exception",
                "python.maintainability-thresholds",
                "python.security-resource-correctness",
                "cpp.make-elf-integration",
            },
        )
        stable_root = quality_zoo_root / "manifest.json"
        _, stable_registry = run._load_registry(stable_root)
        self.assertTrue(set(stable_registry) < set(registry))

        for scenario_id, scenario_root in registry.items():
            with self.subTest(scenario_id=scenario_id, digest=CANDIDATE_DIGEST):
                selector = json.loads(
                    (scenario_root / "scenario.json").read_text(encoding="utf-8")
                )
                self.assertEqual(selector["schema"], 2)
                self.assertIn(CANDIDATE_DIGEST, selector["expectations"])
                expectation = run._load_scenario(
                    scenario_id, scenario_root, CANDIDATE_DIGEST
                )
                report_contract._validate_expectation(expectation)

        for scenario_id in ("cpp.tsan-data-race", "cpp.tsan-synchronized"):
            with self.subTest(scenario_id=scenario_id):
                selector = json.loads(
                    (registry[scenario_id] / "scenario.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    set(selector["expectations"]),
                    {
                        "424108397858470b1209bc2749b580a858fb06c8b09aaa2e4772c94e43690bb5",
                        *EARLIER_CANDIDATE_DIGESTS,
                        PREVIOUS_CANDIDATE_DIGEST,
                        CANDIDATE_DIGEST,
                    },
                )
                expectation = run._load_scenario(
                    scenario_id,
                    registry[scenario_id],
                    "424108397858470b1209bc2749b580a858fb06c8b09aaa2e4772c94e43690bb5",
                )
                report_contract._validate_expectation(expectation)

        self.assertNotIn("cpp.tsan-data-race", stable_registry)
        self.assertNotIn("cpp.tsan-synchronized", stable_registry)

    def test_run_manifest_success_copies_reports_and_records_reproducible_summary(
        self,
    ) -> None:
        manifest, _, fake, output, _ = self.fixture()

        aggregate = run.run_manifest(manifest, [], fake, output, timeout_seconds=5)

        self.assertEqual(aggregate["schema"], "quality-zoo.suite/v1")
        self.assertEqual(aggregate["contract_verdict"], "PASS")
        self.assertEqual(aggregate["scenario_count"], 1)
        self.assertEqual(aggregate["ici"]["version"], PRODUCER_VERSION)
        result = aggregate["results"][0]
        self.assertEqual(result["contract_verdict"], "PASS")
        self.assertEqual(result["observed_suite_status"], "PASS")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "fake stdout\n")
        self.assertEqual(result["stderr"], "fake stderr\n")
        self.assertEqual(
            result["argv"],
            [
                "verify",
                "--profile",
                "fast",
                "--no-cache",
                "--report",
                "--html",
                "verify_report.html",
            ],
        )
        scenario_output = output / "python.example"
        self.assertEqual(
            (scenario_output / "report.json").read_text(encoding="utf-8"),
            json.dumps(
                report(
                    [engine("example")],
                    suite_status="PASS",
                    producer_version=PRODUCER_VERSION,
                )
            ),
        )
        self.assertTrue((scenario_output / "report.html").is_file())
        self.assertTrue((scenario_output / "run.json").is_file())
        self.assertTrue((output / "suite.json").is_file())

    def test_expected_exit_code_matches_each_suite_status(self) -> None:
        expected_exit = {"PASS": 0, "WARN": 0, "FAIL": 1, "ERROR": 1, "SKIP": 2}
        for status, exit_code in expected_exit.items():
            with self.subTest(status=status):
                manifest, _, fake, output, _ = self.fixture(
                    status=status,
                    verify_exit=exit_code,
                    label=f"status-{status.lower()}",
                )
                aggregate = run.run_manifest(
                    manifest, [], fake, output, timeout_seconds=5
                )
                result = aggregate["results"][0]
                self.assertEqual(result["contract_verdict"], "PASS")
                self.assertEqual(result["observed_suite_status"], status)
                self.assertEqual(result["exit_code"], exit_code)

    def test_exit_code_mismatch_fails_contract_without_discarding_artifacts(
        self,
    ) -> None:
        manifest, _, fake, output, _ = self.fixture(verify_exit=7)

        aggregate = run.run_manifest(manifest, [], fake, output, timeout_seconds=5)

        result = aggregate["results"][0]
        self.assertEqual(aggregate["contract_verdict"], "FAIL")
        self.assertEqual(result["contract_verdict"], "FAIL")
        self.assertEqual(result["exit_code"], 7)
        self.assertTrue(
            any("does not match suite status" in error for error in result["errors"])
        )
        self.assertTrue((output / "python.example" / "report.json").is_file())

    def test_producer_version_mismatch_fails_even_when_report_contract_matches(
        self,
    ) -> None:
        manifest, _, fake, output, _ = self.fixture(
            producer_version="9.9.9",
            report_version="9.9.9",
        )

        aggregate = run.run_manifest(manifest, [], fake, output, timeout_seconds=5)

        result = aggregate["results"][0]
        self.assertEqual(aggregate["ici"]["version"], PRODUCER_VERSION)
        self.assertEqual(result["producer_version"], "9.9.9")
        self.assertEqual(result["contract_verdict"], "FAIL")
        self.assertTrue(any("producer" in error for error in result["errors"]))

    def test_missing_reports_are_rejected_after_execution(self) -> None:
        manifest, _, fake, output, _ = self.fixture(write_reports=False)

        self.assert_error(
            run.run_manifest,
            "runner-report-missing",
            manifest,
            [],
            fake,
            output,
            timeout_seconds=5,
        )
        self.assertTrue((output / "python.example").is_dir())
        self.assertFalse((output / "suite.json").exists())
        diagnostic = json.loads(
            (output / "python.example" / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(diagnostic["schema"], "quality-zoo.runner-error/v1")
        self.assertEqual(diagnostic["error_code"], "runner-report-missing")
        self.assertEqual(diagnostic["reports_present"], {"json": False, "html": False})
        self.assertEqual(diagnostic["stderr"], "fake stderr\n")

    def test_unsafe_command_is_rejected_before_fake_binary_runs(self) -> None:
        manifest, scenario_root, fake, output, scenario = self.fixture(
            command=["verify", "--profile", "fast", "--no-cache", "--shell"]
        )
        self.assert_error(
            run.run_manifest,
            "unsafe-command",
            manifest,
            [],
            fake,
            output,
            timeout_seconds=5,
        )
        self.assertFalse((output / "python.example").exists())
        self.assertEqual(
            json.loads((scenario_root / "scenario.json").read_text())["command"],
            scenario["command"],
        )

    def test_manifest_selection_rejects_unknown_duplicate_and_existing_output(
        self,
    ) -> None:
        manifest, _, fake, output, _ = self.fixture()
        self.assert_error(
            run.run_manifest,
            "scenario-selection",
            manifest,
            ["unknown"],
            fake,
            output,
            timeout_seconds=5,
        )
        self.assert_error(
            run.run_manifest,
            "scenario-selection",
            manifest,
            ["python.example", "python.example"],
            fake,
            self.root / "duplicate-output",
            timeout_seconds=5,
        )
        output.mkdir()
        self.assert_error(
            run.run_manifest,
            "output-exists",
            manifest,
            [],
            fake,
            output,
            timeout_seconds=5,
        )

    def test_non_executable_ici_binary_is_rejected(self) -> None:
        manifest, _, fake, output, _ = self.fixture()
        fake.chmod(0o644)

        self.assert_error(
            run.run_manifest,
            "unsafe-ici-bin",
            manifest,
            [],
            fake,
            output,
            timeout_seconds=5,
        )

        fake.chmod(0o755)
        fake_link = self.root / "ici-link"
        fake_link.symlink_to(fake)
        self.assert_error(
            run.run_manifest,
            "unsafe-ici-bin",
            manifest,
            [],
            fake_link,
            self.root / "link-output",
            timeout_seconds=5,
        )

    def test_version_probe_rejects_nonzero_or_malformed_fake_binary(self) -> None:
        for label, kwargs in (
            ("version-exit", {"version_exit": 3}),
            ("version-output", {"version_output": "not ici\n"}),
        ):
            with self.subTest(label=label):
                manifest, _, fake, output, _ = self.fixture(
                    label=label,
                    version_exit=kwargs.get("version_exit", 0),
                    version_output=kwargs.get("version_output"),
                )
                self.assert_error(
                    run.run_manifest,
                    "ici-version-failed",
                    manifest,
                    [],
                    fake,
                    output,
                    timeout_seconds=5,
                )

    def test_output_is_bounded_and_utf8_is_decoded(self) -> None:
        manifest, _, fake, output, _ = self.fixture(
            output_bytes=run.MAX_TOOL_OUTPUT_BYTES + 17
        )

        aggregate = run.run_manifest(manifest, [], fake, output, timeout_seconds=5)

        result = aggregate["results"][0]
        self.assertTrue(result["stdout_truncated"])
        self.assertEqual(len(result["stdout"]), run.MAX_TOOL_OUTPUT_BYTES)
        self.assertFalse(result["stderr_truncated"])

    def test_main_returns_zero_one_and_two_for_pass_failure_and_contract_error(
        self,
    ) -> None:
        manifest, _, fake, output, _ = self.fixture(label="main-pass")
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(
                run.main(
                    [
                        "--manifest",
                        str(manifest),
                        "--ici-bin",
                        str(fake),
                        "--output-dir",
                        str(output),
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(stdout.getvalue())["contract_verdict"], "PASS")

        fail_manifest, _, fail_fake, fail_output, _ = self.fixture(
            verify_exit=5, label="main-fail"
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(
                run.main(
                    [
                        "--manifest",
                        str(fail_manifest),
                        "--ici-bin",
                        str(fail_fake),
                        "--output-dir",
                        str(fail_output),
                    ]
                ),
                1,
            )

        bad_manifest, _, bad_fake, bad_output, _ = self.fixture(label="main-error")
        bad_fake.chmod(0o644)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            run.main(
                [
                    "--manifest",
                    str(bad_manifest),
                    "--ici-bin",
                    str(bad_fake),
                    "--output-dir",
                    str(bad_output),
                ]
            )
        self.assertEqual(raised.exception.code, 2)


class EngineRegressionSummaryTest(unittest.TestCase):
    """The one-screen view must name the engine, not just list sentences."""

    @staticmethod
    def _results() -> list[dict]:
        return [
            {
                "scenario_id": "cpp.asan-uaf",
                "contract_verdict": "FAIL",
                "failures": [
                    {
                        "engine": "sanitize",
                        "kind": "finding-missing",
                        "detail": "expected finding #0 was not matched",
                    },
                    {
                        "engine": "sanitize",
                        "kind": "engine-status",
                        "detail": "engine sanitize status 'PASS' != 'FAIL'",
                    },
                ],
            },
            {
                "scenario_id": "python.dead",
                "contract_verdict": "FAIL",
                "failures": [
                    {
                        "engine": "",
                        "kind": "suite-status",
                        "detail": "suite status WARN != FAIL",
                    },
                    {
                        "engine": "dead",
                        "kind": "finding-missing",
                        "detail": "expected finding #1 was not matched",
                    },
                ],
            },
            {"scenario_id": "cpp.clean", "contract_verdict": "PASS", "failures": []},
        ]

    def test_failures_group_by_engine_worst_first(self):
        grouped = run._engine_regressions(self._results())

        self.assertEqual(
            [(item["engine"], item["failure_count"]) for item in grouped],
            [("sanitize", 2), ("", 1), ("dead", 1)],
        )
        sanitize = grouped[0]
        self.assertEqual(sanitize["scenarios"], ["cpp.asan-uaf"])
        self.assertEqual(sanitize["kinds"], ["engine-status", "finding-missing"])

    def test_one_scenario_is_listed_once_per_engine(self):
        results = self._results()
        results[0]["failures"].append(
            {
                "engine": "sanitize",
                "kind": "finding-missing",
                "detail": "expected finding #1 was not matched",
            }
        )

        grouped = run._engine_regressions(results)

        self.assertEqual(grouped[0]["failure_count"], 3)
        self.assertEqual(grouped[0]["scenarios"], ["cpp.asan-uaf"])

    def test_detail_lines_are_capped_and_the_remainder_is_named(self):
        results = [
            {
                "scenario_id": "python.dead",
                "contract_verdict": "FAIL",
                "failures": [
                    {
                        "engine": "dead",
                        "kind": "finding-missing",
                        "detail": f"expected finding #{index} was not matched",
                    }
                    for index in range(7)
                ],
            }
        ]
        aggregate = {
            "contract_verdict": "FAIL",
            "scenario_count": 1,
            "ici": {"version": "0.10.2"},
            "results": results,
            "engine_regressions": run._engine_regressions(results),
        }

        rendered = run.render_engine_regression_summary(aggregate)

        self.assertEqual(len(aggregate["engine_regressions"][0]["details"]), 3)
        self.assertIn("(+4 more in suite.json)", rendered)
        self.assertIn("0/1 scenario contracts passed", rendered)

    def test_a_passing_suite_renders_a_single_line(self):
        aggregate = {
            "contract_verdict": "PASS",
            "scenario_count": 16,
            "ici": {"version": "0.10.2"},
            "results": [],
            "engine_regressions": [],
        }

        rendered = run.render_engine_regression_summary(aggregate)

        self.assertEqual(rendered.count("\n"), 0)
        self.assertIn("16/16 scenario contracts passed", rendered)


if __name__ == "__main__":
    unittest.main()
