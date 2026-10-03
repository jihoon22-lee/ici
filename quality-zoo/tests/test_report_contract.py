from __future__ import annotations

import copy
import math
import unittest

from runner.common import ContractError
from runner.report_contract import evaluate_contract, validate_report

from tests.helpers import engine, finding, location, report


class ReportContractTests(unittest.TestCase):
    def expectation(self, **updates: object) -> dict[str, object]:
        expectation: dict[str, object] = {
            "schema": 1,
            "scenario_id": "python.example",
            "class": "stable",
            "project_root": ".",
            "profile": "fast",
            "command": ["verify", "--profile", "fast", "--no-cache"],
            "suite_status": "PASS",
            "producer_version": "1.2.3",
            "engines": [],
            "findings": [],
            "forbidden_findings": [],
            "required_capabilities": [],
            "skip_policy": "forbid",
        }
        expectation.update(updates)
        return expectation

    def assert_schema_error(
        self, payload: dict[str, object], code: str = "report-schema"
    ) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_report(payload)
        self.assertEqual(raised.exception.code, code)

    def test_valid_v3_report_returns_metadata_and_engines(self) -> None:
        engines = [
            engine("pass", status="PASS"),
            engine("warn", status="WARN", evidence="ESTIMATED"),
            engine("fail", status="FAIL", evidence="NOT_RUN"),
            engine("error", status="ERROR", evidence="NOT_APPLICABLE"),
            engine("skip", status="SKIP", required=False),
        ]
        payload = report(engines, suite_status="ERROR")

        metadata, validated_engines = validate_report(payload)

        self.assertEqual(metadata["producer_version"], "1.2.3")
        self.assertEqual(
            [item["engine_name"] for item in validated_engines],
            [
                "pass",
                "warn",
                "fail",
                "error",
                "skip",
            ],
        )
        self.assertEqual(payload["total_count"], 5)
        self.assertEqual(payload["failed_count"], 2)
        self.assertEqual(payload["error_count"], 1)

    def test_result_counts_must_match_result_statuses(self) -> None:
        payload = report(
            [
                engine("pass", status="PASS"),
                engine("warn", status="WARN"),
                engine("fail", status="FAIL"),
                engine("error", status="ERROR"),
                engine("skip", status="SKIP"),
            ]
        )
        for field in (
            "passed_count",
            "warned_count",
            "failed_count",
            "error_count",
            "skipped_count",
            "total_count",
        ):
            with self.subTest(field=field):
                altered = copy.deepcopy(payload)
                altered[field] += 1
                self.assert_schema_error(altered, "report-count")

    def test_total_count_must_equal_results_length(self) -> None:
        payload = report()
        payload["total_count"] = 0

        self.assert_schema_error(payload, "report-count")

    def test_top_level_schema_status_duration_and_counts_are_strict(self) -> None:
        payload = report()
        cases = (
            ("schema_version", "ici.result/v2"),
            ("suite_status", "BROKEN"),
            ("duration", -0.01),
            ("duration", math.nan),
            ("duration", math.inf),
            ("passed_count", True),
            ("failed_count", -1),
            ("results", {}),
            ("analysis_metadata", []),
            ("analysis_metadata", {"producer_version": 7}),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                altered = copy.deepcopy(payload)
                altered[field] = value
                if field == "schema_version":
                    expected_code = "report-schema-version"
                elif field in {"passed_count", "failed_count"} or (
                    field == "analysis_metadata" and isinstance(value, dict)
                ):
                    expected_code = "invalid-field"
                else:
                    expected_code = "report-schema"
                self.assert_schema_error(altered, expected_code)

    def test_engine_identity_status_evidence_and_boolean_fields_are_validated(
        self,
    ) -> None:
        base = report()
        cases = (
            ("schema_version", "ici.result/v2"),
            ("engine_name", ""),
            ("status", "BROKEN"),
            ("evidence", "BROKEN"),
            ("required", 1),
            ("cache_hit", "no"),
            ("findings", {}),
        )
        for field, value in cases:
            with self.subTest(field=field, value=value):
                altered = copy.deepcopy(base)
                altered["results"][0][field] = value
                if field == "status":
                    altered.update(
                        {
                            "passed_count": 0,
                            "warned_count": 0,
                            "failed_count": 0,
                            "error_count": 0,
                            "skipped_count": 0,
                        }
                    )
                expected = (
                    "invalid-field"
                    if field in {"engine_name", "required", "cache_hit"}
                    else "report-schema"
                )
                self.assert_schema_error(altered, expected)

        duplicate = report([engine("same"), engine("same")])
        self.assert_schema_error(duplicate)

    def test_location_paths_are_project_relative_canonical_and_portable(self) -> None:
        for bad_path in (
            "/tmp/example.py",
            "../example.py",
            "src\\example.py",
            "./src/example.py",
            "C:/src/example.py",
        ):
            with self.subTest(path=bad_path):
                payload = report(
                    [engine(findings=[finding(location_value=location(path=bad_path))])]
                )
                self.assert_schema_error(payload)

        payload = report(
            [
                engine(
                    findings=[finding(location_value=location(path="src//example.py"))]
                )
            ]
        )
        self.assert_schema_error(payload)

    def test_location_ranges_columns_and_labels_are_checked(self) -> None:
        cases = (
            {"start_line": 0},
            {"start_line": True},
            {"end_line": 0},
            {"end_line": 2, "start_line": 3},
            {"start_column": 0},
            {"end_column": False},
            {"label": 1},
        )
        for updates in cases:
            with self.subTest(updates=updates):
                value = location()
                value.update(updates)
                payload = report([engine(findings=[finding(location_value=value)])])
                expected = (
                    "report-schema"
                    if updates == {"end_line": 2, "start_line": 3}
                    else "invalid-field"
                )
                self.assert_schema_error(payload, expected)

        missing = location()
        del missing["end_column"]
        payload = report([engine(findings=[finding(location_value=missing)])])
        self.assert_schema_error(payload)

    def test_all_finding_fields_and_nested_shapes_are_required(self) -> None:
        required_fields = (
            "rule_id",
            "category",
            "severity",
            "confidence",
            "fingerprint",
            "primary_location",
            "related_locations",
            "message",
            "explanation",
            "remediation",
            "tool_rule_id",
            "tool_name",
            "tool_version",
            "suppression",
            "metrics",
            "snippet",
        )
        for field in required_fields:
            with self.subTest(field=field):
                altered_finding = finding()
                del altered_finding[field]
                payload = report([engine(findings=[altered_finding])])
                self.assert_schema_error(payload)

        for field, value in (
            ("rule_id", "not-an-ici-rule"),
            ("severity", "urgent"),
            ("confidence", "certain"),
            ("fingerprint", "sha256:ABC"),
        ):
            with self.subTest(field=field):
                altered_finding = finding()
                altered_finding[field] = value
                self.assert_schema_error(report([engine(findings=[altered_finding])]))

    def test_finding_strings_suppression_metrics_and_related_locations_are_strict(
        self,
    ) -> None:
        for field in (
            "message",
            "explanation",
            "remediation",
            "tool_rule_id",
            "tool_name",
            "tool_version",
            "snippet",
        ):
            with self.subTest(field=field):
                altered_finding = finding()
                altered_finding[field] = None
                self.assert_schema_error(
                    report([engine(findings=[altered_finding])]), "invalid-field"
                )

        for field, value in (
            ("suppressed", 1),
            ("kind", "unknown"),
            ("reason", None),
        ):
            with self.subTest(suppression_field=field):
                altered_finding = finding()
                altered_finding["suppression"][field] = value
                expected = "report-schema" if field == "kind" else "invalid-field"
                self.assert_schema_error(
                    report([engine(findings=[altered_finding])]), expected
                )

        for metrics in (
            [],
            {"score": {"value": math.nan, "unit": "count"}},
            {"score": {"value": True, "unit": "count"}},
            {"score": {"value": 1, "unit": None}},
            {"score": 1},
            {1: {"value": 1, "unit": "count"}},
        ):
            with self.subTest(metrics=metrics):
                altered_finding = finding(metrics=metrics)  # type: ignore[arg-type]
                expected = (
                    "invalid-field"
                    if metrics
                    in (
                        {"score": {"value": 1, "unit": None}},
                        {1: {"value": 1, "unit": "count"}},
                    )
                    else "report-schema"
                )
                self.assert_schema_error(
                    report([engine(findings=[altered_finding])]), expected
                )

        for related in (None, {}, [location(path="../outside.py")], [1]):
            with self.subTest(related=related):
                altered_finding = finding()
                altered_finding["related_locations"] = related
                self.assert_schema_error(report([engine(findings=[altered_finding])]))

    def test_evaluate_contract_matches_rich_finding_predicates(self) -> None:
        observed_finding = finding(
            rule_id="ici.dead.private",
            category="maintainability",
            severity="medium",
            confidence="medium",
            location_value=location(
                "src/bad.py",
                line=7,
                start_column=3,
                end_column=12,
                label="private helper",
            ),
            related_locations=[location("src/caller.py", line=11, label="caller")],
            message="dead private helper",
            snippet="def _helper():",
            metrics={"score": {"value": 4.5, "unit": "count"}},
            suppressed=False,
        )
        observed_engine = engine(
            "dead",
            status="WARN",
            evidence="ESTIMATED",
            findings=[observed_finding],
            extra={"analysis_provenance": "python-ast-heuristic"},
        )
        expected = self.expectation(
            scenario_id="python.example",
            suite_status="WARN",
            producer_version="1.2.3",
            engines=[
                {
                    "name": "dead",
                    "status": "WARN",
                    "evidence": "ESTIMATED",
                    "required": True,
                    "extra": {"analysis_provenance": "python-ast-heuristic"},
                }
            ],
            findings=[
                {
                    "engine": "dead",
                    "engine_status": "WARN",
                    "evidence": "ESTIMATED",
                    "rule_id": "ici.dead.private",
                    "category": "maintainability",
                    "severity": "medium",
                    "confidence": "medium",
                    "location": {
                        "path": "src/bad.py",
                        "line_min": 5,
                        "line_max": 8,
                        "column_min": 2,
                        "column_max": 4,
                        "label": "private helper",
                    },
                    "message_contains": ["dead private"],
                    "snippet_contains": ["_helper"],
                    "suppressed": False,
                    "metrics": {"score": {"minimum": 4, "maximum": 5, "unit": "count"}},
                    "related_locations": [
                        {"path": "src/caller.py", "line": 11, "label": "caller"}
                    ],
                }
            ],
        )

        result = evaluate_contract(
            report([observed_engine], suite_status="WARN"), expected
        )

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.matched_findings, 1)
        self.assertEqual(result.errors, ())
        self.assertEqual(result.scenario_id, "python.example")

    def test_expected_findings_are_matched_to_unique_observations(self) -> None:
        specific = finding(
            index=0, rule_id="ici.specific", message="same message", snippet="same"
        )
        generic = finding(
            index=1, rule_id="ici.generic", message="same message", snippet="same"
        )
        payload = report([engine("example", findings=[specific, generic])])
        expected = self.expectation(
            scenario_id="unique",
            suite_status="PASS",
            findings=[
                {"message_contains": ["same message"]},
                {"rule_id": "ici.specific"},
            ],
        )

        result = evaluate_contract(payload, expected)

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.matched_findings, 2)

    def test_expected_count_cannot_reuse_one_finding(self) -> None:
        payload = report([engine("example", findings=[finding()])])
        expected = self.expectation(
            scenario_id="count",
            suite_status="PASS",
            findings=[{"rule_id": "ici.example.rule", "count": 2}],
        )

        result = evaluate_contract(payload, expected)

        self.assertEqual(result.verdict, "FAIL")
        self.assertEqual(result.matched_findings, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("expected finding", result.errors[0])

    def test_forbidden_findings_ignore_info_and_suppressed_results(self) -> None:
        forbidden = {"rule_id": "ici.forbidden"}
        info = finding(index=0, rule_id="ici.forbidden", severity="info")
        suppressed = finding(
            index=1,
            rule_id="ici.forbidden",
            severity="high",
            suppressed=True,
            suppression_kind="baseline",
            suppression_reason="accepted",
        )
        payload = report([engine(findings=[info, suppressed])])
        expected = self.expectation(
            scenario_id="absence",
            suite_status="PASS",
            forbidden_findings=[forbidden],
        )

        result = evaluate_contract(payload, expected)

        self.assertEqual(result.verdict, "PASS")
        self.assertEqual(result.errors, ())

        active = finding(index=2, rule_id="ici.forbidden", severity="high")
        failing = evaluate_contract(report([engine(findings=[active])]), expected)
        self.assertEqual(failing.verdict, "FAIL")
        self.assertIn("forbidden finding", failing.errors[0])

    def test_evaluate_contract_reports_status_version_engine_and_finding_mismatches(
        self,
    ) -> None:
        payload = report([engine("actual", findings=[finding()])])
        expected = self.expectation(
            scenario_id="mismatch",
            suite_status="FAIL",
            producer_version="9.9.9",
            engines=[
                {
                    "name": "actual",
                    "status": "FAIL",
                    "evidence": "NOT_RUN",
                    "required": False,
                    "extra": {"missing": True},
                },
                {"name": "absent"},
            ],
            findings=[{"rule_id": "ici.missing"}],
        )

        result = evaluate_contract(payload, expected)

        self.assertEqual(result.verdict, "FAIL")
        self.assertGreaterEqual(len(result.errors), 6)
        self.assertTrue(any("suite status" in error for error in result.errors))
        self.assertTrue(any("producer version" in error for error in result.errors))
        self.assertTrue(any("absent" in error for error in result.errors))

    def test_expected_finding_count_must_be_positive_integer(self) -> None:
        payload = report()
        for count in (0, -1, True, "1"):
            with self.subTest(count=count):
                expected = self.expectation(
                    scenario_id="bad-count",
                    suite_status="PASS",
                    findings=[{"count": count}],
                )
                with self.assertRaises(ContractError) as raised:
                    evaluate_contract(payload, expected)
                self.assertEqual(raised.exception.code, "invalid-field")

    def test_contract_failures_name_the_engine_that_owns_them(self) -> None:
        """The one-screen summary groups by engine, so attribution must be real."""

        payload = report(
            [engine("dead", status="PASS"), engine("sanitize", status="PASS")],
            suite_status="PASS",
        )
        expected = self.expectation(
            suite_status="FAIL",
            engines=[{"name": "sanitize", "status": "FAIL"}],
            findings=[{"engine": "dead", "rule_id": "ici.python.dead.unused"}],
            forbidden_findings=[],
        )

        result = evaluate_contract(payload, expected)

        self.assertEqual(result.verdict, "FAIL")
        attributed = {(item.engine, item.kind) for item in result.failures}
        self.assertIn(("dead", "finding-missing"), attributed)
        self.assertIn(("sanitize", "engine-status"), attributed)
        # A suite-level mismatch belongs to no single engine.
        self.assertIn(("", "suite-status"), attributed)
        # The message surface is derived from the same records, never separate.
        self.assertEqual(result.errors, tuple(item.detail for item in result.failures))

    def test_an_unattributable_finding_expectation_falls_back_to_suite_scope(self) -> None:
        payload = report([engine("dead", status="PASS")], suite_status="PASS")
        expected = self.expectation(findings=[{"rule_id": "ici.python.dead.unused"}])

        result = evaluate_contract(payload, expected)

        self.assertEqual(
            [(item.engine, item.kind) for item in result.failures],
            [("", "finding-missing")],
        )

    def test_a_forbidden_match_is_attributed_to_its_engine(self) -> None:
        offending = finding(rule_id="ici.python.dead.unused", severity="high")
        payload = report(
            [engine("dead", status="WARN", findings=[offending])], suite_status="WARN"
        )
        expected = self.expectation(
            suite_status="WARN",
            forbidden_findings=[{"engine": "dead", "rule_id": "ici.python.dead.unused"}],
        )

        result = evaluate_contract(payload, expected)

        self.assertEqual(
            [(item.engine, item.kind) for item in result.failures],
            [("dead", "finding-forbidden")],
        )


if __name__ == "__main__":
    unittest.main()
