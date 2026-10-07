"""Ruff's output dialects, parsed once for both paths.

Ruff emits three distinct answer shapes that ici consumes: the ``check``
JSON array, the ``format --check`` text stream (in its current
``unformatted:``/``-->`` form and the legacy ``Would reformat:`` form), and
the line-oriented ``warning:`` blocks on stderr. Before this module existed
the stable engine and the next provider each carried their own reading of
those dialects, and the copies had already drifted — next never converted
Ruff's exclusive end column to inclusive, and it defaulted a missing
``code``/``message`` instead of refusing the entry.

Everything here is deliberately strict: a field Ruff must emit that is
absent or mistyped makes the whole output unreadable rather than a finding
with a guessed value, because a parser that guesses reports a clean file on
the strength of not having understood the answer. What stays with the
consumers is policy, not syntax — how a path relates to the component root,
what an exit code means, and what shape the result takes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

__all__ = [
    "RuffDiagnostic",
    "RuffFormatItem",
    "RuffFormatText",
    "format_supports_json",
    "is_format_success_output",
    "parse_check_json",
    "parse_format_text",
    "parse_legacy_format_text",
    "parse_stderr_warnings",
]


@dataclass(frozen=True)
class RuffDiagnostic:
    """One ``ruff check`` JSON entry, with paths and offsets as Ruff said them.

    ``filename`` is exactly what Ruff emitted — typically absolute — because
    relating it to the analysed component is the consumer's policy, not the
    parser's. ``end_column`` is *inclusive*: Ruff's end position points one
    past the last character, and a reported range that covers a character it
    does not cover misstates the violation.
    """

    filename: str
    code: str
    message: str
    row: int
    column: int | None
    end_row: int | None
    end_column: int | None


def parse_check_json(text: str) -> tuple[tuple[RuffDiagnostic, ...], str | None]:
    """Turn ``ruff check --output-format json`` output into diagnostics.

    Returns ``(diagnostics, None)`` on success and ``((), error)`` when the
    output is present but unreadable. An empty stream is an empty answer —
    whether that is acceptable for the exit code observed is the consumer's
    contract, not the parser's.
    """

    stripped = text.strip()
    if not stripped:
        return (), None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as error:
        return (), f"ruff output was not JSON: {error}"
    if not isinstance(payload, list):
        return (), f"ruff output was {type(payload).__name__}, expected a list"

    diagnostics = []
    for index, item in enumerate(payload):
        try:
            diagnostics.append(_diagnostic(item))
        except ValueError as error:
            return (), f"ruff entry {index} was not readable: {error}"
    return tuple(diagnostics), None


def _diagnostic(item: object) -> RuffDiagnostic:
    if not isinstance(item, dict):
        raise ValueError(f"entry was {type(item).__name__}, expected an object")
    filename = _required_text(item, "filename")
    code = _required_text(item, "code")
    message = _required_text(item, "message")

    location = item.get("location")
    if not isinstance(location, dict):
        raise ValueError("location is not an object")
    row = _coordinate(location.get("row"), "row")
    raw_column = location.get("column")
    column = None if raw_column is None else _coordinate(raw_column, "column")

    end_row: int | None = None
    end_column: int | None = None
    end_location = item.get("end_location")
    if end_location is not None:
        if not isinstance(end_location, dict):
            raise ValueError("end_location is not an object")
        end_row = _coordinate(end_location.get("row"), "end row", minimum=row)
        exclusive_end = _coordinate(end_location.get("column"), "end column")
        end_column = max(1, exclusive_end - 1)
        if end_row == row and column is not None and end_column < column:
            raise ValueError("source range is invalid")

    return RuffDiagnostic(
        filename=filename,
        code=code,
        message=message,
        row=row,
        column=column,
        end_row=end_row,
        end_column=end_column,
    )


def _required_text(item: dict[object, object], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is missing")
    return value


def _coordinate(value: object, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} is invalid")
    return value


# --- format --check -------------------------------------------------------

#: The current dialect: a per-file ``unformatted:`` header, then a ``-->``
#: span pointing at where the diff would start.
_FORMAT_HEADER_RE = re.compile(r"^unformatted:")
_FORMAT_SPAN_RE = re.compile(r"^\s*-->\s*(?P<path>.+?):(?P<line>[1-9]\d*):(?P<column>[1-9]\d*)\s*$")
#: The legacy dialect: one ``Would reformat: <path>`` line per file.
_FORMAT_LEGACY_RE = re.compile(r"Would reformat: (?P<path>\S.*)")
#: The closing tally. The units must agree with the counts — a line that
#: claims "1 files" is not a summary Ruff wrote.
_FORMAT_SUMMARY_RE = re.compile(
    r"(?P<would_count>[1-9]\d*) (?P<would_unit>file|files) would be reformatted"
    r"(?:, (?P<already_count>[1-9]\d*) (?P<already_unit>file|files) already formatted)?"
)
#: A looser recognition for the same idea — a ``N files ...`` line that is
#: not the strict summary is context, not a finding.
_FORMAT_COUNT_RE = re.compile(r"^\d+ files?\b")
#: Diff bodies, gutters and line-number decorations are context, not findings.
_FORMAT_CONTEXT_RE = re.compile(r"^\s*(\||[-+]|\d+\s*[-+|])")
_FORMAT_SUCCESS_RE = re.compile(r"\d+ files? already formatted(?:\r?\n)?\Z")


@dataclass(frozen=True)
class RuffFormatItem:
    """One file ``ruff format --check`` says would change."""

    path: str
    line: int
    column: int | None
    from_span: bool


@dataclass(frozen=True)
class RuffFormatText:
    items: tuple[RuffFormatItem, ...]
    would_reformat: int | None
    already_formatted: int | None
    error: str | None = None


def parse_format_text(text: str) -> RuffFormatText:
    """Turn ``ruff format --check`` text output into one item per file.

    Both text dialects are recognised — current ``unformatted:``/``-->``
    pairs and legacy ``Would reformat:`` lines — plus the diff gutters and
    the closing tally, which are context. A line this grammar does not know
    makes the whole output unreadable: a half-read stream reporting zero
    files is indistinguishable from a formatted tree.
    """

    items: list[RuffFormatItem] = []
    would_reformat: int | None = None
    already_formatted: int | None = None
    pending_header = False

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            pending_header = False
            continue
        legacy = _FORMAT_LEGACY_RE.fullmatch(line)
        if legacy is not None:
            items.append(RuffFormatItem(legacy.group("path").strip(), 1, None, False))
            continue
        if _FORMAT_HEADER_RE.match(line):
            pending_header = True
            continue
        span = _FORMAT_SPAN_RE.match(line)
        if span is not None:
            if pending_header:
                items.append(
                    RuffFormatItem(
                        span.group("path"),
                        int(span.group("line")),
                        int(span.group("column")),
                        True,
                    )
                )
            pending_header = False
            continue
        summary = _FORMAT_SUMMARY_RE.fullmatch(line)
        if summary is not None:
            would_reformat = int(summary.group("would_count"))
            already = summary.group("already_count")
            already_formatted = None if already is None else int(already)
            continue
        if _FORMAT_COUNT_RE.match(line) or _FORMAT_CONTEXT_RE.match(line):
            continue
        return RuffFormatText((), None, None, f"unrecognized ruff format line: {line.strip()!r}")
    return RuffFormatText(tuple(items), would_reformat, already_formatted)


def parse_legacy_format_text(text: str) -> tuple[tuple[str, ...], str | None]:
    """The strict legacy grammar: ``Would reformat:`` lines, then one tally.

    Older Ruff — the only kind a path without ``--output-format`` support
    will ever meet — emits exactly ``N`` file lines followed by a summary
    whose count must equal the number of paths it just printed. Anything
    else means the stream was not understood, and guessing at it would
    report a formatted tree that is not.
    """

    lines = text.splitlines()
    if not lines:
        return (), "no output"

    summary = _FORMAT_SUMMARY_RE.fullmatch(lines[-1])
    if summary is None:
        return (), "no trailing summary"
    for earlier in lines[:-1]:
        if _FORMAT_SUMMARY_RE.fullmatch(earlier) is not None:
            return (), "summary is not the final line"

    would_count = int(summary.group("would_count"))
    if summary.group("would_unit") != ("file" if would_count == 1 else "files"):
        return (), "summary count does not agree with its unit"
    already_count = summary.group("already_count")
    if already_count is not None and summary.group("already_unit") != (
        "file" if int(already_count) == 1 else "files"
    ):
        return (), "summary count does not agree with its unit"

    paths: list[str] = []
    for line in lines[:-1]:
        match = _FORMAT_LEGACY_RE.fullmatch(line)
        if match is None:
            return (), f"unrecognized format line: {line!r}"
        path = match.group("path").strip()
        if not path:
            return (), "format line names no file"
        paths.append(path)

    if not paths or would_count != len(paths):
        return (), "summary count does not match the files listed"
    return tuple(paths), None


def is_format_success_output(text: str) -> bool:
    """Whether ``format --check`` printed the all-formatted tally, or nothing."""

    return not text.strip() or _FORMAT_SUCCESS_RE.fullmatch(text) is not None


_PREVIEW_ONLY_RE = re.compile(r"only respected in preview mode", re.IGNORECASE)


def format_supports_json(help_text: str) -> bool:
    """Whether a ``ruff format --help`` says ``--output-format`` works.

    The flag's name in the help is not enough on its own — some versions
    list it as respected only in preview mode, which is a promise it does
    not keep.
    """

    return "--output-format" in help_text and _PREVIEW_ONLY_RE.search(help_text) is None


# --- stderr ---------------------------------------------------------------

_WARNING_RE = re.compile(r"^warning:\s+\S.*$")


def parse_stderr_warnings(text: str) -> tuple[list[str], str | None]:
    """Ruff's line-oriented stderr warnings, refusing arbitrary stderr.

    A warning block is a ``warning: ...`` line followed by its indented
    continuation lines. Anything else on stderr is not a warning Ruff is
    known to write, so the whole stream is reported unreadable rather than
    picking out the parts that happened to match.
    """

    if not text.strip():
        return [], None

    lines = text.splitlines()
    warnings: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _WARNING_RE.fullmatch(line):
            return [], f"unrecognized stderr line: {line!r}"

        block = [line]
        index += 1
        while index < len(lines):
            continuation = lines[index]
            if _WARNING_RE.fullmatch(continuation):
                break
            if continuation and continuation[0].isspace():
                block.append(continuation)
                index += 1
                continue
            return [], f"unrecognized stderr line: {continuation!r}"

        warnings.append("\n".join(block).rstrip("\r\n"))

    return warnings, None
