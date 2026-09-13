"""Actually running a tool, in an environment that was chosen rather than found.

PR A took its probe as an argument so the selection rules could be tested with
no process involved. This is the argument it was waiting for: the one place that
starts a process, with the environment passed as a value.

Three things it refuses to do, each one a way a result stops meaning what it
says:

- **inherit.** The child is given the snapshot's mapping and nothing else, so a
  variable nobody put in the snapshot cannot reach it. An inherited
  ``PYTHONPATH`` is exactly how a run analyses a different tree than the one it
  reports on.
- **give up quietly.** A timeout and a flood of output both come back as facts
  on the result, not as an exception to catch or a partial string to parse. PR A
  maps both to ``BROKEN``, which is a third answer rather than "unavailable".
- **use a shell.** argv is a list. A path with a space is a path with a space.

The starting itself is not done here. #205 item 5 asks for the probe path to
move onto the common executor, keeping the boundary that the resolver chooses a
tool and the executor runs it, and the move fixes two things this module used to
get wrong while looking like it did not:

- ``subprocess.run(capture_output=True)`` reads **all** of the output before
  anything trims it. The limit was on the report, not on the reading: a probe
  printing 200 MB returned a tidy 1 KB with ``truncated`` set, having first
  taken 600 MB of resident memory to do it. The executor's capture stops at the
  bound as it drains.
- ``subprocess.run(timeout=...)`` kills the process it started and nothing else.
  A probe that had spawned a child left that child running -- and, with no new
  session, running inside ici's own process group. The executor puts the child
  in its own group and cleans up the group.

Neither was visible in the result. Both were a tool "answering" in a way that
said nothing about what it left behind.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ici.execution.process import Outcome, TaskOutcome, TaskSpec, run_task
from ici.toolchain.environment import EnvironmentSnapshot
from ici.toolchain.resolution import ProbeResult

__all__ = ["DEFAULT_OUTPUT_LIMIT", "DEFAULT_TIMEOUT", "probe_with", "run"]

# A version probe that has not answered in this long is not slow, it is stuck.
DEFAULT_TIMEOUT = 30.0

# Enough for any version banner. Past it the output is not a version, and
# reading the first part of it as one is how a broken tool passes for a working
# one (#204 item 6).
DEFAULT_OUTPUT_LIMIT = 64 * 1024


def run(
    argv: Sequence[str],
    *,
    environment: EnvironmentSnapshot,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
) -> ProbeResult:
    """Run ``argv`` in ``environment`` and report what happened.

    Returns a result for every outcome including the failures. A caller
    deciding what a timeout means is the point: #204 keeps "could not be asked"
    apart from "is not there", and an exception here would collapse them at the
    first ``except``.
    """

    try:
        spec = TaskSpec(
            argv=tuple(argv),
            cwd=cwd,
            # The snapshot, and only the snapshot. Anything not in it cannot
            # reach the child.
            environment=dict(environment.variables),
            timeout=timeout,
            output_limit=output_limit,
        )
    except ValueError as error:
        # An argv or a timeout the executor will not accept. Reported rather
        # than raised, for the same reason as everything else here.
        return ProbeResult(exit_code=-1, output=str(error))
    return _as_probe_result(run_task(spec), output_limit)


def _as_probe_result(outcome: TaskOutcome, output_limit: int) -> ProbeResult:
    """Read an execution outcome as the answer to "can this tool be asked?".

    The executor bounds each stream separately, so the two together can exceed
    what a probe asked for; the combined limit is applied again here rather
    than letting a caller's number quietly mean twice itself.
    """

    output = outcome.stdout + outcome.stderr
    truncated = outcome.truncated
    if len(output) > output_limit:
        output, truncated = output[:output_limit], True

    if outcome.outcome is Outcome.TIMED_OUT:
        return ProbeResult(exit_code=-1, output=output, timed_out=True, truncated=truncated)
    if outcome.outcome is Outcome.START_FAILED:
        return ProbeResult(exit_code=-1, output=outcome.detail)
    if outcome.outcome is Outcome.CANCELLED:
        # Nobody cancels a probe today. If that changes, a cancelled probe must
        # not read as a tool that answered, so it is given a code that is not
        # usable rather than inheriting whatever signal killed it.
        return ProbeResult(exit_code=-1, output=output, truncated=truncated)
    return ProbeResult(exit_code=outcome.exit_code, output=output, truncated=truncated)


def probe_with(
    environment: EnvironmentSnapshot,
    *,
    cwd: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT,
):
    """A probe bound to one environment, for :class:`~ici.toolchain.resolver.Resolver`.

    This is what closes PR A's loop: the resolver asked for a callable, and the
    environment a tool is probed in is now part of the question rather than
    whatever the process happened to inherit.
    """

    def probe(argv: Sequence[str]) -> ProbeResult:
        return run(argv, environment=environment, cwd=cwd, timeout=timeout)

    return probe
