"""A failure never becomes an empty pass (#205 PR A).

The first acceptance criterion is a list of five things that must not turn into
a PASS: a timeout, a signal, output too long to read, output that is not what
the tool promised, and a cancellation. They share a cause — the tool did not
finish saying what it found — and a way of being mishandled: a caller that
checks ``returncode == 0`` reads all five as "ran fine, found nothing".

The row that makes the point is a process that exits 0 with its output cut off.
Naively that is a clean pass. Here it is ``did-not-run``, because the part of the
answer that was thrown away might have been the part with the findings in it.

The other half is item 6, cutting the other way: a linter that exits 1 *because
it found violations* has worked perfectly, and calling that a process failure
reports a healthy tool as broken.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from ici.execution.process import (
    ExitContract,
    Interpretation,
    Outcome,
    TaskOutcome,
    TaskSpec,
    run_task,
)

ENV = {"PATH": "/usr/bin:/bin"}
RUFF_LIKE = ExitContract(success=(0,), findings=(1,))


def _spec(code: str, **kwargs) -> TaskSpec:
    return TaskSpec(
        argv=(sys.executable, "-c", code),
        environment=kwargs.pop("environment", ENV),
        timeout=kwargs.pop("timeout", 10.0),
        **kwargs,
    )


class TestNothingThatDidNotFinishBecomesAnAnswer:
    def test_a_timeout_is_not_a_pass(self) -> None:
        outcome = run_task(_spec("import time; time.sleep(30)", timeout=0.5))
        assert outcome.outcome is Outcome.TIMED_OUT
        assert RUFF_LIKE.read(outcome) is Interpretation.DID_NOT_RUN
        assert not RUFF_LIKE.read(outcome).is_an_answer

    def test_truncated_output_is_not_a_pass_even_at_exit_zero(self) -> None:
        # The row that makes the point. returncode == 0 and the answer is
        # incomplete: what was cut off might be the part with the findings.
        outcome = run_task(_spec("print('x' * 5000)", output_limit=50))
        assert outcome.exit_code == 0
        assert outcome.outcome is Outcome.OUTPUT_TRUNCATED
        assert RUFF_LIKE.read(outcome) is Interpretation.DID_NOT_RUN

    def test_a_signal_is_not_a_pass(self) -> None:
        outcome = run_task(_spec("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"))
        assert outcome.outcome is Outcome.SIGNALLED
        assert outcome.signal == 9
        assert RUFF_LIKE.read(outcome) is Interpretation.DID_NOT_RUN

    def test_a_command_that_cannot_start_is_not_a_pass(self) -> None:
        outcome = run_task(TaskSpec(argv=("/definitely/not/here",), environment=ENV))
        assert not outcome.outcome.ran_to_completion
        assert RUFF_LIKE.read(outcome) is Interpretation.DID_NOT_RUN

    def test_a_cancellation_is_not_a_pass(self) -> None:
        # PR B implements cancelling; the reading of it is fixed here so the
        # implementation cannot arrive with a different meaning.
        cancelled = TaskOutcome(spec=_spec("pass"), outcome=Outcome.CANCELLED, exit_code=0)
        assert RUFF_LIKE.read(cancelled) is Interpretation.DID_NOT_RUN

    @pytest.mark.parametrize(
        "outcome",
        [o for o in Outcome if o is not Outcome.FINISHED],
        ids=lambda o: o.value,
    )
    def test_no_unfinished_outcome_can_ever_be_an_answer(self, outcome: Outcome) -> None:
        # Asserted over the whole enum rather than a list written here, so a
        # reason added later cannot be forgotten.
        result = TaskOutcome(spec=_spec("pass"), outcome=outcome, exit_code=0)
        assert RUFF_LIKE.read(result) is Interpretation.DID_NOT_RUN


class TestFindingsAreNotFailures:
    """#205 item 6, cutting the other way."""

    def test_a_linter_exiting_one_with_findings_has_worked(self) -> None:
        outcome = run_task(_spec("print('F401 unused import'); raise SystemExit(1)"))
        assert outcome.outcome is Outcome.FINISHED
        assert RUFF_LIKE.read(outcome) is Interpretation.FOUND_FINDINGS
        assert RUFF_LIKE.read(outcome).is_an_answer

    def test_the_same_exit_code_is_a_failure_without_that_contract(self) -> None:
        # The contract is what makes 1 an answer; it is not a convention.
        outcome = run_task(_spec("raise SystemExit(1)"))
        assert ExitContract().read(outcome) is Interpretation.FAILED

    def test_an_unlisted_code_is_a_failure(self) -> None:
        outcome = run_task(_spec("raise SystemExit(2)"))
        assert RUFF_LIKE.read(outcome) is Interpretation.FAILED

    def test_success_is_still_success(self) -> None:
        outcome = run_task(_spec("print('clean')"))
        assert RUFF_LIKE.read(outcome) is Interpretation.SUCCEEDED

    def test_a_code_cannot_mean_two_things(self) -> None:
        with pytest.raises(ValueError, match="two things"):
            ExitContract(success=(0, 1), findings=(1,))


class TestTheLogAndTheParseableOutputAreSeparate:
    """#205 item 2."""

    def test_the_log_holds_everything_the_process_said(self) -> None:
        outcome = run_task(_spec("import sys; print('out'); print('err', file=sys.stderr)"))
        assert "out" in outcome.log and "err" in outcome.log

    def test_the_parser_reads_only_what_the_tool_promised(self) -> None:
        outcome = run_task(
            _spec("import sys; print('findings'); print('warning', file=sys.stderr)")
        )
        assert outcome.parseable.strip() == "findings"

    def test_a_parser_is_given_nothing_from_a_run_that_did_not_finish(self) -> None:
        # A parser handed a truncated stream produces findings that look exactly
        # like real ones and are missing however much was cut off.
        outcome = run_task(_spec("print('x' * 5000)", output_limit=50))
        assert outcome.parseable == ""
        assert outcome.log  # the person still gets to see it


