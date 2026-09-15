"""Running mypy against a component's own Python files.

Two rules, the same ones Ruff is held to:

**The project's own configuration answers.** No ``--config`` is passed and no
option overrides the project's rules: mypy reads ``mypy.ini``, ``.mypy.ini``,
``setup.cfg`` or ``pyproject.toml`` walking up from the files it is given,
which is exactly the configuration the project's own type check uses. The
fixed head only names output shape (``--show-error-codes``,
``--no-color-output``, ``--no-pretty``) so the stream stays parseable.

**Unreadable output is a parse failure, not a clean file.** mypy exits 0 on
clean, 1 on errors found, and 2 when it could not do its job — but exit 1
with output this parser cannot read is a third case, reported as
:attr:`ParsedOutput.failed_to_parse` rather than an empty finding list
(#205 item 6, #215 item 5).

The checker runs in ici's environment, analysing the component's declared
sources — it does not install into or import from the project's own virtual
environment (#215's separation rule: the checker's runtime and the analysed
target stay apart, and import resolution follows mypy's own rules for the
paths it is given).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["MYPY_CONTRACT", "MypyProvider", "parse_mypy_output"]

PROVIDER_NAME = "mypy"

#: 0 clean, 1 errors found — both answers. 2 is "could not run", which the
#: executor reports rather than parses.
MYPY_CONTRACT = ExitContract(success=(0,), findings=(1,))

#: ``path:line[:col]: severity: message [code]`` — mypy's stable text shape
#: since long before error codes existed. ``--no-pretty`` keeps the context
#: block out of this stream so no line here is ambiguous.
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))?:\s*"
    r"(?P<severity>error|warning|note):\s*(?P<message>.*?)"
    r"(?:\s+\[(?P<code>[A-Za-z0-9_-]+)\])?$"
)
#: Lines mypy adds that are part of the answer but carry no finding:
#: the totals and the clean summary.
_SUMMARY_RE = re.compile(r"^(Found \d+ errors?|Success: no issues found|Checked \d+|\s*$)")

_SEVERITY = {"error": "high", "warning": "medium", "note": "low"}


class MypyProvider:
    """The mypy provider — one invocation over the component's sources."""

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        *,
        targets: tuple[str, ...],
        cwd: str,
        task_id: str,
        cache_dir: str | None = None,
        analysis_unit_id: str = "",
        input_refs: tuple[str, ...] = (),
        tool_digest: str | None = None,
    ) -> ProviderPlan:
        # Left to itself mypy writes ``.mypy_cache`` into the tree it checks —
        # the same reason ruff gets ``--no-cache``. The caller names a cache
        # directory under .ici, or the module cache is disabled outright.
        cache = ("--cache-dir", cache_dir) if cache_dir else ("--no-incremental",)
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(
                executable,
                "--show-error-codes",
                "--no-color-output",
                "--no-pretty",
                *cache,
                *targets,
            ),
            cwd=cwd,
            input_refs=input_refs,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=600,
            tool_digest=tool_digest,
        )
        return ProviderPlan(task=task, contract=MYPY_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Normalize mypy's text output, or refuse what cannot be read."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        root = outcome.spec.cwd or Path.cwd()
        return parse_mypy_output(outcome.parseable, root, task_id=outcome.spec.name)


def parse_mypy_output(text: str, root: Path, task_id: str = "") -> ParsedOutput:
    """Turn mypy's text stream into findings, refusing anything else."""

    findings: list[Finding] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        diagnostic = _DIAGNOSTIC_RE.match(line)
        if diagnostic is not None:
            filename = _relative(diagnostic.group("path"), root)
            if Path(filename).is_absolute():
                # A diagnostic outside the workspace — a site-packages stub —
                # is mypy context, not a project finding.
                continue
            message = diagnostic.group("message") or "(no message)"
            code = diagnostic.group("code") or ""
            findings.append(
                Finding(
                    fingerprint=_fingerprint(
                        code,
                        filename,
                        int(diagnostic.group("line")),
                        diagnostic.group("column"),
                        message,
                    ),
                    rule_id=f"mypy.{code}" if code else "mypy.error",
                    message=message,
                    severity=_SEVERITY.get(diagnostic.group("severity"), "medium"),
                    confidence="high",
                    primary_location=SourceSpan(
                        path=filename,
                        start_line=int(diagnostic.group("line")),
                        start_column=(
                            int(diagnostic.group("column")) if diagnostic.group("column") else None
                        ),
                    ),
                    provider=PROVIDER_NAME,
                    native_rule_id=code,
                    task_id=task_id or None,
                    evidence=EvidenceLevel.MEASURED,
                )
            )
            continue
        if _SUMMARY_RE.match(line):
            continue
        return ParsedOutput(failed_to_parse=f"unrecognized mypy output line: {line.strip()!r}")
    return ParsedOutput(findings=tuple(findings))


def _relative(path: str, root: Path) -> str:
    """A finding path made workspace-relative when it points inside it.

    Relative spellings are anchored at the task's working directory — not at
    wherever this process happens to be running from.
    """

    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        return str(candidate.resolve().relative_to(root))
    except (OSError, ValueError):
        return path


def _fingerprint(code: str, filename: str, line: int, column: str | None, message: str) -> str:
    digest = hashlib.sha256(
        "\x00".join((PROVIDER_NAME, code, filename, str(line), str(column), message)).encode()
    ).hexdigest()
    return f"sha256:{digest}"
