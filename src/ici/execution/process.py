"""Running a task, and never turning a failure into an empty pass.

The first acceptance criterion of #205 is a list of things that must not become
a PASS: a timeout, a signal, output too long to read, output that is not what
the tool promised, and a cancellation. They have one thing in common — the tool
did not finish saying what it found — and one way of being mishandled: a caller
that checks ``returncode == 0`` treats all five as "some findings, none of them
errors", which is indistinguishable from a clean run.

So an outcome here is a *reason*, not a number, and the number is data inside it.

The second distinction is the one #205 item 6 asks for and it cuts the other
way. A linter that exits 1 **because it found violations** has finished
perfectly well; its exit code is its answer. Treating that as a process failure
is how a working tool gets reported as a broken one. So a provider states which
exit codes are answers (:class:`ExitContract`) and which are failures, and the
interpretation of a run is made against that contract rather than against a
convention.

The bounded capture, the draining threads that keep a full pipe from
deadlocking, and the process-group cleanup are not rewritten here: #205 says to
reuse the current runner's verified timeout and output handling, and this wraps
it. Replacing it would mean re-earning behaviour that already works.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ici.core.runner import ProcessResult, run_process
from ici.execution.cancellation import Cancellation
from ici.execution.tree import DEFAULT_GRACE, Cleanup
from ici.execution.watchdog import WATCHDOG_MARGIN, Trigger, Watchdog

__all__ = [
    "DEFAULT_GRACE",
    "DEFAULT_OUTPUT_LIMIT",
    "DEFAULT_TIMEOUT",
    "ExitContract",
    "Interpretation",
    "Outcome",
    "TaskOutcome",
    "TaskSpec",
    "run_task",
]

DEFAULT_TIMEOUT = 300.0
DEFAULT_OUTPUT_LIMIT = 1_000_000


class Outcome(str, Enum):
    """How the process ended, before anyone asks what it meant.

    ``FINISHED`` says only that the tool ran to completion and its exit code is
    worth reading. Every other member says the tool did not get to the end, and
    none of them can become a pass.
    """

    FINISHED = "finished"
    TIMED_OUT = "timed-out"
    SIGNALLED = "signalled"
    OUTPUT_TRUNCATED = "output-truncated"
    START_FAILED = "start-failed"
    CANCELLED = "cancelled"

    @property
    def ran_to_completion(self) -> bool:
        return self is Outcome.FINISHED


class Interpretation(str, Enum):
    """What a finished run *meant*, read against the provider's contract."""

    SUCCEEDED = "succeeded"
    FOUND_FINDINGS = "found-findings"
    FAILED = "failed"
    DID_NOT_RUN = "did-not-run"

    @property
    def is_an_answer(self) -> bool:
        """Whether the tool actually told us something about the code."""

        return self in (Interpretation.SUCCEEDED, Interpretation.FOUND_FINDINGS)


@dataclass(frozen=True)
class ExitContract:
    """Which exit codes a provider means as answers.

    ``findings`` is the reason this type exists. Ruff exits 1 when it finds
    violations, and that is the tool working: reading it as a process failure
    reports a healthy linter as broken, and reading it as success reports the
    violations as absent.
    """

    success: tuple[int, ...] = (0,)
    findings: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        overlap = set(self.success) & set(self.findings)
        if overlap:
            raise ValueError(f"exit code {sorted(overlap)} cannot mean two things at once")

    def read(self, outcome: TaskOutcome) -> Interpretation:
        """Interpret a run. Anything that did not finish is never an answer."""

        if not outcome.outcome.ran_to_completion:
            return Interpretation.DID_NOT_RUN
        if outcome.exit_code in self.success:
            return Interpretation.SUCCEEDED
        if outcome.exit_code in self.findings:
            return Interpretation.FOUND_FINDINGS
        return Interpretation.FAILED


@dataclass(frozen=True)
class TaskSpec:
    """One process to run, with its bounds stated rather than assumed."""

    argv: tuple[str, ...]
    name: str = ""
    cwd: Path | None = None
    # Explicit and complete. An environment of None would mean "inherit", and
    # inheriting is how a task ends up analysing something other than what it
    # reports on.
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    output_limit: int = DEFAULT_OUTPUT_LIMIT
    #: How long a cancelled tool is given to exit on its own before it is
    #: killed. A tool that is allowed to exit removes its own temporary
    #: files; one that is killed outright leaves them for someone else.
    grace: float = DEFAULT_GRACE

    def __post_init__(self) -> None:
        if not self.argv:
            raise ValueError("a task must have a command")
        if self.timeout <= 0:
            raise ValueError("a task timeout must be positive")
        if self.grace < 0:
            raise ValueError("a task grace period cannot be negative")
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "name", self.name or Path(self.argv[0]).name)


