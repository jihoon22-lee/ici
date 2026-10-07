"""mypy's text output dialects, parsed once for both paths.

mypy answers in a single text shape — ``path:line[:column]: severity:
message [code]`` — but the two paths used to read it with two grammars that
had drifted: stable recognised only ``error`` and ``note`` severities, let
zero-valued coordinates through nothing (it never saw them because its own
regex rejected them), and kept the trailing ``[code]`` inside the message;
next accepted ``warning``, allowed ``0`` as a line number, and split the
code out.

This module owns the line vocabulary: what a diagnostic line is, which
severities exist, and which lines are context rather than findings. What
stays with the consumers is grammar — whether stderr counts as mypy output,
whether a warning severity is a finding or unreadable, how notes merge into
errors, and how strict the clean-run tally must be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "ALL_MYPY_SEVERITIES",
    "MYPY_CONTEXT_RE",
    "MYPY_FOUND_SUMMARY_RE",
    "MYPY_SUCCESS_LINE_RE",
    "MypyDiagnostic",
    "parse_mypy_line",
    "parse_mypy_stream",
]

#: ``path:line[:col]: severity: message [code]`` — mypy's stable text shape
#: since long before error codes existed. Line and column are 1-based; a
#: zero would mean the stream is not what it claims. The message must carry
#: at least one non-space character — a diagnostic that says nothing is not
#: a diagnostic.
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>[1-9]\d*)(?::(?P<column>[1-9]\d*))?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>\S.*?)"
    r"(?:\s+\[(?P<code>[A-Za-z0-9_-]+)\])?$"
)

#: The clean-run tally: ``Success: no issues found in N source files?``.
MYPY_SUCCESS_LINE_RE = re.compile(r"^Success: no issues found in (?P<count>\d+) source files?$")

#: The violations tally, strict form — the full sentence, not a prefix:
#: ``Found N errors in M files (checked K source files)``.
MYPY_FOUND_SUMMARY_RE = re.compile(r"Found \d+ errors? in \d+ files? \(checked \d+ source files?\)")

#: Lines mypy adds that are part of the answer but carry no finding —
#: recognised by prefix, because the tallies trail off differently per
#: version.
MYPY_CONTEXT_RE = re.compile(r"^(Found \d+ errors?|Success: no issues found|Checked \d+|\s*$)")

#: Every severity the diagnostic-line grammar knows. Consumers that accept
#: fewer name their subset — a line whose severity is not accepted reads as
#: not-a-diagnostic there.
ALL_MYPY_SEVERITIES = ("error", "warning", "note")


@dataclass(frozen=True)
class MypyDiagnostic:
    """One ``path:line[:col]: severity: message [code]`` line.

    ``path`` is exactly what mypy emitted — relating it to the analysed
    component is the consumer's policy. ``message`` excludes the trailing
    ``[code]``; :attr:`full_message` rebuilds the line's text for consumers
    that historically reported it whole.
    """

    path: str
    line: int
    column: int | None
    severity: str
    message: str
    code: str | None

    @property
    def full_message(self) -> str:
        if self.code is None:
            return self.message
        return f"{self.message} [{self.code}]" if self.message else f"[{self.code}]"


def parse_mypy_line(
    line: str, *, severities: tuple[str, ...] = ALL_MYPY_SEVERITIES
) -> MypyDiagnostic | None:
    """One diagnostic line, or None when the line is not one this consumer reads."""

    match = _DIAGNOSTIC_RE.fullmatch(line)
    if match is None or match.group("severity") not in severities:
        return None
    return MypyDiagnostic(
        path=match.group("path"),
        line=int(match.group("line")),
        column=int(match.group("column")) if match.group("column") else None,
        severity=match.group("severity"),
        message=match.group("message"),
        code=match.group("code"),
    )


def parse_mypy_stream(
    text: str, *, severities: tuple[str, ...] = ALL_MYPY_SEVERITIES
) -> tuple[tuple[MypyDiagnostic, ...], str | None]:
    """The whole diagnostic stream: diagnostics, context lines, nothing else.

    Every line must be a diagnostic line or a known context line — the
    tallies, the success line, blank lines. Anything else makes the stream
    unreadable, because a half-read stream reporting zero findings is
    indistinguishable from a clean run.
    """

    diagnostics: list[MypyDiagnostic] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        diagnostic = parse_mypy_line(line, severities=severities)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
            continue
        if MYPY_CONTEXT_RE.match(line):
            continue
        return (), f"unrecognized mypy output line: {line.strip()!r}"
    return tuple(diagnostics), None
