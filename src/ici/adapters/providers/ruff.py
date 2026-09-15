"""Running Ruff, and two things it must never be able to do.

**It must never change the code.** #206 item 2 says so, and the reason is that
a verification tool that edits the tree it is verifying has destroyed the thing
it was asked to report on -- the next run measures the edit, and the diff a
person reviews is not the diff that was checked. So the argv is *built here*
from a fixed head, never composed out of configuration, and no input reaches
the part of it that could turn on a fix. A test asserts that over every request
the builder accepts rather than over one example.

**It must never report unreadable output as a clean file.** Ruff exits 1 when
it finds violations, which is the tool working (#205 item 6), and exits 2 when
it could not do its job. But a third case sits between them: exit 1 with output
this parser cannot read. Returning an empty finding list there reports the code
as clean on the strength of not having understood the answer, and it is
indistinguishable from a genuinely clean run. That is :attr:`ParsedOutput.failed_to_parse`.

The project's own configuration is left alone. Ruff finds ``pyproject.toml`` or
``ruff.toml`` by walking up from the files it is given, and this passes no
``--config``, no ``--select`` and no ``--ignore``: a linter run against rules
other than the project's own answers a question nobody asked.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
from ici.execution.process import ExitContract, TaskOutcome

__all__ = [
    "FIXED_HEAD",
    "FORBIDDEN_OPTIONS",
    "RUFF_CONTRACT",
    "RuffProvider",
    "RuffRequest",
    "parse_ruff_json",
]

PROVIDER_NAME = "ruff"

#: Ruff's own convention, stated once. Exit 1 is "violations found", which is
#: an answer; exit 2 is "I could not run", which is not.
RUFF_CONTRACT = ExitContract(success=(0,), findings=(1,))

#: Everything after the executable and before the paths, fixed. This is the
#: stronger of the two guarantees below: because position 1 is always ``check``,
#: ``ruff format`` cannot be reached at all -- not by configuration, and not by
#: a path that happens to be named ``format``, which is a real directory name
#: and a legitimate thing to lint.
#:
#: ``--output-format json`` is what makes the result parseable at all, and
#: ``--quiet`` keeps the human summary out of the stream the parser reads.
FIXED_HEAD = ("check", "--output-format", "json", "--quiet")

#: Options that would let a lint run edit the tree, or replace the project's
#: rules with ours. Only options: a *path* is never dangerous, and listing
#: bare words here would mean refusing to lint a directory for its name.
#:
#: Cache options are not here. Where the cache goes changes nothing about what
#: is reported, and this module sets it deliberately so that the default writes
#: nothing into the tree being checked.
FORBIDDEN_OPTIONS = (
    "--fix",
    "--fix-only",
    "--unsafe-fixes",
    "--select",
    "--ignore",
    "--extend-select",
    "--extend-ignore",
    "--config",
    "--add-noqa",
)

_SEVERITY = "warning"
_CATEGORY = "lint"

#: The format-check head: ``ruff format --check`` is read-only — it reports
#: what *would* change and writes nothing, which is the only kind of format
#: check a verifier is allowed to run (#206 item 2, #215 item 1).
FORMAT_HEAD = ("format", "--check")


@dataclass(frozen=True)
class RuffRequest:
    """What to lint, and the only things a caller gets to choose.

    ``cache_dir`` defaults to None, which means ``--no-cache``. Left to itself
    Ruff writes ``.ruff_cache/`` into the tree it is checking, and a
    verification tool that writes into what it is verifying cannot run against
    a read-only checkout -- which #206 asks it to do. Nothing is reported
    differently either way; a caller that wants the speed back names a
    directory of its own.
    """

    executable: str
    project_root: Path
    targets: tuple[str, ...]
    component_id: str | None = None
    analysis_unit_id: str | None = None
    task_id: str = "python.lint.ruff"
    timeout_seconds: float = 300.0
    cache_dir: Path | None = None
    #: ``"lint"`` runs ``ruff check``; ``"format"`` runs the read-only
    #: ``ruff format --check``. The argv is the record of which ran — the
    #: parser reads it back rather than trusting a side channel.
    mode: str = "lint"
    #: Ruff also reads its configuration — pyproject/ruff.toml between the
    #: project root and the workspace root. Declared so the task's identity
    #: (#209) covers every file that could change the answer; a check whose
    #: inputs are not enumerable is one whose results cannot be reused.
    config_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ("lint", "format"):
            raise ValueError(f"unknown ruff mode {self.mode!r}")
        if not self.targets:
            raise ValueError("a lint request must name something to lint")
        for target in self.targets:
            if target.startswith("-"):
                # An option smuggled in as a path is how a fixed argv stops
                # being fixed.
                raise ValueError(f"{target!r} is an option, not a path to lint")


class RuffProvider:
    """The Ruff lint provider."""

    name = PROVIDER_NAME

    def plan(self, request: RuffRequest) -> ProviderPlan:
        """Build the one command this provider is allowed to run."""

        cache = (
            ("--cache-dir", str(request.cache_dir))
            if request.cache_dir is not None
            else ("--no-cache",)
        )
        head = FORMAT_HEAD if request.mode == "format" else FIXED_HEAD
        argv = (request.executable, *head, *cache, *request.targets)
        task = TaskSpec(
            id=request.task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=argv,
            cwd=str(request.project_root),
            input_refs=(*request.targets, *request.config_files),
            analysis_unit_ids=((request.analysis_unit_id,) if request.analysis_unit_id else ()),
            timeout_seconds=request.timeout_seconds,
        )
        return ProviderPlan(task=task, contract=RUFF_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Read Ruff's output, or say that it could not be read."""

        root = outcome.spec.cwd or Path.cwd()
        argv = outcome.spec.argv
        if len(argv) > 1 and argv[1] == "format":
            return parse_ruff_format(outcome.parseable, root=root, task_id=outcome.spec.name)
        return parse_ruff_json(outcome.parseable, root=root, task_id=outcome.spec.name)


