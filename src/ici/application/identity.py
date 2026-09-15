"""What makes a task's answer *this* task's answer — the cache key's parts.

#209 item 1: an observation may only be reused when everything that could
change its answer is inside the key. This module composes that key; it never
reads a file itself — the caller hands in the digests it measured, because who
resolves paths (the inventory, the workspace root) is context the identity
does not own.

The parts, and why each is there:

- **the task itself** — ``share_key`` already carries provider, kind, argv,
  cwd and the environment overlay, which is everything the command line says.
- **the declared inputs** — a digest per ``input_refs`` entry. A provider that
  cannot enumerate what it reads has no complete identity, and #209's answer
  for that is a disabled cache with a recorded reason, not a partial key.
- **the tool** — the digest of the executable itself, so a Ruff upgrade is a
  different run rather than a silent change of measurer.
- **the policy** — the composed configuration's digest, so a rule
  reconfiguration cannot reuse evidence produced under different settings.
"""

from __future__ import annotations

import hashlib

from ici.application.graph import WorkUnit
from ici.domain.enums import TaskKind

__all__ = ["task_identity"]

_DIGEST = "sha256"


def task_identity(
    unit: WorkUnit,
    *,
    input_digests: tuple[tuple[str, str], ...] | None,
    tool_digest: str | None,
    policy_digest: str,
) -> tuple[str | None, str]:
    """The cache key for one unit, or why it has none.

    ``input_digests`` are ``(declared-path, content-digest)`` pairs the caller
    measured — ``None`` when one could not be read. A ``None`` key is not a
    failure of the run: the unit still executes, the reason is just recorded so
    "why did nothing reuse" has an answer.
    """

    if unit.is_internal or unit.plan is None:
        return None, "internal checks run in-process; there is nothing to cache"
    task = unit.task
    if task.kind is TaskKind.TEST:
        # #209 item 6: test evidence is per-run. A cached test result would be
        # an answer about code that may have changed since it ran.
        return None, "test evidence is per-run and is never persisted"
    if not task.cacheable:
        return None, f"{task.kind.value} tasks are not cacheable"
    if not task.input_refs:
        return None, "the provider did not declare its inputs; reuse stays off"
    if input_digests is None:
        return None, "a declared input could not be read; reuse stays off"
    if tool_digest is None:
        return None, "the tool is not content-addressable; reuse stays off"

    hasher = hashlib.new(_DIGEST)
    hasher.update(repr(task.share_key).encode("utf-8"))
    for path, digest in input_digests:
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(digest.encode("utf-8"))
        hasher.update(b"\0")
    hasher.update(tool_digest.encode("utf-8"))
    hasher.update(policy_digest.encode("utf-8"))
    return f"{_DIGEST}:{hasher.hexdigest()}", ""
