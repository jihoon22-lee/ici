"""ici's own line count, as an observation.

There is no tool to run here: the algorithm is ici's, so this produces the
observation directly instead of planning a process. It is the same function the
stable ``line`` engine calls -- #206 asks for the existing algorithm to be
connected, and connecting means one implementation with two callers rather than
a second one that means almost the same thing.

What the counts are *for* differs from the stable engine, though, and that is
deliberate. Here they are :class:`~ici.domain.observation.Measurement` values
with their raw numerator and denominator kept, because SPEC-04 forbids
averaging ratios across components and only raw counts can be combined later.

The per-file size rule is carried too: the stable engine flags a file over
500 code lines and escalates over 1000, and WP20 promised no removed rules.
Next has no configurable threshold keys — the matrix calls them ``확인`` —
so the rule keeps its stable defaults as fixed constants and the gate, not
a warn/fail knob, decides whether the finding matters.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.engines.line import count_lines

__all__ = ["PROVIDER_NAME", "LineRequest", "count"]

PROVIDER_NAME = "ici.line"

# The stable engine's defaults (engines/line.py warn_limit/fail_limit),
# fixed here because the next schema has no per-check threshold keys.
_WARN_LINES = 500
_FAIL_LINES = 1000


@dataclass(frozen=True)
class LineRequest:
    """The files to count, resolved before this is called."""

    project_root: Path
    files: tuple[Path, ...]
    task_id: str = "python.line"

    def __post_init__(self) -> None:
        if not self.files:
            # Zero selected sources is a configuration error, not an empty
            # result: SPEC-04 says so, and a check that quietly counts nothing
            # reports a project as measured when nothing was looked at.
            raise ValueError("a line count needs at least one file")


def count(request: LineRequest) -> Observation:
    """Count the request's files and report what was and was not readable."""

    code = comment = blank = 0
    unreadable: list[str] = []
    findings: list[Finding] = []
    for path in request.files:
        try:
            file_code, file_comment, file_blank = count_lines(path)
        except OSError as error:  # pragma: no cover - count_lines swallows most
            unreadable.append(f"{_relative(path, request.project_root)}: {error}")
            continue
        if file_code == file_comment == file_blank == 0 and not _is_empty(path):
            # count_lines returns zeros both for an empty file and for one it
            # could not decode. Telling them apart matters: an unreadable file
            # that counts as zero lines is a file nobody looked at, reported as
            # a file with nothing in it.
            unreadable.append(_relative(path, request.project_root))
            continue
        code += file_code
        comment += file_comment
        blank += file_blank
        finding = _oversized(path, file_code, request)
        if finding is not None:
            findings.append(finding)

    total = code + comment + blank
    counted = len(request.files) - len(unreadable)
    measurements = (
        Measurement(name="lines_total", value=total, unit="lines"),
        _part("lines_code", code, total),
        _part("lines_comment", comment, total),
        _part("lines_blank", blank, total),
        _part("files_counted", counted, len(request.files), unit="files"),
    )
    return Observation(
        task_id=request.task_id,
        provider=PROVIDER_NAME,
        state=TaskState.SUCCEEDED,
        findings=tuple(findings),
        measurements=measurements,
        limitations=tuple(f"not readable: {item}" for item in unreadable),
    )


def _oversized(path: Path, code: int, request: LineRequest) -> Finding | None:
    """The stable file-size rule, carried at its fixed thresholds."""

    if code > _FAIL_LINES:
        severity, message = (
            "high",
            f"Pure code lines ({code}) exceed {_FAIL_LINES} lines limit (Refactoring required)",
        )
    elif code > _WARN_LINES:
        severity, message = (
            "medium",
            f"Pure code lines ({code}) exceed {_WARN_LINES} lines threshold (Split recommended)",
        )
    else:
        return None
    relative = _relative(path, request.project_root)
    return Finding(
        fingerprint="line-"
        + hashlib.sha1(relative.encode(), usedforsecurity=False).hexdigest()[:16],
        rule_id="line.file-size",
        message=message,
        severity=severity,
        confidence="high",
        provider=PROVIDER_NAME,
        primary_location=SourceSpan(path=relative, start_line=1),
        task_id=request.task_id,
        evidence=EvidenceLevel.MEASURED,
    )


def _part(name: str, value: int, whole: int, unit: str = "lines") -> Measurement:
    """A count, with the whole it is part of kept beside it where there is one.

    The raw pair is what lets these be combined across components later; SPEC-04
    forbids averaging the ratios. When the whole is zero there is no fraction to
    describe, and claiming one would be inventing a denominator.
    """

    if whole <= 0:
        return Measurement(name=name, value=value, unit=unit)
    return Measurement(name=name, value=value, unit=unit, numerator=value, denominator=whole)


def _is_empty(path: Path) -> bool:
    try:
        return path.stat().st_size == 0
    except OSError:  # pragma: no cover - the stat that follows a failed read
        return False


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
