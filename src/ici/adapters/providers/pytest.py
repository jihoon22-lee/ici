"""Running the project's own pytest — and never a different interpreter's.

#216's first rule is that the tests run under the *project's* interpreter, with
the project's own plugins and configuration: the argv carries no ``-c``, no
``--rootdir`` override and no plugin list, because a test run against rules
other than the suite's own answers a question nobody asked. What ici fixes is
only what its parser needs — ``-v`` so each node's verdict is a line, and
``-p no:cacheprovider`` so the run writes no ``.pytest_cache`` into the tree
it is checking.

When the coverage check is also selected the *same* run is wrapped in
``coverage run`` — one pytest execution feeds test evidence to both checks,
which is #216 item 4's sharing rule. The checks stay distinct: this task
answers "did the suite pass", the coverage task answers "what did it reach",
and the DAG orders them through the declared ``test-evidence`` input.

Outcome shape: a finished run is read with the shared
``parse_pytest_outcomes`` — real assertion failures and collection errors
become findings at the node that failed, while a run that did not finish is
the executor's report, not a parse. Zero collected tests is exit 5, which the
contract does not treat as an answer: a suite that collected nothing has not
verified anything.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.domain.enums import EvidenceLevel, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Measurement
from ici.domain.tasks import TaskSpec
from ici.engines.test_output import parse_pytest_outcomes, pytest_node_location
from ici.execution.process import ExitContract, TaskOutcome

__all__ = ["PYTEST_CONTRACT", "PytestProvider"]

PROVIDER_NAME = "pytest"

#: 0 is a green suite, 1 is findings — both are answers. 2 (interrupted) is
#: an answer only when the interruption is a collection error, which the
#: parser can see on its own line; otherwise it is a failed run. 5 (nothing
#: collected) and 4 (usage error) are never answers.
PYTEST_CONTRACT = ExitContract(success=(0,), findings=(1, 2))

#: ``ERROR collecting path/to/test_x.py`` — collection failures never reach
#: the per-node stream; the banner line is decorated, so the match is made
#: after the rule/underline prefix, not at column zero.
_COLLECTION_RE = re.compile(
    r"^[=\s_]*(?:ERROR|FAILED)\s+(?:collecting\s+)?(?P<path>\S+\.py)",
    re.MULTILINE,
)


class PytestProvider:
    """One pytest invocation per component, over its declared test scope."""

    name = PROVIDER_NAME

    def plan(
        self,
        executable: str,
        *,
        targets: tuple[str, ...],
        cwd: str,
        task_id: str,
        coverage_data: str | None = None,
        analysis_unit_id: str = "",
        input_refs: tuple[str, ...] = (),
        tool_digest: str | None = None,
    ) -> ProviderPlan:
        pytest_argv = (
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-v",
            "--tb=short",
            *targets,
        )
        argv = (
            (
                executable,
                "-m",
                "coverage",
                "run",
                # Branch coverage is part of the evidence contract — without it
                # the totals carry no branch counts to verify.
                "--branch",
                "--data-file",
                coverage_data,
                *pytest_argv,
            )
            if coverage_data is not None
            else (executable, *pytest_argv)
        )
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=argv,
            cwd=cwd,
            input_refs=input_refs,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=1800,
            tool_digest=tool_digest,
            # Test outcomes depend on installed packages and conftest state the
            # declared inputs do not enumerate — a cached verdict could report
            # a suite that no longer exists.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=PYTEST_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        """Normalize per-node verdicts, or say that the stream was not one."""

        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        root = outcome.spec.cwd or Path.cwd()
        return _parse(outcome.parseable, root, task_id=outcome.spec.name)


def _parse(text: str, root: Path, task_id: str) -> ParsedOutput:
    findings: list[Finding] = []
    limitations: list[str] = []
    outcomes = parse_pytest_outcomes(text)
    for nodeid, verdict in outcomes.items():
        if verdict not in ("FAILED", "ERROR"):
            continue
        path, line = pytest_node_location(nodeid, root)
        findings.append(
            Finding(
                fingerprint=_fingerprint(nodeid, verdict),
                rule_id=f"pytest.{verdict.lower()}",
                message=f"{nodeid} {verdict.lower()}",
                severity="high",
                confidence="high",
                primary_location=SourceSpan(path=path, start_line=line),
                provider=PROVIDER_NAME,
                task_id=task_id or None,
                evidence=EvidenceLevel.MEASURED,
            )
        )
    for match in _COLLECTION_RE.finditer(text):
        source = match.group("path")
        filename = _relative(source, root)
        if Path(filename).is_absolute():
            continue
        findings.append(
            Finding(
                fingerprint=_fingerprint(f"collect:{source}", "error"),
                rule_id="pytest.collection-error",
                message=f"collection error in {filename}",
                severity="high",
                confidence="high",
                primary_location=SourceSpan(path=filename, start_line=1),
                provider=PROVIDER_NAME,
                task_id=task_id or None,
                evidence=EvidenceLevel.MEASURED,
            )
        )
    total = len(outcomes)
    passed = sum(1 for verdict in outcomes.values() if verdict == "PASSED")
    skipped = sum(1 for verdict in outcomes.values() if verdict == "SKIPPED")
    measurements = (
        (
            Measurement(
                name="pytest.cases",
                value=float(total),
                numerator=passed,
                denominator=total,
            ),
        )
        if total
        else ()
    )
    if total and passed == 0:
        # Every collected case skipped, xfailed or errored — nothing verified.
        limitations.append(f"no test ran green ({skipped} skipped of {total} collected)")
    if not outcomes and not findings:
        # A findings-class exit with no evidence in it is not a quiet pass.
        if "Interrupted" in text or "errors during collection" in text:
            return ParsedOutput(failed_to_parse="pytest was interrupted before reporting")
        if "no tests ran" in text:
            limitations.append("pytest collected no tests")
    return ParsedOutput(
        findings=tuple(findings),
        measurements=measurements,
        limitations=tuple(limitations),
    )


def _relative(path: str, root: Path) -> str:
    """A finding path made workspace-relative when it points inside it."""

    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        return str(candidate.resolve().relative_to(root))
    except (OSError, ValueError):
        return path


def _fingerprint(nodeid: str, verdict: str) -> str:
    digest = hashlib.sha256("\x00".join((PROVIDER_NAME, nodeid, verdict)).encode()).hexdigest()
    return f"sha256:{digest}"
