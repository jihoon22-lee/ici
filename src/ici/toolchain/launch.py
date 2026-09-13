"""Actually running a tool, in an environment that was chosen rather than found.

PR A took its probe as an argument so the selection rules could be tested with
no process involved. This is the argument it was waiting for: the one place that
starts a process, with the environment passed as a value.

Three things it refuses to do, each one a way a result stops meaning what it
says:

- **inherit.** ``subprocess`` is given the snapshot's mapping and nothing else,
  so a variable nobody put in the snapshot cannot reach the child. An inherited
  ``PYTHONPATH`` is exactly how a run analyses a different tree than the one it
  reports on.
- **give up quietly.** A timeout and a flood of output both come back as facts
  on the result, not as an exception to catch or a partial string to parse. PR A
  maps both to ``BROKEN``, which is a third answer rather than "unavailable".
- **use a shell.** argv is a list. A path with a space is a path with a space.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

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
        # argv is a list and shell is never set, so a path with a space is a
        # path with a space rather than two arguments.
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            # The snapshot, and only the snapshot. Anything not in it cannot
            # reach the child.
            env=dict(environment.variables),
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(exit_code=-1, output="", timed_out=True)
    except (OSError, ValueError) as error:
        # Not executable, not there, or an argv the platform refuses. Reported
        # rather than raised, for the same reason as the timeout.
        return ProbeResult(exit_code=-1, output=str(error))

    output = (completed.stdout or "") + (completed.stderr or "")
    if len(output) > output_limit:
        return ProbeResult(
            exit_code=completed.returncode,
            output=output[:output_limit],
            truncated=True,
        )
    return ProbeResult(exit_code=completed.returncode, output=output)


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
