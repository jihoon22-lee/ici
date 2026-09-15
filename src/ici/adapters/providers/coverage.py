"""Collecting coverage.py's data after the shared pytest run.

The run is not this check's to repeat: ``python.test`` already executed the
suite — wrapped in ``coverage run`` when this check is selected — and the DAG
orders this task after it through the declared ``test-evidence`` input (#216
item 4: one execution, two readers). What remains here is only the read:
``coverage json`` turns the data file into per-file line and branch counts.

The evidence is verified, not trusted: ``parse_coverage_json`` refuses a file
whose totals disagree with their parts, whose file set is not the declared
source scope, or that simply is not coverage JSON — and an absent or
unreadable report is :attr:`ParsedOutput.failed_to_parse`, never a zero-coverage
verdict nor a silent pass.
"""

from __future__ import annotations

from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.engines.coverage_support import parse_coverage_json
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["COVERAGE_CONTRACT", "CoverageProvider"]

PROVIDER_NAME = "coverage"

#: ``coverage json`` exits 0 when the report was written; anything else means
#: the data file was absent, stale or unreadable — a failure to answer, not
#: a coverage verdict.
COVERAGE_CONTRACT = ExitContract(success=(0,), findings=())


class CoverageProvider:
    """Turn the shared run's coverage data into measured counts."""

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        *,
        data_file: str,
        report_path: str,
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
        tool_digest: str | None = None,
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(
                executable,
                "-m",
                "coverage",
                "json",
                "--data-file",
                data_file,
                "-o",
                report_path,
            ),
            cwd=cwd,
            output_specs=(report_path,),
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=300,
            tool_digest=tool_digest,
            # The inputs are a file the previous task writes — there is no
            # honest identity to reuse against until it exists.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=COVERAGE_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Read the JSON the task was told to write, or refuse the run."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        argv = outcome.spec.argv
        report_path = Path(argv[-1])
        root = outcome.spec.cwd or Path.cwd()
        try:
            result = parse_coverage_json(report_path, root)
        except OSError:
            result = None
        if result is None:
            return ParsedOutput(
                failed_to_parse="coverage json was absent, unreadable or inconsistent"
            )
        totals = result["totals"]
        measurements = (
            Measurement(
                name="coverage.lines",
                value=totals["cover"] or 0.0,
                unit="%",
                numerator=totals["stmts"] - totals["miss"],
                denominator=totals["stmts"],
                evidence=EvidenceLevel.MEASURED,
            ),
            Measurement(
                name="coverage.branches",
                value=totals["branch_cover"] or 0.0,
                unit="%",
            ),
        )
        return ParsedOutput(measurements=measurements)
