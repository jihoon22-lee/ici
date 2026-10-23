"""Native identities for process evidence, independent of execution labels."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ici.execution.process import ProcessSpec


def process_fingerprint(provider: str, spec: ProcessSpec, root: Path | None, *details: str) -> str:
    """Keep the measured invocation and diagnostic, never its consumer task ID.

    Capture bounds and freshness timestamps are execution bookkeeping. The
    workspace root is supplied by the provider registry; without it, absolute
    paths stay absolute rather than guessing which directory is the project.
    """

    payload = (
        provider,
        _relative(str(spec.cwd), root) if spec.cwd is not None else None,
        tuple(_relative(arg, root) for arg in spec.argv),
        details,
    )
    digest = hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
    return f"sha256:{digest}"


def _relative(value: str, root: Path | None) -> str:
    path = Path(os.path.normpath(value))
    if root is None or not path.is_absolute():
        return value
    try:
        relative = path.relative_to(root)
    except ValueError:
        return value
    return "${workspace}" if relative == Path(".") else "${workspace}/" + relative.as_posix()
