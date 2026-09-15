"""Reading gcov evidence after the shared instrumented run.

The data is not this check's to produce: ``cpp.test`` already ran the
instrumented binaries, whose ``.gcda`` landed beside the build's ``.gcno``.
This task only reads — ``gcov --json-format`` over the notes the build left —
and the report is verified before it counts: ``GcovJsonError`` is a parse
failure, a run that wrote no report at all is a parse failure, and totals
that disagree with their parts never become a 0% verdict (#219's stale /
mismatched evidence case).

gcov writes its reports where it runs, so the task's cwd is a directory under
``.ici`` — the project's build tree is read, not written to.
"""

from __future__ import annotations

from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.engines.gcov_json import GcovJsonError, parse_gcov_json_file
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["GCOV_CONTRACT", "GcovProvider"]

PROVIDER_NAME = "gcov"

#: gcov exits 0 when it produced reports; anything else — bad args, unreadable
#: notes — is a failure to answer, not a coverage verdict.
GCOV_CONTRACT = ExitContract(success=(0,), findings=())


class GcovProvider:
    """Turn ``.gcno``/``.gcda`` pairs into measured line and branch counts."""

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        *,
        gcno_files: tuple[str, ...],
        work_dir: str,
        task_id: str,
        analysis_unit_id: str = "",
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(executable, "--json-format", "-b", "-p", *gcno_files),
            cwd=work_dir,
            output_specs=("*.gcov.json.gz",),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=900,
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=GCOV_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Aggregate every report the run wrote, or refuse the run."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        work_dir = Path(outcome.spec.cwd) if outcome.spec.cwd else Path.cwd()
        reports = sorted(work_dir.glob("*.gcov.json.gz"))
        if not reports:
            return ParsedOutput(
                failed_to_parse="gcov produced no reports — the .gcda are "
                "absent or the binaries were never instrumented"
            )
        covered_lines = total_lines = 0
        covered_branches = total_branches = 0
        limitations: list[str] = []
        for report_path in reports:
            try:
                report = parse_gcov_json_file(report_path)
            except GcovJsonError as exc:
                return ParsedOutput(failed_to_parse=f"gcov evidence rejected ({exc.code}): {exc}")
            except OSError as exc:
                return ParsedOutput(failed_to_parse=f"gcov report unreadable: {exc}")
            for gcov_file in report.files:
                for line in gcov_file.lines:
                    total_lines += 1
                    covered_lines += 1 if line.count > 0 else 0
                    for branch in line.branches:
                        total_branches += 1
                        covered_branches += 1 if branch.count > 0 else 0
        if not total_lines:
            return ParsedOutput(failed_to_parse="gcov reports held no line data")
        if "stamp mismatch" in outcome.stderr or "version mismatch" in outcome.stderr:
            limitations.append("gcov reported stale instrumentation (.gcda/.gcno mismatch)")
        return ParsedOutput(
            measurements=(
                Measurement(
                    name="coverage.cpp.lines",
                    value=round(covered_lines / total_lines * 100.0, 1),
                    unit="%",
                    numerator=covered_lines,
                    denominator=total_lines,
                    evidence=EvidenceLevel.MEASURED,
                ),
                Measurement(
                    name="coverage.cpp.branches",
                    value=(
                        round(covered_branches / total_branches * 100.0, 1)
                        if total_branches
                        else 0.0
                    ),
                    unit="%",
                    # A denominator of zero is a refused pair, not "zero
                    # branches measured" — emit the bare value instead.
                    **(
                        {"numerator": covered_branches, "denominator": total_branches}
                        if total_branches
                        else {}
                    ),
                    evidence=EvidenceLevel.MEASURED,
                ),
            ),
            limitations=tuple(limitations),
        )