class TestTasksDoNotContaminateEachOther:
    """The second acceptance criterion, with the two tasks actually concurrent."""

    def test_two_tasks_with_different_environments(self, tmp_path: Path) -> None:
        results: dict[str, str] = {}

        def record(name: str, value: str) -> None:
            outcome = run_task(
                TaskSpec(
                    argv=(sys.executable, "-c", "import os; print(os.environ['MARKER'])"),
                    environment={**ENV, "MARKER": value},
                    name=name,
                )
            )
            results[name] = outcome.parseable.strip()

        threads = [
            threading.Thread(target=record, args=("first", "alpha")),
            threading.Thread(target=record, args=("second", "beta")),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert results == {"first": "alpha", "second": "beta"}

    def test_two_tasks_with_different_working_directories(self, tmp_path: Path) -> None:
        first, second = tmp_path / "one", tmp_path / "two"
        first.mkdir()
        second.mkdir()
        results: dict[str, str] = {}

        def record(name: str, where: Path) -> None:
            outcome = run_task(
                TaskSpec(
                    argv=(sys.executable, "-c", "import os; print(os.getcwd())"),
                    environment=ENV,
                    cwd=where,
                    name=name,
                )
            )
            results[name] = os.path.realpath(outcome.parseable.strip())

        threads = [
            threading.Thread(target=record, args=("one", first)),
            threading.Thread(target=record, args=("two", second)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert results == {
            "one": os.path.realpath(first),
            "two": os.path.realpath(second),
        }

    def test_running_a_task_does_not_move_this_process(self, tmp_path: Path) -> None:
        before = os.getcwd()
        run_task(TaskSpec(argv=(sys.executable, "-c", "pass"), environment=ENV, cwd=tmp_path))
        assert os.getcwd() == before

    def test_running_a_task_does_not_change_this_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ICI_PROCESS_SENTINEL", "unchanged")
        run_task(
            TaskSpec(
                argv=(sys.executable, "-c", "pass"),
                environment={**ENV, "ICI_PROCESS_SENTINEL": "something-else"},
            )
        )
        assert os.environ["ICI_PROCESS_SENTINEL"] == "unchanged"

    def test_a_task_does_not_inherit_this_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ICI_LEAK_SENTINEL", "should-not-travel")
        outcome = run_task(_spec("import os; print('ICI_LEAK_SENTINEL' in os.environ)"))
        assert outcome.parseable.strip() == "False"


class TestASpecStatesItsBounds:
    def test_a_command_is_required(self) -> None:
        with pytest.raises(ValueError, match="command"):
            TaskSpec(argv=())

    def test_a_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            TaskSpec(argv=("true",), timeout=0)

    def test_a_name_defaults_to_the_program(self) -> None:
        assert TaskSpec(argv=("/usr/bin/ruff", "check")).name == "ruff"

    def test_the_environment_defaults_to_empty_not_inherited(self) -> None:
        # None would mean "inherit", and inheriting is how a task ends up
        # analysing something other than what it reports on.
        assert TaskSpec(argv=("true",)).environment == {}


class TestAnOutcomeReadsAsWhatHappened:
    def test_a_finished_run_reports_its_code(self) -> None:
        assert "exited 3" in str(run_task(_spec("raise SystemExit(3)")))

    def test_an_unfinished_run_reports_the_reason(self) -> None:
        rendered = str(run_task(_spec("import time; time.sleep(30)", timeout=0.5)))
        assert "timed-out" in rendered and "0.5" in rendered


class TestALogSaysWhereItWasCut:
    """#205 item 2's other half: the bound on the log is part of the log."""

    def test_an_untruncated_log_is_exactly_what_the_process_said(self) -> None:
        outcome = run_task(_spec("import sys; print('out'); print('err', file=sys.stderr)"))

        assert not outcome.truncated
        assert outcome.log == outcome.stdout + outcome.stderr

    def test_a_truncated_log_says_so_where_a_person_will_see_it(self) -> None:
        # A truncated log that simply stops reads like a tool that simply
        # stopped, and the person scrolling to the bottom has no way to tell
        # the difference.
        outcome = run_task(_spec("print('x' * 5000)", output_limit=50))

        assert outcome.truncated
        assert outcome.log.rstrip().endswith("never read]")
        assert "50 characters" in outcome.log

    def test_the_truncation_survives_being_reported_as_something_else(self) -> None:
        # A run can only be given one reason, and a run that both flooded its
        # pipe and ran out of time is reported as a timeout. Without a fact of
        # its own the truncation would disappear from the log entirely.
        outcome = run_task(
            _spec(
                "import sys, time\n"
                "sys.stdout.write('x' * 100000)\n"
                "sys.stdout.flush()\n"
                "time.sleep(30)\n",
                timeout=1.0,
                output_limit=100,
            )
        )

        assert outcome.outcome is Outcome.TIMED_OUT
        assert outcome.truncated, "the overflow was forgotten once a reason was chosen"
        assert "never read]" in outcome.log

    def test_each_stream_is_bounded_separately(self) -> None:
        outcome = run_task(
            _spec(
                "import sys\nsys.stdout.write('o' * 5000)\nsys.stderr.write('e' * 5000)\n",
                output_limit=100,
            )
        )

        assert len(outcome.stdout) <= 100
        assert len(outcome.stderr) <= 100
