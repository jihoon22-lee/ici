"""clang-tidy over the compile database, once for the whole component.

Why ``-p`` and not a flag transform: ``clang-tidy -p <build>`` reads the
compilation database itself and applies *each file's own* recorded
invocation. That is the strongest fidelity the run can claim — the analyzer
sees the TU exactly as the build compiled it, per file, without this module
opining on which flags matter (the alternative transform in
:mod:`ici.adapters.providers.compiler` exists for the compiler itself, where
no database replay exists).

The provider runs once per component rather than once per TU because
clang-tidy's own batching is the honest unit: it parallelizes internally,
deduplicates header diagnostics the way the tool means to, and reports a
component-scope summary ici's per-TU loop would have to reconstruct.

The check still gates on the coverage evidence: a partial database means a
partial analysis, and ``cpp.compile`` is what says so — ``cpp.tidy`` declares
``needs = compile-inputs`` so a run that skipped the coverage check never
gets to claim the analysis either.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
from ici.engines._cpp_diagnostics import parse_clang_tidy_diagnostics
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["TIDY_CONTRACT", "ClangTidyProvider"]

PROVIDER_NAME = "clang-tidy"

#: clang-tidy exits 0 with warnings printed — findings live in the stream,
#: not the code. A nonzero exit means it could not analyze, which is not an
#: answer.
TIDY_CONTRACT = ExitContract(success=(0,))


class ClangTidyProvider:
    """``clang-tidy -p <build> <covered sources>`` — the DB replays itself."""

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        *,
        database_dir: str,
        sources: tuple[str, ...],
        task_id: str,
        cwd: str,
        analysis_unit_id: str = "",
        input_refs: tuple[str, ...] = (),
        tool_digest: str | None = None,
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(executable, "-p", database_dir, *sources),
            cwd=cwd,
            input_refs=input_refs,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=900,
            tool_digest=tool_digest,
            # Same honesty bound as the compiler provider: transitive headers
            # are read but not identity-tracked, so reuse stays off.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=TIDY_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Read clang-tidy's diagnostics through the shared normalizer."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        root = outcome.spec.cwd or Path.cwd()
        parsed = parse_clang_tidy_diagnostics(root, root, outcome.stdout, outcome.stderr)
        if parsed.error:
            return ParsedOutput(failed_to_parse=parsed.error)
        findings: list[Finding] = []
        for diagnostic in parsed.diagnostics:
            target = diagnostic.target
            filename = _relative(target.file_path, root)
            if not filename or Path(filename).is_absolute():
                continue
            message = target.message or "(no message)"
            rule = diagnostic.tool_rule_id or "diagnostic"
            findings.append(
                Finding(
                    fingerprint=_fingerprint(
                        rule, filename, target.start_line, target.start_column, message
                    ),
                    rule_id=f"clang_tidy.{rule}",
                    message=message,
                    severity="medium",
                    confidence="high",
                    primary_location=SourceSpan(
                        path=filename,
                        start_line=target.start_line,
                        start_column=target.start_column,
                    ),
                    provider=self.name,
                    native_rule_id=diagnostic.tool_rule_id,
                    task_id=outcome.spec.name,
                    evidence=EvidenceLevel.MEASURED,
                )
            )
        return ParsedOutput(findings=tuple(findings))


def _relative(path: str, root: Path) -> str:
    """A finding path made workspace-relative when it points inside it."""

    try:
        return str(Path(path).resolve().relative_to(root))
    except (OSError, ValueError):
        return path


def _fingerprint(rule: str, filename: str, line: int, column: int | None, message: str) -> str:
    digest = hashlib.sha256(
        "\x00".join((PROVIDER_NAME, rule, filename, str(line), str(column), message)).encode()
    ).hexdigest()
    return f"sha256:{digest}"
