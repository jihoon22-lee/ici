"""What every provider has to say, and the one thing none of them may decide.

A provider answers two questions. *What do I run?* — a plan, which is a
:class:`~ici.domain.tasks.TaskSpec` and the contract its exit codes are read
against. And *what did that mean?* — an :class:`~ici.domain.observation.Observation`
built from what the executor brought back.

The thing a provider may not decide is whether its own run counts. #205 settled
that: only a run that reached the end can be read for an answer, and a provider
that checked its exit code first would be free to disagree with the executor
about whether there was an answer to read. So :func:`observe` is handed the
interpretation rather than computing one, and the not-an-answer cases never
reach a parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ici.domain.enums import TaskState
from ici.domain.finding import Finding
from ici.domain.observation import Measurement, Observation
from ici.domain.tasks import TaskSpec
from ici.execution.process import ExitContract, Interpretation, Outcome, TaskOutcome

__all__ = ["ParsedOutput", "Provider", "ProviderPlan", "observe", "unavailable"]


#: The default reading: 0 is success and every other code is a failure. A
#: provider whose tool reports findings through its exit code says so itself.
STRICT = ExitContract()


@dataclass(frozen=True)
class ProviderPlan:
    """One thing to run, and how to read the number it ends with."""

    task: TaskSpec
    contract: ExitContract = STRICT


@dataclass(frozen=True)
class ParsedOutput:
    """What a provider made of a finished run's output.

    ``failed_to_parse`` is separate from an empty ``findings`` on purpose, and
    it is the whole reason this type exists rather than returning a list. A
    parser that cannot read its tool's output has learned nothing; returning
    zero findings from it reports the code as clean on the strength of not
    having understood the answer.
    """

    findings: tuple[Finding, ...] = ()
    measurements: tuple[Measurement, ...] = ()
    limitations: tuple[str, ...] = ()
    failed_to_parse: str = ""

    @property
    def is_readable(self) -> bool:
        return not self.failed_to_parse


class Provider(Protocol):
    """A tool ici can run, reduced to what the *application* needs from it.

    Deliberately does not include ``plan``. Planning is provider-specific by
    nature -- Ruff needs paths and a cache location, a compiler needs a build
    directory -- and a shared signature could only take ``object``, which every
    real provider then narrows and so fails to implement. By the time a plan is
    executed the planning is already done; what is left is reading the result.
    """

    #: Stable identifier, used in task ids, findings and the registry.
    name: str

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Read a finished run's output. Only called for a run that finished."""


def observe(
    provider: Provider,
    plan: ProviderPlan,
    outcome: TaskOutcome,
    parsed: ParsedOutput | None = None,
) -> Observation:
    """Turn one run into an observation, without ever inventing an answer.

    The order is the point. A run that did not finish is reported as a failed
    task with the executor's reason and **no parse attempt at all**: handing a
    truncated or timed-out stream to a parser produces findings that look
    exactly like real ones and are missing however much was cut off.
    """

    interpretation = plan.contract.read(outcome)
    if not interpretation.is_an_answer:
        return Observation(
            task_id=plan.task.id,
            provider=provider.name,
            state=_state_for(outcome),
            exit_code=outcome.exit_code,
            signal=str(outcome.signal) if outcome.signal is not None else None,
            timed_out=outcome.outcome is Outcome.TIMED_OUT,
            truncated=outcome.truncated,
            duration_seconds=outcome.duration,
            limitations=(_limitation(outcome, interpretation),),
        )

    read = parsed if parsed is not None else provider.parse(outcome)
    if not read.is_readable:
        # The tool ran and said something nobody could read. That is not a
        # clean result with no findings; it is a run with no evidence.
        return Observation(
            task_id=plan.task.id,
            provider=provider.name,
            state=TaskState.FAILED,
            exit_code=outcome.exit_code,
            timed_out=outcome.outcome is Outcome.TIMED_OUT,
            truncated=outcome.truncated,
            duration_seconds=outcome.duration,
            limitations=(read.failed_to_parse,),
        )
    return Observation(
        task_id=plan.task.id,
        provider=provider.name,
        state=TaskState.SUCCEEDED,
        findings=read.findings,
        measurements=read.measurements,
        exit_code=outcome.exit_code,
        # Carried even here, where the run finished. These are the fields
        # Observation.evidence_is_complete reads, and dropping them is how a
        # run whose output was cut short reports as complete evidence. The
        # executor does not currently classify such a run as FINISHED, which
        # is exactly why this must not depend on it continuing not to.
        timed_out=outcome.outcome is Outcome.TIMED_OUT,
        truncated=outcome.truncated,
        duration_seconds=outcome.duration,
        limitations=read.limitations,
    )


def unavailable(
    provider_name: str, task_id: str, reason: str, state: TaskState = TaskState.BLOCKED
) -> Observation:
    """An observation for a tool that was never run, saying so.

    BLOCKED rather than FAILED, and never SUCCEEDED: a check whose tool is
    missing has produced no evidence, and #206 asks for that to come out as
    INCOMPLETE rather than as a pass with nothing in it.
    """

    return Observation(
        task_id=task_id,
        provider=provider_name,
        state=state,
        limitations=(reason,),
    )


def _state_for(outcome: TaskOutcome) -> TaskState:
    if outcome.outcome is Outcome.CANCELLED:
        return TaskState.CANCELLED
    if outcome.outcome is Outcome.START_FAILED:
        return TaskState.BLOCKED
    return TaskState.FAILED


def _limitation(outcome: TaskOutcome, interpretation: Interpretation) -> str:
    if interpretation is Interpretation.FAILED:
        return f"{outcome.spec.name} failed with exit code {outcome.exit_code}"
    detail = f": {outcome.detail}" if outcome.detail else ""
    return f"{outcome.spec.name} {outcome.outcome.value}{detail}"
