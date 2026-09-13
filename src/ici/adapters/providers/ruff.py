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


@dataclass(frozen=True)
class RuffRequest:
    """What to lint, and the only things a caller gets to choose."""

    executable: str
    project_root: Path
    targets: tuple[str, ...]
    component_id: str | None = None
    analysis_unit_id: str | None = None
    task_id: str = "python.lint.ruff"
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
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

        argv = (request.executable, *FIXED_HEAD, *request.targets)
        task = TaskSpec(
            id=request.task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=argv,
            cwd=str(request.project_root),
            analysis_unit_ids=((request.analysis_unit_id,) if request.analysis_unit_id else ()),
            timeout_seconds=request.timeout_seconds,
        )
        return ProviderPlan(task=task, contract=RUFF_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Read Ruff's JSON, or say that it could not be read."""

        root = outcome.spec.cwd or Path.cwd()
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
