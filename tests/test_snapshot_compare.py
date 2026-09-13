"""Four axes that never answer for one another (#201 step 5).

The case this module exists for: a finding vanishes while every engine runs
normally, because its file left the analysed scope. Against the existing v3
baseline comparison that is indistinguishable from a fix — #234 handled the
engine-level half (ERROR/SKIP/absent), and this is the file-level half it left.
"""

from __future__ import annotations

import pytest

from ici.domain.enums import EvidenceLevel, GateVerdict, ScopeKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.result import (
    ExecutionSummary,
    GateOutcome,
    Producer,
    RunIdentity,
    RunResult,
    ScopeSelection,
)
from ici.domain.workspace import SourceSnapshot
from ici.execution.snapshot import compare

DIGEST = "sha256:" + "ab" * 32
OTHER_DIGEST = "sha256:" + "cd" * 32


def _finding(fingerprint: str, path: str, line: int = 10) -> Finding:
    return Finding(
        fingerprint=fingerprint,
        rule_id="ici.test.rule",
        message="something",
        severity="medium",
        confidence="high",
        primary_location=SourceSpan(path=path, start_line=line),
        provider="lint",
    )


def _result(
    *,
    run_id: str = "run",
    files: tuple[str, ...] = (),
    findings: tuple[Finding, ...] = (),
    metrics: tuple[Measurement, ...] = (),
    policy: str = DIGEST,
    toolchain: str = DIGEST,
) -> RunResult:
    return RunResult(
        run_id=run_id,
        producer=Producer(ici_version="0.11.0"),
        identity=RunIdentity(
            source=SourceSnapshot(digest=DIGEST, files=files),
            policy_digest=policy,
            toolchain_digest=toolchain,
        ),
        scope=ScopeSelection(kind=ScopeKind.FULL, full_required_satisfied=True),
        execution=ExecutionSummary(required_complete=True),
        gate=GateOutcome(selected=GateVerdict.PASS),
        findings=findings,
        metrics=metrics,
    )


class TestAFindingThatLeftScopeIsNotResolved:
    """The measurement that motivated the module."""

    def test_it_is_withheld_rather_than_resolved(self):
        before = _result(files=("a.py", "b.py"), findings=(_finding("f1", "b.py"),))
        after = _result(files=("a.py",))

        diff = compare(before, after)

        assert diff.findings.resolved == ()
        assert diff.source_scope.withheld == ("f1",)

    def test_the_comparison_reports_itself_as_partial(self):
        before = _result(files=("a.py", "b.py"), findings=(_finding("f1", "b.py"),))
        after = _result(files=("a.py",))

        diff = compare(before, after)

        assert diff.comparable is False
        assert any("withheld rather than resolved" in note for note in diff.notes)

    def test_the_file_leaving_scope_is_reported_on_its_own_axis(self):
        before = _result(files=("a.py", "b.py"), findings=(_finding("f1", "b.py"),))
        after = _result(files=("a.py",))

        diff = compare(before, after)

        assert diff.source_scope.left == ("b.py",)
        assert diff.source_scope.entered == ()

    def test_a_real_fix_in_a_file_still_in_scope_is_resolved(self):
        """The control. Without it the rest could pass by never resolving anything."""

        before = _result(files=("a.py",), findings=(_finding("f1", "a.py"),))
        after = _result(files=("a.py",))

        diff = compare(before, after)

        assert diff.findings.resolved == ("f1",)
        assert diff.source_scope.withheld == ()
        assert diff.comparable is True

    def test_one_withheld_finding_does_not_withhold_the_others(self):
        """The split is per finding, by the file it lives in."""

        before = _result(
            files=("a.py", "b.py"),
            findings=(_finding("kept", "a.py"), _finding("gone", "b.py")),
        )
        after = _result(files=("a.py",))

        diff = compare(before, after)

        assert diff.findings.resolved == ("kept",)
        assert diff.source_scope.withheld == ("gone",)

    def test_a_run_that_records_no_scope_resolves_nothing(self):
        """A result that does not say what it read cannot show it re-examined
        anything."""

        before = _result(files=("a.py",), findings=(_finding("f1", "a.py"),))
        after = _result(files=())

        diff = compare(before, after)

        assert diff.findings.resolved == ()
        assert diff.source_scope.withheld == ("f1",)
        assert any("records no source scope" in note for note in diff.notes)