def parse_ruff_json(text: str, root: Path, task_id: str = "") -> ParsedOutput:
    """Turn Ruff's JSON array into findings, refusing anything else.

    An empty stream is a real answer -- Ruff prints ``[]`` when it finds
    nothing, and with ``--quiet`` a clean run can print nothing at all. What is
    refused is output that is *present and unreadable*, because that is the
    case that otherwise passes for clean.

    ``root`` is needed because Ruff reports absolute paths and a
    :class:`~ici.domain.finding.SourceSpan` is relative to its component. The
    first version of this took the paths as they came and every unit test
    passed, because the fixtures were written by hand with relative paths; the
    first run against the real tool failed on all of them.
    """

    stripped = text.strip()
    if not stripped:
        return ParsedOutput()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as error:
        return ParsedOutput(failed_to_parse=f"ruff output was not JSON: {error}")
    if not isinstance(payload, list):
        return ParsedOutput(
            failed_to_parse=f"ruff output was {type(payload).__name__}, expected a list"
        )

    findings = []
    outside: list[str] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            return ParsedOutput(
                failed_to_parse=f"ruff entry {index} was {type(item).__name__}, expected an object"
            )
        try:
            findings.append(_finding(item, root, task_id))
        except _OutsideTheRoot as error:
            # A real violation in a file this component does not own. Not a
            # parse failure -- the output was perfectly readable -- but not
            # something to drop silently either, so it is named.
            outside.append(str(error))
        except (KeyError, TypeError, ValueError) as error:
            return ParsedOutput(failed_to_parse=f"ruff entry {index} was not readable: {error}")
    return ParsedOutput(
        findings=tuple(findings),
        limitations=tuple(f"outside the component: {item}" for item in outside),
    )


class _OutsideTheRoot(Exception):
    """A finding whose file is not under the root being analysed."""


def _finding(item: dict[str, object], root: Path, task_id: str) -> Finding:
    code = _text(item, "code") or "RUFF"
    message = _text(item, "message") or "(no message)"
    filename = _relative(_text(item, "filename"), root)
    location = item.get("location")
    line, column = _position(location)
    end_line, end_column = _position(item.get("end_location"))

    return Finding(
        fingerprint=_fingerprint(code, filename, line, column, message),
        rule_id=f"ruff.{code}",
        native_rule_id=code,
        message=message,
        severity=_SEVERITY,
        confidence="high",
        category=_CATEGORY,
        provider=PROVIDER_NAME,
        primary_location=SourceSpan(
            path=filename,
            start_line=line,
            start_column=column,
            end_line=end_line or None,
            end_column=end_column or None,
        ),
        task_id=task_id or None,
        evidence=EvidenceLevel.MEASURED,
    )


