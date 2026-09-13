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
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ici.domain.enums import TaskState
from ici.domain.observation import Measurement, Observation
from ici.engines.line import count_lines

__all__ = ["PROVIDER_NAME", "LineRequest", "count"]

PROVIDER_NAME = "ici.line"


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
        measurements=measurements,
        limitations=tuple(f"not readable: {item}" for item in unreadable),
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
