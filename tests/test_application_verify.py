"""Running a plan and judging it, with the order of the questions pinned.

#206 item 4 points at #196's gate rules, and they are three questions asked in
one order. Most of what is below is about what happens when more than one is
true at once, because that is where asking them the other way round stops being
visible in the easy cases and starts reporting a run as something it was not.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.adapters.providers.ruff import RUFF_CONTRACT
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.application.verify import verify
from ici.domain.enums import EvidenceLevel, GateVerdict, TaskKind, TaskState
from ici.domain.finding import Finding, FindingSuppression, SourceSpan
from ici.domain.observation import Observation
from ici.domain.tasks import TaskSpec
from ici.execution.process import Outcome, TaskOutcome
from ici.execution.process import TaskSpec as ExecutionTaskSpec
from ici.languages.python.checks import CheckDefinition

LINE = CheckDefinition(id="python.line", title="Lines", language="python", tool=None)
LINT = CheckDefinition(id="python.lint", title="Ruff", language="python", tool="ruff")
ADVISORY = CheckDefinition(
    id="python.advice", title="Advice", language="python", tool="ruff", required=False
)


def _task(check_id: str = "python.lint") -> ProviderPlan:
    return ProviderPlan(
        task=TaskSpec(
            id=check_id,
            kind=TaskKind.ANALYZE,
            provider="ruff",
            argv=("ruff", "check", "src"),
            cwd="/project",
        ),
        contract=RUFF_CONTRACT,
    )


def _finding(rule: str = "ruff.F401") -> Finding:
    return Finding(
        fingerprint=f"sha256:{rule}",
        rule_id=rule,
        message="unused import",
        severity="warning",
        confidence="high",
        provider="ruff",
        primary_location=SourceSpan(path="src/app.py", start_line=1, start_column=1),
        evidence=EvidenceLevel.MEASURED,
    )


class _Provider:
    name = "ruff"

    def __init__(self, parsed: ParsedOutput | None = None) -> None:
        self._parsed = parsed if parsed is not None else ParsedOutput()

    def plan(self, request: object) -> ProviderPlan:  # pragma: no cover - not used here
        return _task()

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        return self._parsed


def _outcome(exit_code: int = 0, **kwargs: object) -> TaskOutcome:
    return TaskOutcome(
        spec=ExecutionTaskSpec(argv=("ruff",), name="python.lint"),
        outcome=kwargs.pop("outcome", Outcome.FINISHED),  # type: ignore[arg-type]
        exit_code=exit_code,
        **kwargs,  # type: ignore[arg-type]
    )


def _clean_line() -> Observation:
    return Observation(task_id="python.line", provider="ici.line", state=TaskState.SUCCEEDED)


# --- the three questions, one at a time -----------------------------------


def test_everything_completed_and_nothing_found_is_a_pass() -> None:
    plan = Plan(checks=(PlannedCheck(check=LINE),))

    result = verify(plan, providers={}, analyses={"python.line": _clean_line})

    assert result.gate.selected is GateVerdict.PASS
    assert result.exit_code == 0
    assert not result.gate.has_violations


def test_violations_in_a_completed_run_fail() -> None:
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))
    provider = _Provider(ParsedOutput(findings=(_finding(),)))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(1))

    assert result.gate.selected is GateVerdict.FAIL
    assert result.exit_code == 1
    assert result.gate.has_violations


def test_a_missing_required_tool_is_incomplete_not_a_pass() -> None:
    # #206's acceptance line: 필수 Ruff 누락은 INCOMPLETE/exit 3.
    plan = Plan(checks=(PlannedCheck(check=LINT, blocked="ruff was not found"),))

    result = verify(plan, providers={}, analyses={})

    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.exit_code == 3
    assert any("ruff was not found" in reason for reason in result.gate.reasons)


# --- where the order of the questions shows -------------------------------


def test_an_unfinished_run_that_found_nothing_is_not_a_pass() -> None:
    # Asking "were there violations?" first reports this as clean, on the
    # strength of having not looked.
    plan = Plan(checks=(PlannedCheck(check=LINE), PlannedCheck(check=LINT, blocked="no ruff")))

    result = verify(plan, providers={}, analyses={"python.line": _clean_line})

    assert result.gate.selected is not GateVerdict.PASS
    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.exit_code == 3


def test_an_unfinished_run_that_found_something_keeps_both_facts() -> None:
    # 미완료가 있다는 이유로 확인한 문제를 버리지 않는다. INCOMPLETE outranks
    # FAIL in the exit code, and has_violations still carries the rest.
    plan = Plan(
        checks=(
            PlannedCheck(check=LINT, task=_task()),
            PlannedCheck(
                check=CheckDefinition(
                    id="python.other", title="Other", language="python", tool="ruff"
                ),
                blocked="the other tool is missing",
            ),
        )
    )
    provider = _Provider(ParsedOutput(findings=(_finding(),)))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(1))

    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.exit_code == 3, "an incomplete run reported a verdict it never reached"
    assert result.gate.has_violations, "a real finding was thrown away"
    assert len(result.findings) == 1


def test_an_advisory_check_that_could_not_run_does_not_block_the_gate() -> None:
    # 참고 check의 오류도 보여주되 required policy에 없는 기능으로 전체 검증을
    # 무조건 막지 않는다.
    plan = Plan(
        checks=(PlannedCheck(check=LINE), PlannedCheck(check=ADVISORY, blocked="not installed"))
    )

    result = verify(plan, providers={}, analyses={"python.line": _clean_line})

    assert result.gate.selected is GateVerdict.PASS
    assert result.exit_code == 0
    blocked = [o for o in result.observations if o.task_id == "python.advice"]
    assert blocked and blocked[0].limitations, "the advisory failure was hidden entirely"


def test_an_estimated_finding_is_reported_but_cannot_fail_the_gate() -> None:
    # SPEC-03: a heuristic result is advisory. Reporting it as a violation
    # would both fail a run it may not fail and trip the RunResult invariant
    # that violations require a blocking finding.
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))
    estimated = replace(_finding(), evidence=EvidenceLevel.ESTIMATED)
    provider = _Provider(ParsedOutput(findings=(estimated,)))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(0))

    assert result.gate.selected is GateVerdict.PASS
    assert result.exit_code == 0
    assert not result.gate.has_violations
    assert result.findings == (estimated,), "the advisory finding was dropped"


def test_a_suppressed_finding_is_reported_but_cannot_fail_the_gate() -> None:
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))
    suppressed = replace(
        _finding(), suppression=FindingSuppression(suppressed=True, reason="accepted")
    )
    provider = _Provider(ParsedOutput(findings=(suppressed,)))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(0))

    assert result.gate.selected is GateVerdict.PASS
    assert result.findings == (suppressed,), "the suppressed finding was dropped"


def test_a_finding_from_an_optional_check_does_not_fail_the_gate() -> None:
    # A check the policy marks required=False may still report what it found;
    # the finding is kept, but it cannot turn the gate red — #219 item 4's
    # 참고 metric vs 필수 gate split.
    plan = Plan(checks=(PlannedCheck(check=ADVISORY, task=_task("python.advice")),))
    provider = _Provider(ParsedOutput(findings=(_finding(),)))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(0))

    assert result.gate.selected is GateVerdict.PASS
    assert not result.gate.has_violations
    assert len(result.findings) == 1, "the optional check's finding was dropped"


def test_incomplete_run_with_only_advisory_findings_reports_no_violations() -> None:
    # INCOMPLETE + advisory findings: the run did not finish, and nothing
    # measured a violation — both facts, neither overstated.
    plan = Plan(
        checks=(
            PlannedCheck(check=LINE, blocked="not installed"),
            PlannedCheck(check=ADVISORY, task=_task("python.advice")),
        )
    )
    estimated = replace(_finding(), evidence=EvidenceLevel.ESTIMATED)
    provider = _Provider(ParsedOutput(findings=(estimated,)))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(0))

    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.exit_code == 3
    assert not result.gate.has_violations


# --- evidence that is present but partial ---------------------------------


@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        ({"outcome": Outcome.OUTPUT_TRUNCATED, "truncated": True}, "truncated"),
        ({"outcome": Outcome.TIMED_OUT}, "timed out"),
    ],
)
def test_a_required_check_with_partial_evidence_is_incomplete(
    kwargs: dict[str, object], label: str
) -> None:
    # The run produced output and a zero exit code. Reading that as a pass is
    # the empty PASS one layer up from #205.
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))

    result = verify(
        plan,
        providers={"ruff": _Provider()},
        analyses={},
        runner=lambda _: _outcome(0, **kwargs),  # type: ignore[arg-type]
    )

    assert result.gate.selected is GateVerdict.INCOMPLETE, f"a {label} run passed"
    assert result.exit_code == 3


def test_a_finished_run_still_carries_whether_its_output_was_cut() -> None:
    # Defence in depth, and it caught a real bug: the first version of observe()
    # dropped truncated and timed_out when the run had finished, which are the
    # fields evidence_is_complete reads. The executor does not currently call a
    # truncated run FINISHED -- this is here so that the gate does not depend on
    # it continuing not to.
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))

    result = verify(
        plan,
        providers={"ruff": _Provider()},
        analyses={},
        runner=lambda _: _outcome(0, truncated=True),
    )

    (observation,) = result.observations
    assert observation.truncated, "the observation forgot that the output was cut"
    assert result.gate.selected is GateVerdict.INCOMPLETE


def test_a_required_check_whose_output_was_unreadable_is_incomplete() -> None:
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))
    provider = _Provider(ParsedOutput(failed_to_parse="ruff output was not JSON"))

    result = verify(plan, providers={"ruff": provider}, analyses={}, runner=lambda _: _outcome(0))

    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert any("not JSON" in reason for reason in result.gate.reasons)


def test_a_check_whose_provider_is_not_registered_is_incomplete_not_ignored() -> None:
    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))

    result = verify(plan, providers={}, analyses={})

    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.exit_code == 3


# --- the plan itself ------------------------------------------------------


def test_selecting_nothing_is_a_configuration_error() -> None:
    # 0개 source/check selection은 config 오류다 — not an empty PASS.
    with pytest.raises(NothingSelected):
        Plan(checks=())


def test_a_check_cannot_be_selected_twice() -> None:
    with pytest.raises(ValueError, match="selected twice"):
        Plan(checks=(PlannedCheck(check=LINE), PlannedCheck(check=LINE)))


def test_a_check_cannot_be_both_planned_and_blocked() -> None:
    with pytest.raises(ValueError, match="both planned and blocked"):
        PlannedCheck(check=LINT, task=_task(), blocked="missing")


def test_a_blocked_check_stays_in_the_plan() -> None:
    # A plan that drops what it cannot do describes a smaller run than the one
    # that was asked for, and the difference is invisible.
    plan = Plan(checks=(PlannedCheck(check=LINE), PlannedCheck(check=LINT, blocked="no ruff")))

    assert len(plan.checks) == 2
    assert [item.check.id for item in plan.required_blocked] == ["python.lint"]
    assert "blocked" in str(plan)


def test_a_one_layer_plan_refuses_dependencies_it_cannot_order() -> None:
    dependent = ProviderPlan(
        task=TaskSpec(
            id="python.lint",
            kind=TaskKind.ANALYZE,
            provider="ruff",
            argv=("ruff",),
            cwd="/project",
            depends_on=("python.build",),
        )
    )

    with pytest.raises(ValueError, match="cannot order"):
        Plan(checks=(PlannedCheck(check=LINT, task=dependent),))


# --- the environment a task runs in ---------------------------------------


def test_a_task_runs_with_the_environment_it_was_given(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def runner(spec: ExecutionTaskSpec) -> TaskOutcome:
        seen.update(spec.environment)
        return _outcome(0)

    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))
    verify(
        plan,
        providers={"ruff": _Provider()},
        analyses={},
        runner=runner,
        environment={"ONLY": "this"},
    )

    assert seen == {"ONLY": "this"}