def _relative(filename: str, root: Path) -> str:
    """Ruff's absolute path as a path inside the component."""

    if not filename:
        raise ValueError("entry has no filename")
    try:
        return Path(filename).resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise _OutsideTheRoot(filename) from error
    except OSError as error:  # pragma: no cover - an unresolvable path
        raise ValueError(f"{filename} could not be resolved: {error}") from error


def _text(item: dict[str, object], key: str) -> str:
    value = item.get(key)
    return value if isinstance(value, str) else ""


def _position(value: object) -> tuple[int, int]:
    if not isinstance(value, dict):
        return 0, 0
    row = value.get("row")
    column = value.get("column")
    return (
        row if isinstance(row, int) and not isinstance(row, bool) else 0,
        column if isinstance(column, int) and not isinstance(column, bool) else 0,
    )


def _fingerprint(code: str, filename: str, line: int, column: int, message: str) -> str:
    # The path and the position are in it so two violations of the same rule in
    # one file stay distinct; the message is in it so a rule whose text carries
    # the specifics does not collapse them either.
    digest = hashlib.sha256(
        "\x00".join((PROVIDER_NAME, code, filename, str(line), str(column), message)).encode()
    ).hexdigest()
    return f"sha256:{digest}"


_FORMAT_HEADER_RE = re.compile(r"^unformatted:")
#: Older Ruff prints ``Would reformat: <path>`` — a finding on one line.
_FORMAT_LEGACY_RE = re.compile(r"^Would reformat:\s*(?P<path>.+)$")
_FORMAT_SPAN_RE = re.compile(r"^\s*-->\s*(?P<path>.+?):(?P<line>\d+):(?P<column>\d+)\s*$")
#: Diff bodies, gutters and the ``N files would be reformatted`` summary are
#: context, not findings — but nothing else may appear, because a line this
#: parser does not know is a line whose meaning was guessed at.
_FORMAT_CONTEXT_RE = re.compile(r"^\s*(\||[-+]|\d+\s*[-+|])")
_FORMAT_SUMMARY_RE = re.compile(r"^\d+ files?\b")


def parse_ruff_format(text: str, root: Path, task_id: str = "") -> ParsedOutput:
    """Turn ``ruff format --check`` output into one finding per file.

    ``--check`` answers which files would change; the per-file
    ``unformatted:``/``-->`` pairs and the legacy ``Would reformat:`` lines
    are the only finding carriers. Anything unrecognized is a parse failure —
    the same rule as the JSON path, because a half-read stream reporting zero
    files is indistinguishable from a formatted tree.
    """

    findings: list[Finding] = []
    seen: set[str] = set()
    pending_header = False

    def _add(path: str, line: int, column: int | None) -> None:
        try:
            # Relative spans are relative to the task's cwd — resolve them
            # there, not against wherever this process happens to sit.
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = root / candidate
            filename = _relative(str(candidate), root)
        except (_OutsideTheRoot, ValueError):
            # A file outside this component is context, not a finding here.
            return
        if filename in seen:
            return
        seen.add(filename)
        message = "file would be reformatted"
        findings.append(
            Finding(
                fingerprint=_fingerprint("format", filename, line, column or 0, message),
                rule_id="ruff.format",
                message=message,
                severity="low",
                confidence="high",
                category="format",
                provider=PROVIDER_NAME,
                primary_location=SourceSpan(
                    path=filename,
                    start_line=line or 1,
                    start_column=column,
                ),
                task_id=task_id or None,
                evidence=EvidenceLevel.MEASURED,
            )
        )

    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            pending_header = False
            continue
        legacy = _FORMAT_LEGACY_RE.match(line)
        if legacy is not None:
            _add(legacy.group("path").strip(), 1, None)
            continue
        if _FORMAT_HEADER_RE.match(line):
            pending_header = True
            continue
        span = _FORMAT_SPAN_RE.match(line)
        if span is not None:
            if pending_header:
                _add(
                    span.group("path"),
                    int(span.group("line")),
                    int(span.group("column")),
                )
            pending_header = False
            continue
        if _FORMAT_CONTEXT_RE.match(line) or _FORMAT_SUMMARY_RE.match(line):
            continue
        return ParsedOutput(failed_to_parse=f"unrecognized ruff format line: {line.strip()!r}")
    return ParsedOutput(findings=tuple(findings))
