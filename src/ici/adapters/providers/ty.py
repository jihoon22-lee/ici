"""Running ty against a component's own Python files.

ty is the opt-in checker: a component names it with
``[python] type_provider = "ty"`` and gets ``ty check --output-format
concise`` over its declared sources — the ``path:line:col: severity[rule]
message`` stream this parser is written against. The full (default) format's
rendered context blocks are display, not data, so the concise head is part of
the fixed argv rather than an option.

The same two rules hold as for every provider: the project's own ty
configuration answers (no ``--config`` is passed), and output this parser
cannot read is :attr:`ParsedOutput.failed_to_parse`, never an empty PASS.
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

__all__ = ["TY_CONTRACT", "TyProvider", "parse_ty_output"]

PROVIDER_NAME = "ty"

#: 0 clean, 1 diagnostics found. Anything else is a failure to run.
TY_CONTRACT = ExitContract(success=(0,), findings=(1,))

#: ``path:line:col: severity[rule] message`` — ty's concise stream. The rule
#: bracket is optional because a few diagnostics carry no code.
_DIAGNOSTIC_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<severity>error|warning|info)"
    r"(?:\[(?P<code>[A-Za-z0-9_-]+)\])?\s*:?\s*(?P<message>.*)$"
)
_SUMMARY_RE = re.compile(r"^(Found \d+ diagnostics?|\s*$)")

_SEVERITY = {"error": "high", "warning": "medium", "info": "low"}


class TyProvider:
    """The ty provider — opt-in via ``type_provider = "ty"``."""

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        *,
        targets: tuple[str, ...],
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
        input_refs: tuple[str, ...] = (),
        tool_digest: str | None = None,
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(executable, "check", "--output-format", "concise", *targets),
            cwd=cwd,
            input_refs=input_refs,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=600,
            tool_digest=tool_digest,
        )
        return ProviderPlan(task=task, contract=TY_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Normalize ty's concise stream, or refuse what cannot be read."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        root = outcome.spec.cwd or Path.cwd()
        return parse_ty_output(outcome.parseable, root, task_id=outcome.spec.name)


def parse_ty_output(text: str, root: Path, task_id: str = "") -> ParsedOutput:
    """Turn ty's concise stream into findings, refusing anything else."""

    findings: list[Finding] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        diagnostic = _DIAGNOSTIC_RE.match(line)
        if diagnostic is not None:
            filename = _relative(diagnostic.group("path"), root)
            if Path(filename).is_absolute():
                continue
            message = diagnostic.group("message") or "(no message)"
            code = diagnostic.group("code") or ""
            findings.append(
                Finding(
                    fingerprint=_fingerprint(
                        code,
                        filename,
                        int(diagnostic.group("line")),
                        int(diagnostic.group("column")),
                        message,
                    ),
                    rule_id=f"ty.{code}" if code else "ty.diagnostic",
                    message=message,
                    severity=_SEVERITY.get(diagnostic.group("severity"), "medium"),
                    confidence="high",
                    primary_location=SourceSpan(
                        path=filename,
                        start_line=int(diagnostic.group("line")),
                        start_column=int(diagnostic.group("column")),
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
        return ParsedOutput(failed_to_parse=f"unrecognized ty output line: {line.strip()!r}")
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


def _fingerprint(code: str, filename: str, line: int, column: int, message: str) -> str:
    digest = hashlib.sha256(
        "\x00".join((PROVIDER_NAME, code, filename, str(line), str(column), message)).encode()
    ).hexdigest()
    return f"sha256:{digest}"
