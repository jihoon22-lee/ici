"""The ici.next.run / ici.next.event wire contract (#200 PR B).

Three questions, and the tests are grouped by them:

1. Does a result survive a round trip with its meaning intact? #200's
   acceptance criterion names finding, location, confidence and scope.
2. Is a bad payload refused *with a reason*? SPEC-04 section 5 forbids turning
   an unrecognised result into a silent empty PASS, and the six checked-in
   fixtures exist so that the interesting shapes are exercised rather than
   described.
3. Is the output deterministic? A golden byte comparison catches a formatting
   change that would otherwise show up as an unreadable diff months later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ici.domain import (
    EventType,
    EvidenceLevel,
    ExecutionSummary,
    GateOutcome,
    GateVerdict,
    Producer,
    RunEvent,
    RunIdentity,
    RunResult,
    ScopeKind,
    ScopeSelection,
    SourceSnapshot,
)
from ici.domain._codec import SchemaError, UnsupportedSchemaError, dumps
from ici.domain.eventstream import (
    event_from_dict,
    event_to_dict,
    events_from_jsonl,
    events_to_jsonl,
)
from ici.domain.serialization import loads, run_result_from_dict, run_result_to_dict

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ici-next"

# Every result fixture #200 step 4 asks for. Named rather than globbed so that
# deleting one fails a test instead of shrinking the checked set.
RESULT_FIXTURES = (
    "run-success",
    "run-code-fail",
    "run-required-incomplete",
    "run-partial-selection",
    "run-cancelled",
)

DIGEST = "sha256:" + "ab" * 32


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def minimal_result(**overrides: object) -> RunResult:
    base: dict[str, object] = {
        "run_id": "run-1",
        "producer": Producer(ici_version="0.11.0"),
        "identity": RunIdentity(
            source=SourceSnapshot(digest=DIGEST),
            policy_digest=DIGEST,
            toolchain_digest=DIGEST,
        ),
        "scope": ScopeSelection(kind=ScopeKind.FULL, full_required_satisfied=True),
        "execution": ExecutionSummary(required_complete=True),
        "gate": GateOutcome(selected=GateVerdict.PASS),
    }
    base.update(overrides)
    return RunResult(**base)  # type: ignore[arg-type]


class TestFixturesAreComplete:
    """The fixtures are the contract's worked examples, not illustrations."""

    @pytest.mark.parametrize("name", RESULT_FIXTURES)
    def test_every_fixture_loads(self, name: str):
        result = run_result_from_dict(load_fixture(name))
        assert result.schema_id == "ici.next.run"
        assert result.schema_version == 1

    @pytest.mark.parametrize("name", RESULT_FIXTURES)
    def test_round_trip_is_stable(self, name: str):
        payload = load_fixture(name)
        once = run_result_to_dict(run_result_from_dict(payload))
        twice = run_result_to_dict(run_result_from_dict(once))
        assert once == twice == payload

    @pytest.mark.parametrize("name", RESULT_FIXTURES)
    def test_serialized_bytes_match_the_checked_in_file(self, name: str):
        """Golden comparison: a formatting change has to be deliberate."""

        on_disk = (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
        regenerated = dumps(run_result_to_dict(run_result_from_dict(load_fixture(name)))) + "\n"
        assert regenerated == on_disk

    def test_the_fixtures_cover_the_shapes_that_differ(self):
        """Guards against five fixtures that all say the same thing."""

        gates = {
            name: run_result_from_dict(load_fixture(name)).gate.selected for name in RESULT_FIXTURES
        }
        assert gates["run-success"] is GateVerdict.PASS
        assert gates["run-code-fail"] is GateVerdict.FAIL
        assert gates["run-required-incomplete"] is GateVerdict.INCOMPLETE
        assert gates["run-cancelled"] is GateVerdict.INCOMPLETE

        partial = run_result_from_dict(load_fixture("run-partial-selection"))
        assert partial.scope.kind is ScopeKind.PARTIAL
        assert partial.scope.omitted
        assert partial.gate.workspace is GateVerdict.NOT_EVALUATED

        cancelled = run_result_from_dict(load_fixture("run-cancelled"))
        assert cancelled.execution.cancelled


class TestMeaningSurvivesTheRoundTrip:
    def test_finding_location_confidence_and_scope_are_preserved(self):
        """#200 acceptance criterion, checked field by field."""

        original = run_result_from_dict(load_fixture("run-code-fail"))
        restored = run_result_from_dict(run_result_to_dict(original))

        assert restored.findings == original.findings
        finding = restored.findings[0]
        assert finding.primary_location.start_line == 12
        assert finding.primary_location.start_column == 1
        assert finding.primary_location.label == "demo.main"
        assert finding.confidence == "exact"
        assert finding.provider == "ruff"
        assert finding.native_rule_id == "F401"
        assert finding.component_id == "tool-a"
        assert finding.analysis_unit_id == "tool-a-python"
        assert finding.task_id == "analyze-tool-a"

    def test_raw_counts_survive_so_a_consumer_can_combine_them(self):
        """SPEC-04 forbids averaging percentages; the counts must travel."""

        result = run_result_from_dict(load_fixture("run-success"))
        metric = result.metrics[0]
        assert (metric.numerator, metric.denominator) == (446, 500)

    def test_estimated_evidence_is_not_flattened_to_measured(self):
        result = run_result_from_dict(load_fixture("run-partial-selection"))
        levels = {item.name: item.evidence for item in result.metrics}
        assert levels["cpp-cognitive"] is EvidenceLevel.ESTIMATED
        assert levels["line-coverage"] is EvidenceLevel.MEASURED

    def test_incomplete_keeps_its_findings_and_its_reasons(self):
        """The combination #200 names: could not finish, and found something."""

        result = run_result_from_dict(load_fixture("run-required-incomplete"))
        assert result.gate.selected is GateVerdict.INCOMPLETE
        assert result.gate.has_violations
        assert result.blocking_findings
        assert len(result.gate.reasons) == 2
        assert result.gate.exit_code == 3


class TestBadPayloadsAreRefusedWithAReason:
    def test_a_legacy_v3_report_is_refused_by_name(self):
        """The most likely mistake: handing over the wrong file.

        The message has to name what was found, or the caller cannot tell this
        from a corrupt document.
        """

        legacy = load_fixture("legacy-v3-result")
        with pytest.raises(SchemaError) as excinfo:
            run_result_from_dict(legacy)
        assert "ici.next.run" in str(excinfo.value)
        assert "None" in str(excinfo.value) or "found" in str(excinfo.value)

    def test_a_newer_major_version_is_a_distinct_error(self):
        """A newer producer is not the same problem as a broken file."""

        payload = load_fixture("run-success")
        payload["schema_version"] = 2
        with pytest.raises(UnsupportedSchemaError) as excinfo:
            run_result_from_dict(payload)
        assert "version 2" in str(excinfo.value)
        assert "implements version 1" in str(excinfo.value)

    def test_unsupported_version_is_still_a_schema_error(self):
        """Callers that only catch SchemaError must not miss it."""

        payload = load_fixture("run-success")
        payload["schema_version"] = 99
        with pytest.raises(SchemaError):
            run_result_from_dict(payload)

    def test_an_abbreviated_digest_is_refused(self):
        """SPEC-04's envelope prints `sha256:...`; that is not a value."""

        payload = load_fixture("run-success")
        payload["identity"]["policy_digest"] = "sha256:..."
        with pytest.raises(SchemaError, match="full sha256"):
            run_result_from_dict(payload)

    def test_an_unknown_enum_names_the_allowed_values(self):
        payload = load_fixture("run-success")
        payload["gate"]["selected"] = "MOSTLY_FINE"
        with pytest.raises(SchemaError) as excinfo:
            run_result_from_dict(payload)
        assert "INCOMPLETE" in str(excinfo.value)
        assert "MOSTLY_FINE" in str(excinfo.value)

    def test_a_payload_that_parses_but_lies_is_still_refused(self):
        """Model invariants run on read, not only on construction."""

        payload = load_fixture("run-success")
        payload["gate"]["has_violations"] = True
        with pytest.raises(SchemaError, match="not valid"):
            run_result_from_dict(payload)

    def test_a_traversing_path_is_refused(self):
        payload = load_fixture("run-code-fail")
        payload["findings"][0]["primary_location"]["path"] = "../../etc/passwd"
        with pytest.raises(SchemaError, match="relative and contained"):
            run_result_from_dict(payload)

    def test_malformed_json_says_so(self):
        with pytest.raises(SchemaError, match="not valid JSON"):
            loads("{not json")

    def test_a_scalar_is_not_a_result(self):
        with pytest.raises(SchemaError, match="must be a JSON object"):
            run_result_from_dict([1, 2, 3])


class TestDeterminism:
    def test_key_order_does_not_change_the_output(self):
        result = minimal_result()
        payload = run_result_to_dict(result)
        shuffled = dict(reversed(list(payload.items())))
        assert dumps(payload) == dumps(shuffled)

    def test_nan_is_refused_rather_than_written(self):
        """Python would emit bare NaN, which no other language can read back."""

        with pytest.raises(ValueError):
            dumps({"value": float("nan")})

    def test_identity_excludes_the_clock(self):
        """Two runs over the same inputs must produce the same identity.

        Nothing in the envelope's identity block comes from a clock, so a
        result serialized today and one serialized tomorrow compare equal.
        """

        payload = run_result_to_dict(minimal_result())
        assert set(payload["identity"]) == {"source", "policy_digest", "toolchain_digest"}
        assert dumps(payload) == dumps(run_result_to_dict(minimal_result()))


class TestEventStream:
    def make_events(self) -> tuple[RunEvent, ...]:
        return (
            RunEvent(
                run_id="run-1",
                seq=0,
                event_type=EventType.RUN_STARTED,
                timestamp="2026-09-13T00:00:00Z",
            ),
            RunEvent(
                run_id="run-1",
                seq=1,
                event_type=EventType.TASK_STARTED,
                timestamp="2026-09-13T00:00:01Z",
                task_id="analyze-tool-a",
            ),
            RunEvent(
                run_id="run-1",
                seq=2,
                event_type=EventType.RUN_COMPLETED,
                timestamp="2026-09-13T00:00:09Z",
            ),
        )

    def test_round_trip_through_jsonl(self):
        events = self.make_events()
        parsed, skipped = events_from_jsonl(events_to_jsonl(events))
        assert parsed == events
        assert skipped == ()

    def test_one_object_per_line(self):
        text = events_to_jsonl(self.make_events())
        lines = text.splitlines()
        assert len(lines) == 3
        for line in lines:
            assert json.loads(line)["schema_id"] == "ici.next.event"

    def test_a_task_event_must_name_its_task(self):
        """Otherwise a reader grouping by task silently drops it."""

        with pytest.raises(ValueError, match="must name the task"):
            RunEvent(
                run_id="run-1",
                seq=1,
                event_type=EventType.TASK_COMPLETED,
                timestamp="2026-09-13T00:00:01Z",
            )

    def test_a_truncated_final_line_keeps_the_events_before_it(self):
        """A writer killed mid-flush leaves one. That is expected, not fatal."""

        text = events_to_jsonl(self.make_events())
        truncated = text[: -len(text.splitlines()[-1]) - 1] + '{"schema_id":"ici.next'
        parsed, skipped = events_from_jsonl(truncated)
        assert len(parsed) == 2
        assert len(skipped) == 1
        assert "truncated" in skipped[0]

    def test_an_unknown_event_type_is_skipped_not_fatal(self):
        """SPEC-04 section 6: a consumer tolerates an unknown optional event."""

        text = events_to_jsonl(self.make_events())
        extra = dumps(
            {
                "schema_id": "ici.next.event",
                "schema_version": 1,
                "run_id": "run-1",
                "seq": 3,
                "event_type": "task.telepathy",
                "timestamp": "2026-09-13T00:00:10Z",
            }
        )
        parsed, skipped = events_from_jsonl(text + extra + "\n")
        assert len(parsed) == 3
        assert any("unknown event_type" in note for note in skipped)

    def test_an_unsupported_major_version_is_reported(self):
        """Tolerating unknown kinds does not mean tolerating a newer schema."""

        with pytest.raises(UnsupportedSchemaError):
            event_from_dict(
                {
                    "schema_id": "ici.next.event",
                    "schema_version": 2,
                    "run_id": "run-1",
                    "seq": 0,
                    "event_type": "run.started",
                    "timestamp": "2026-09-13T00:00:00Z",
                }
            )

    def test_a_seq_gap_is_reported_never_repaired(self):
        events = self.make_events()
        text = events_to_jsonl((events[0], events[2]))
        parsed, skipped = events_from_jsonl(text)
        assert len(parsed) == 2
        assert any("seq jumped from 0 to 2" in note for note in skipped)

    def test_a_duplicate_seq_is_reported(self):
        first = self.make_events()[0]
        text = events_to_jsonl((first, first))
        _, skipped = events_from_jsonl(text)
        assert any("duplicate seq 0" in note for note in skipped)

    def test_an_oversized_message_is_refused_at_construction(self):
        """Bound output where it is produced, not in the event stream."""

        with pytest.raises(ValueError, match="exceeds"):
            RunEvent(
                run_id="run-1",
                seq=0,
                event_type=EventType.DIAGNOSTIC,
                timestamp="2026-09-13T00:00:00Z",
                message="x" * 5000,
            )

    def test_events_carry_no_verdict(self):
        """The result is authoritative; the stream must not look like a second
        source of truth for pass or fail."""

        payload = event_to_dict(self.make_events()[2])
        assert "gate" not in payload
        assert "findings" not in payload
        assert "has_violations" not in payload