@dataclass(frozen=True)
class TaskOutcome:
    """What happened, with the logs kept apart from what a parser reads.

    #205 item 2 asks for that separation. A parser handed the whole log has to
    guess where the tool's own output begins; a log trimmed to what the parser
    wanted has lost what a person needs to see when the parse fails.
    """

    spec: TaskSpec
    outcome: Outcome
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    detail: str = ""
    #: Whether the capture hit its limit, recorded as a fact of its own.
    #: The chosen ``outcome`` can only name one reason, and a run that both
    #: timed out and overflowed is reported as a timeout -- which would
    #: otherwise make the overflow disappear from a log somebody reads.
    truncated: bool = False
    #: What stopping the process tree found, when it had to be stopped.
    cleanup: Cleanup | None = None

    @property
    def signal(self) -> int | None:
        """The signal that killed it, where the platform reports one that way."""

        return -self.exit_code if self.exit_code < 0 else None

    @property
    def log(self) -> str:
        """Everything the process said, for a person, and where it was cut.

        A truncated log that simply stops reads like a tool that simply
        stopped. The marker is part of the log because the person who needs it
        is the one scrolling to the bottom wondering where the rest went.
        """

        body = self.stdout + self.stderr
        if not self.truncated:
            return body
        return (
            f"{body}\n[ici: capture stopped at {self.spec.output_limit} characters "
            f"per stream; the rest of this log was never read]\n"
        )

    @property
    def cleaned_up(self) -> bool:
        """Whether a stopped run was *verified* to have left nothing running.

        False when nothing was stopped and false when nobody could look, since
        "we did not check" is not a clean tree.
        """

        return self.cleanup is not None and self.cleanup.is_clean

    @property
    def parseable(self) -> str:
        """What a provider's parser should read.

        Empty unless the process finished: a parser given a truncated or
        timed-out stream produces findings that look exactly like real ones and
        are missing however much was cut off.
        """

        return self.stdout if self.outcome.ran_to_completion else ""

    def __str__(self) -> str:
        if self.outcome.ran_to_completion:
            return f"{self.spec.name} exited {self.exit_code}"
        return f"{self.spec.name} {self.outcome.value}" + (
            f": {self.detail}" if self.detail else ""
        )


def run_task(spec: TaskSpec, cancellation: Cancellation | None = None) -> TaskOutcome:
    """Run a task through the existing bounded runner, and classify the result.

    ``cancellation`` is checked before anything is spawned, because the cheapest
    way to clean up after a process is not to have started it. After that the
    watchdog carries the request into the running tree, since the thread sitting
    here is inside the runner and cannot notice anything.
    """

    started = time.monotonic()
    if cancellation is not None and cancellation.requested:
        return TaskOutcome(
            spec=spec,
            outcome=Outcome.CANCELLED,
            exit_code=-1,
            detail=cancellation.reason or "cancelled before it started",
            cleanup=Cleanup(reason="nothing was started", survivors=frozenset()),
        )

    watchdog = Watchdog(
        cancellation,
        grace=spec.grace,
        # Past this the runner itself is the thing that is stuck, so waiting
        # longer for it to notice the timeout only makes the stall longer.
        limit=spec.timeout + spec.grace + WATCHDOG_MARGIN,
    )
    try:
        result = run_process(
            list(spec.argv),
            cwd=spec.cwd,
            env=dict(spec.environment),
            timeout=spec.timeout,
            max_output_chars=spec.output_limit,
            # The environment is the spec's, entire. Merging with the ambient
            # one would make a task's inputs depend on who started ici.
            replace_env=True,
            started=watchdog.attach,
        )
    except (OSError, ValueError) as error:
        return TaskOutcome(
            spec=spec,
            outcome=Outcome.START_FAILED,
            exit_code=-1,
            duration=time.monotonic() - started,
            detail=str(error),
        )
    finally:
        watchdog.finish()
    return _classify(spec, result, watchdog)


def _interruption(watchdog: Watchdog) -> tuple[Outcome, str] | None:
    """What the watchdog did to this run, if it actually interrupted it.

    A cancellation that arrives while the tool is already exiting does not take
    its answer away. ``interrupted`` is read from whether the leader was still
    running at the moment we signalled, so a race is decided by what was true
    rather than by who called first.
    """

    cleanup = watchdog.cleanup
    if cleanup is None or not cleanup.interrupted:
        return None
    if watchdog.trigger == Trigger.CANCELLED:
        return Outcome.CANCELLED, cleanup.reason or "cancelled"
    return Outcome.TIMED_OUT, cleanup.reason or "stopped by the watchdog"


def _reason(spec: TaskSpec, result: ProcessResult, watchdog: Watchdog) -> tuple[Outcome, str]:
    """Pick the one reason a run gets, in the order of causes.

    A cancelled run is also a run that died by signal, and a run that both ran
    out of time and flooded its pipe is both of those. Naming the symptom
    instead of the cause is how a cancelled build gets investigated as a crash.
    """

    interruption = _interruption(watchdog)
    if interruption is not None:
        return interruption
    if result.timed_out:
        return Outcome.TIMED_OUT, f"no answer within {spec.timeout:g}s"
    if result.returncode < 0:
        return Outcome.SIGNALLED, f"killed by signal {-result.returncode}"
    if result.truncated:
        limit = spec.output_limit
        return Outcome.OUTPUT_TRUNCATED, f"more than {limit} characters of output"
    return Outcome.FINISHED, ""


def _classify(spec: TaskSpec, result: ProcessResult, watchdog: Watchdog) -> TaskOutcome:
    """Turn a process result into a reason, keeping the facts it did not name."""

    outcome, detail = _reason(spec, result, watchdog)
    return TaskOutcome(
        spec=spec,
        outcome=outcome,
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration=result.duration,
        detail=detail,
        truncated=result.truncated,
        cleanup=watchdog.cleanup,
    )