class TestTheOrdinaryFindingAxis:
    def test_a_new_finding_is_added(self):
        before = _result(files=("a.py",))
        after = _result(files=("a.py",), findings=(_finding("f1", "a.py"),))

        assert compare(before, after).findings.added == ("f1",)

    def test_a_surviving_finding_is_unchanged(self):
        finding = _finding("f1", "a.py")
        diff = compare(
            _result(files=("a.py",), findings=(finding,)),
            _result(files=("a.py",), findings=(finding,)),
        )

        assert diff.findings.unchanged == ("f1",)
        assert diff.findings.changed is False

    def test_a_finding_in_a_newly_analysed_file_is_added_not_a_regression_of_nothing(self):
        before = _result(files=("a.py",))
        after = _result(files=("a.py", "new.py"), findings=(_finding("f1", "new.py"),))

        diff = compare(before, after)

        assert diff.findings.added == ("f1",)
        assert diff.source_scope.entered == ("new.py",)


class TestMetricsAndEvidenceAreSeparateAxes:
    def test_a_moved_value_is_reported_with_both_ends(self):
        before = _result(metrics=(Measurement(name="line_coverage", value=80.0, unit="%"),))
        after = _result(metrics=(Measurement(name="line_coverage", value=72.0, unit="%"),))

        assert compare(before, after).metrics.changed_values == {"line_coverage": (80.0, 72.0)}

    def test_evidence_weakening_is_caught_when_the_value_did_not_move(self):
        """The reason evidence is its own axis.

        An ESTIMATED 80% is not a MEASURED 80%. A comparison watching only the
        number would report nothing here.
        """

        before = _result(
            metrics=(
                Measurement(
                    name="line_coverage", value=80.0, unit="%", evidence=EvidenceLevel.MEASURED
                ),
            )
        )
        after = _result(
            metrics=(
                Measurement(
                    name="line_coverage", value=80.0, unit="%", evidence=EvidenceLevel.ESTIMATED
                ),
            )
        )

        diff = compare(before, after)

        assert diff.metrics.changed_values == {}
        assert diff.evidence.weakened == {"line_coverage": ("MEASURED", "ESTIMATED")}
        assert diff.changed is True

    def test_evidence_strengthening_is_reported_separately_from_weakening(self):
        before = _result(
            metrics=(Measurement(name="branch", value=70.0, evidence=EvidenceLevel.ESTIMATED),)
        )
        after = _result(
            metrics=(Measurement(name="branch", value=70.0, evidence=EvidenceLevel.MEASURED),)
        )

        diff = compare(before, after)

        assert diff.evidence.strengthened == {"branch": ("ESTIMATED", "MEASURED")}
        assert diff.evidence.weakened == {}

    @pytest.mark.parametrize("level", [EvidenceLevel.NOT_RUN, EvidenceLevel.NOT_APPLICABLE])
    def test_dropping_to_a_non_measuring_level_counts_as_weakening(self, level):
        before = _result(
            metrics=(Measurement(name="branch", value=70.0, evidence=EvidenceLevel.MEASURED),)
        )
        after = _result(metrics=(Measurement(name="branch", value=70.0, evidence=level),))

        assert "branch" in compare(before, after).evidence.weakened

    def test_a_metric_that_disappeared_is_not_a_value_of_zero(self):
        before = _result(metrics=(Measurement(name="branch", value=70.0),))
        after = _result()

        diff = compare(before, after)

        assert diff.metrics.removed == ("branch",)
        assert diff.metrics.changed_values == {}


class TestTheAxesStayApart:
    def test_a_scope_change_alone_does_not_touch_the_finding_axis(self):
        before = _result(files=("a.py",))
        after = _result(files=("a.py", "b.py"))

        diff = compare(before, after)

        assert diff.source_scope.changed is True
        assert diff.findings.changed is False

    def test_a_finding_change_alone_does_not_touch_the_scope_axis(self):
        before = _result(files=("a.py",))
        after = _result(files=("a.py",), findings=(_finding("f1", "a.py"),))

        diff = compare(before, after)

        assert diff.findings.changed is True
        assert diff.source_scope.changed is False

    def test_two_identical_runs_report_no_change_at_all(self):
        finding = _finding("f1", "a.py")
        metric = Measurement(name="line_coverage", value=80.0, unit="%")
        run = _result(files=("a.py",), findings=(finding,), metrics=(metric,))

        diff = compare(run, run)

        assert diff.changed is False
        assert diff.comparable is True
        assert diff.notes == ()


class TestComparabilityWarnings:
    def test_a_different_policy_is_named(self):
        diff = compare(_result(policy=DIGEST), _result(policy=OTHER_DIGEST))

        assert any("analysis policy differs" in note for note in diff.notes)

    def test_a_different_toolchain_is_named(self):
        diff = compare(_result(toolchain=DIGEST), _result(toolchain=OTHER_DIGEST))

        assert any("toolchain differs" in note for note in diff.notes)

    def test_identical_runs_carry_no_warnings(self):
        run = _result(files=("a.py",))

        assert compare(run, run).notes == ()

    def test_a_scopeless_comparison_with_nothing_at_stake_is_not_warned_about(self):
        """A note about a limitation with no consequence trains readers to skip
        the notes."""

        assert compare(_result(), _result()).notes == ()
