"""Running a task and publishing what it made, as one thing that cannot half-happen.

This is #205 item 4 put together: *"task output은 허용된 run/build root 안에만
생성하고 private tmp, exclusive resource lock, 원자적 manifest publish를 사용한다.
실패/취소 결과는 정상 cache로 승격하지 않는다."*

The shape is one rule applied to every name a later reader might trust:

    **nothing exists under the name a reader trusts until all of it is there.**

The tool writes into a private staging directory, so while it is running there
is no file under a real name for anybody to find. Each finished artifact is
renamed into place, and the manifest — the thing that says the set is complete —
is renamed in last. A reader that arrives at any moment sees the previous run's
output or this one's, and never a mixture. A run that is cancelled halfway
leaves its half-written files in the staging area, under names nobody reads,
and they go when the staging area does.

The lock is what stops two runs from interleaving those renames into the same
root. It is an operating-system lock precisely because a cancelled run cannot
be relied on to release anything (see :mod:`ici.execution.locks`).

Nothing raises out of here except a caller's own mistake. A refusal is a value
on the result, because a refusal to promote is a *fact about the run* that the
caller needs to report, and an exception is how it would become a crash in
whoever called instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from ici.execution.cancellation import Cancellation
from ici.execution.locks import DEFAULT_LOCK_TIMEOUT, Unlocked, exclusive
from ici.execution.manifest import Manifest, describe, publish
from ici.execution.outputs import STAGING_DIRECTORY, OutputRoot
from ici.execution.process import ExitContract, TaskOutcome, TaskSpec, run_task

__all__ = ["DEFAULT_MANIFEST_NAME", "LOCK_NAME", "Production", "produce"]

DEFAULT_MANIFEST_NAME = "manifest.json"

#: Inside the staging area rather than the root, so the root holds published
#: output and nothing else.
LOCK_NAME = "output.lock"


@dataclass(frozen=True)
class Production:
    """What a run produced, or why nothing of it was published."""

    outcome: TaskOutcome | None
    manifest: Manifest | None = None
    published: Path | None = None
    refusal: str = ""

    @property
    def promoted(self) -> bool:
        """Whether this run's output became something a later run may trust."""

        return self.published is not None

    def __str__(self) -> str:
        if self.promoted:
            assert self.manifest is not None
            return f"{self.manifest.task}: {len(self.manifest.artifacts)} artifact(s) published"
        return self.refusal or "nothing was published"


def produce(
    spec: TaskSpec,
    root: OutputRoot,
    produces: tuple[str, ...] = (),
    contract: ExitContract | None = None,
    cancellation: Cancellation | None = None,
    manifest_name: str = DEFAULT_MANIFEST_NAME,
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
) -> Production:
    """Run ``spec`` with its output confined to ``root`` and publish the result.

    ``produces`` names the files the tool is expected to write, relative to
    where it runs, which is also what they will be called under the root.
    """

    lock = root.path / STAGING_DIRECTORY / LOCK_NAME
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive(lock, timeout=lock_timeout):
            return _produce_holding_the_lock(
                spec, root, produces, contract, cancellation, manifest_name
            )
    except Unlocked as error:
        # Not an error in the tool, and not a result either: nothing ran.
        return Production(outcome=None, refusal=str(error))


def _produce_holding_the_lock(
    spec: TaskSpec,
    root: OutputRoot,
    produces: tuple[str, ...],
    contract: ExitContract | None,
    cancellation: Cancellation | None,
    manifest_name: str,
) -> Production:
    with root.staging() as staged:
        # The tool runs in the staging directory, so a relative output path it
        # writes has no name under the root at all until this function gives it
        # one. There is nothing for a concurrent reader to find early.
        outcome = run_task(replace(spec, cwd=staged), cancellation=cancellation)

        refusal = _refusal(staged, outcome, produces, contract)
        if refusal:
            return Production(outcome=outcome, refusal=refusal)

        for name in produces:
            os.replace(staged / name, root.prepare(name))
        manifest = describe(root, outcome, produces, contract)
        published = publish(manifest, root.prepare(manifest_name))
    return Production(outcome=outcome, manifest=manifest, published=published)


def _refusal(
    staged: Path,
    outcome: TaskOutcome,
    produces: tuple[str, ...],
    contract: ExitContract | None,
) -> str:
    """Why this run must not be promoted, or an empty string if it may be."""

    interpretation = (contract or ExitContract()).read(outcome)
    if not interpretation.is_an_answer:
        return f"{outcome} — not a cacheable result"
    for name in produces:
        try:
            target = _staged_output(staged, name)
        except ValueError as error:
            return str(error)
        if not target.is_file():
            return f"{name} was expected but the tool did not write it"
    return ""


def _staged_output(staged: Path, name: str) -> Path:
    """The staged path for ``name``, refusing one that leaves the staging area.

    The root's own rules are applied to the destination; this is the other
    side. A tool that writes a symlink in its working directory would otherwise
    have that link renamed into the output tree, where following it once is all
    it takes.
    """

    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{name} is not a name inside the task's working directory")
    walked = staged
    for part in candidate.parts:
        walked = walked / part
        if walked.is_symlink():
            raise ValueError(f"{name} was written as a symlink ({walked}) and is not published")
    return walked
