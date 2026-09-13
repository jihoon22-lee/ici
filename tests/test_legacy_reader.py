"""The v3 boundary, in both directions (#200 PR C).

Two properties matter here and they pull in opposite directions:

- reading a v3 report must not invent the facts v3 never recorded, and
- the existing v3 readers must not read a *new* result as a quiet pass.

The second is #200's acceptance criterion and the reason
``test_publish_reader_refuses`` exists in this file rather than in
``tests/test_publish.py``: it is a statement about the format boundary, not
about publishing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.domain.enums import GateVerdict, ScopeKind
from ici.domain.serialization import run_result_to_dict
from ici.domain.workspace import SourceSnapshot
from ici.engines.publish import load_suite_from_json
from ici.execution.legacy_reader import (
    LegacyReadError,
    promote,
    read_legacy_document,
    read_legacy_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ici-next"
V3_SUITE = FIXTURES / "legacy-v3-suite.json"
SNAPSHOT = SourceSnapshot(digest="sha256:" + "09" * 32)


def _document() -> dict:
    return json.loads(V3_SUITE.read_text(encoding="utf-8"))


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "verify_report.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class TestReadingV3:
    def test_findings_keep_their_location_severity_and_provider(self):
        report = read_legacy_report(V3_SUITE)

        assert [(f.provider, f.rule_id) for f in report.findings] == [
            ("lint", "ici.python.lint.unused-import"),
            ("lint", "ici.python.lint.line-too-long"),
        ]
        first = report.findings[0]
        assert first.severity == "medium"
        assert first.confidence == "high"
        assert first.primary_location.path == "src/app/main.py"
        assert first.primary_location.start_line == 12

    def test_the_provider_is_the_engine_not_the_executable(self):
        """Both findings come from ruff; only the engine identifies who ran it."""

        report = read_legacy_report(V3_SUITE)

        assert {f.provider for f in report.findings} == {"lint"}
        assert {f.rule_version for f in report.findings} == {"0.6.9"}

    def test_suppression_survives_and_is_marked_as_coming_from_v3(self):
        report = read_legacy_report(V3_SUITE)

        suppressed = report.findings[1].suppression
        assert suppressed.suppressed is True
        assert suppressed.kind == "inline"
        assert suppressed.origin == "legacy-v3"

    def test_fail_stays_fail(self):
        report = read_legacy_report(V3_SUITE)

        assert report.gate.selected is GateVerdict.FAIL
        assert report.gate.has_violations is True
        assert report.gate.reasons

    def test_error_becomes_incomplete_not_fail(self):
        """An engine that could not run is not a verdict about the code."""

        document = _document()
        document["suite_status"] = "ERROR"
        document["results"][0]["status"] = "ERROR"

        report = read_legacy_document(document)

        assert report.gate.selected is GateVerdict.INCOMPLETE
        assert report.gate.has_violations is False
        assert report.execution.required_complete is False

    def test_warn_becomes_pass_and_says_so(self):
        """The one lossy status. The findings survive; the distinction does not."""

        document = _document()
        document["suite_status"] = "WARN"

        report = read_legacy_document(document)

        assert report.gate.selected is GateVerdict.PASS
        assert report.gate.has_violations is False
        assert len(report.findings) == 2
        assert any("WARN became PASS" in item for item in report.limitations)

    def test_an_unknown_suite_status_is_refused_rather_than_guessed(self):
        document = _document()
        document["suite_status"] = "BLOCKED"

        with pytest.raises(LegacyReadError, match="unknown v3 suite_status"):
            read_legacy_document(document)

    def test_only_the_suite_score_is_carried(self):
        """Per-engine v3 scores have no unit, so they are not made Measurements."""

        report = read_legacy_report(V3_SUITE)

        assert [(m.name, m.value) for m in report.metrics] == [("tem_score", 4.12)]


class TestRefusingRatherThanInventing:
    def test_a_missing_producer_version_is_refused(self):
        document = _document()
        del document["analysis_metadata"]["producer_version"]

        with pytest.raises(LegacyReadError, match="producer_version"):
            read_legacy_document(document)

    def test_a_report_with_no_digests_anywhere_is_refused(self):
        """A placeholder digest would make a later baseline comparison lie."""

        document = _document()
        del document["analysis_metadata"]["policy_digest"]
        del document["analysis_context"]["identity"]["config_digest"]

        with pytest.raises(LegacyReadError, match="policy digest"):
            read_legacy_document(document)

    def test_a_digest_in_either_place_is_enough(self):
        document = _document()
        del document["analysis_metadata"]["policy_digest"]

        report = read_legacy_document(document)

        assert report.policy_digest == "sha256:" + "c3" * 32

    def test_an_unavailable_commit_becomes_none_with_a_limitation(self):
        document = _document()
        document["analysis_context"]["identity"]["source_commit"] = "unavailable"

        report = read_legacy_document(document)

        assert report.commit is None
        assert any("source commit is unknown" in item for item in report.limitations)

    def test_the_missing_source_snapshot_is_always_named(self):
        """The structural gap: v3 never digested the content it read."""

        report = read_legacy_report(V3_SUITE)

        assert any("no source snapshot" in item for item in report.limitations)

    def test_a_next_result_is_not_readable_as_v3(self):
        with pytest.raises(LegacyReadError, match="schema_version"):
            read_legacy_document(json.loads((FIXTURES / "run-success.json").read_text()))

    def test_a_wrong_shaped_document_does_not_escape_as_a_bare_valueerror(self):
        document = _document()
        document["results"][0]["findings"][0]["severity"] = "SEVERE"

        with pytest.raises(LegacyReadError, match="does not fit the domain model"):
            read_legacy_document(document)


class TestPromotion:
    def test_promotion_needs_a_snapshot_the_caller_supplies(self):
        report = read_legacy_report(V3_SUITE)

        result = promote(report, run_id="imported-1", source=SNAPSHOT)

        assert result.identity.source.digest == SNAPSHOT.digest
        assert result.identity.policy_digest == report.policy_digest
        assert result.identity.toolchain_digest == report.toolchain_digest

    def test_an_imported_run_is_standalone_and_never_a_workspace_pass(self):
        """R05: a single v3 run must not stand in for a workspace verdict."""

        result = promote(read_legacy_report(V3_SUITE), run_id="imported-1", source=SNAPSHOT)

        assert result.scope.kind is ScopeKind.STANDALONE
        assert result.scope.full_required_satisfied is False
        assert result.gate.workspace is GateVerdict.NOT_EVALUATED

    def test_the_reports_commit_fills_a_snapshot_that_has_none(self):
        result = promote(read_legacy_report(V3_SUITE), run_id="imported-1", source=SNAPSHOT)

        assert result.identity.source.commit == "c3" * 20

    def test_a_measured_commit_wins_over_the_reports(self):
        """The snapshot was measured now; the report's commit is what an older
        build recorded, and the two disagreeing means the tree moved."""

        measured = SourceSnapshot(digest=SNAPSHOT.digest, commit="ab" * 20)

        result = promote(read_legacy_report(V3_SUITE), run_id="imported-1", source=measured)

        assert result.identity.source.commit == "ab" * 20

    def test_the_limitations_reach_the_run_result(self):
        result = promote(read_legacy_report(V3_SUITE), run_id="imported-1", source=SNAPSHOT)

        assert result.limitations == read_legacy_report(V3_SUITE).limitations
        assert result.limitations

    def test_a_promoted_result_serializes_as_a_normal_run(self):
        result = promote(read_legacy_report(V3_SUITE), run_id="imported-1", source=SNAPSHOT)

        payload = run_result_to_dict(result)

        assert payload["schema_id"] == "ici.next.run"
        assert payload["scope"]["kind"] == "standalone"
        assert len(payload["findings"]) == 2


class TestPublishReaderRefusesRatherThanDegrading:
    """#200: a legacy reader must not turn a result it cannot read into a pass.

    ``load_suite_from_json`` feeds the sticky PR comment. Every case below used
    to produce a summary — WARN with no engines — from a document that either
    said something worse or said nothing this build could read.
    """

    def test_a_newer_producers_fail_report_is_refused_not_downgraded_to_warn(self, tmp_path):
        path = _write(
            tmp_path,
            {
                "schema_version": "ici.result/v4",
                "suite_status": "BLOCKED",
                "results": [
                    {"engine_name": "lint", "status": "BLOCKED", "summary": "12 violations"}
                ],
                "tem_score": 1.2,
            },
        )

        assert load_suite_from_json(path) is None

    def test_a_document_with_no_schema_version_is_refused(self, tmp_path):
        path = _write(
            tmp_path,
            {
                "suite_status": "FAIL",
                "results": [{"engine_name": "lint", "status": "FAIL", "summary": "x"}],
            },
        )

        assert load_suite_from_json(path) is None

    def test_an_unreadable_suite_status_is_refused_not_called_warn(self, tmp_path):
        path = _write(
            tmp_path,
            {
                "schema_version": "ici.result/v3",
                "suite_status": "ZZZ",
                "results": [],
            },
        )

        assert load_suite_from_json(path) is None

    def test_one_unparseable_engine_refuses_the_whole_document(self, tmp_path):
        """Dropping it silently is how a report loses every engine and still
        gets summarised."""

        path = _write(
            tmp_path,
            {
                "schema_version": "ici.result/v3",
                "suite_status": "FAIL",
                "results": [
                    {"engine_name": "lint", "status": "FAIL", "summary": "real"},
                    {"engine_name": "test", "status": "not-a-status", "summary": "lost"},
                ],
            },
        )

        assert load_suite_from_json(path) is None

    def test_a_next_result_is_refused(self, tmp_path):
        assert load_suite_from_json(FIXTURES / "run-code-fail.json") is None

    @pytest.mark.parametrize("version", ["ici.result/v2", "ici.result/v3"])
    def test_the_formats_this_build_does_read_still_read(self, tmp_path, version):
        path = _write(
            tmp_path,
            {
                "schema_version": version,
                "suite_status": "FAIL",
                "duration": 1.0,
                "tem_score": 4.5,
                "max_tem_score": 5.0,
                "results": [
                    {
                        "engine_name": "lint",
                        "status": "FAIL",
                        "summary": "12 violations",
                        "duration": 0.5,
                    }
                ],
            },
        )

        suite = load_suite_from_json(path)

        assert suite is not None
        assert suite.suite_status.value == "FAIL"
        assert len(suite.results) == 1
