"""Running a C++ build's own test suites — ctest and QTest.

The binaries are the build's, not ici's: ``ctest --test-dir`` reads the
``CTestTestfile`` the project's configure generated, and a QTest target runs
as the executable the build produced. What ici fixes is only what the parser
needs — ``--output-on-failure`` for ctest, nothing at all for QTest, whose
plain-text protocol is already stable.

Outcome shape: a suite's exit code carries the failure count (ctest reports
8 on failures, a QTest binary returns the number of failed cases), so every
non-zero exit in range is findings-class — but only when the output actually
shows test verdicts. A run that produced none is a parse failure, and "No
tests were found" is too: a suite that ran nothing has not verified anything
(#219's zero-test and missing-suite cases stay INCOMPLETE, never PASS).
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
from ici.execution.process import ExitContract, TaskOutcome
from ici.execution.process import TaskSpec as ExecTaskSpec

__all__ = ["CtestProvider", "QtestProvider"]

#: ctest: 0 all passed, 8 failures — and a QTest binary: the failure count.
#: Any of them is an answer; what the parse cannot find a verdict in is not.
TEST_CONTRACT = ExitContract(success=(0,), findings=tuple(range(1, 256)))

#: ``    Test #3: suite_name ..............***Failed    0.02 sec``
_CTEST_RE = re.compile(r"Test\s+#\d+:\s+(?P<name>\S+)\s*\.{3,}\s*(?P<verdict>\*+\S+|Passed)")
_CTEST_ZERO = re.compile(r"No tests were found", re.IGNORECASE)

#: QTest text: ``PASS   : Class::case()`` / ``FAIL!  : Class::case() msg``
_QTEST_RE = re.compile(
    r"^(?P<verdict>PASS|FAIL!|XFAIL|XPASS|SKIP)\s*:\s*(?P<node>\S+)", re.MULTILINE
)
_QTEST_LOC = re.compile(r"Loc:\s*\[(?P<path>[^:\]]+)\((?P<line>\d+)\)\]")
_QTEST_TOTALS = re.compile(r"Totals:\s*(?P<passed>\d+)\s+passed,\s*(?P<failed>\d+)\s+failed")


class CtestProvider:
    """One ``ctest --test-dir`` run per build that declares tests."""

    name = "ctest"

    def plan(
        self,
        executable: str,
        *,
        build_dir: str,
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(executable, "--test-dir", build_dir, "--output-on-failure"),
            cwd=cwd,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=1800,
            # A test verdict depends on rebuilt binaries the declared inputs
            # do not enumerate — a cached verdict could describe stale builds.
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=TEST_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        return _parse_ctest(outcome.parseable, outcome.spec)


class QtestProvider:
    """One QTest binary per suite — the binary the build produced."""

    name = "qtest"

    def plan(
        self,
        *,
        binary: str,
        cwd: str,
        task_id: str,
        analysis_unit_id: str = "",
    ) -> ProviderPlan:
        task = TaskSpec(
            id=task_id,
            kind=TaskKind.ANALYZE,
            provider=self.name,
            argv=(binary,),
            cwd=cwd,
            analysis_unit_ids=(analysis_unit_id,) if analysis_unit_id else (),
            timeout_seconds=1800,
            cacheable=False,
        )
        return ProviderPlan(task=task, contract=TEST_CONTRACT)

    def parse(self, outcome: TaskOutcome) -> ParsedOutput:
        if not outcome.outcome.ran_to_completion:
            return ParsedOutput(failed_to_parse=f"{outcome.spec.name} did not finish")
        return _parse_qtest(outcome.parseable, outcome.spec)


def _parse_ctest(text: str, spec: ExecTaskSpec) -> ParsedOutput:
    findings: list[Finding] = []
    total = 0
    passed = 0
    for match in _CTEST_RE.finditer(text):
        total += 1
        verdict = match.group("verdict")
        if verdict == "Passed":
            passed += 1
            continue
        name = match.group("name")
        findings.append(
            Finding(
                fingerprint=_fingerprint(spec.name, name, verdict),
                rule_id="ctest.failed",
                message=f"test {name}: {verdict.lstrip('*')}",
                severity="high",
                confidence="high",
                primary_location=SourceSpan(path=_test_source(name, spec.cwd), start_line=1),
                provider="ctest",
                task_id=spec.name,
                evidence=EvidenceLevel.MEASURED,
            )
        )
    if not total:
        if _CTEST_ZERO.search(text):
            return ParsedOutput(failed_to_parse="ctest found no tests")
        return ParsedOutput(failed_to_parse="ctest output held no test verdicts")
    return ParsedOutput(
        findings=tuple(findings),
        measurements=(
            Measurement(
                name="ctest.cases",
                value=float(total),
                numerator=passed,
                denominator=total,
            ),
        ),
    )


def _parse_qtest(text: str, spec: ExecTaskSpec) -> ParsedOutput:
    findings: list[Finding] = []
    total = passed = skipped = 0
    pending_loc: tuple[str, int] | None = None
    for line in text.splitlines():
        verdict_match = _QTEST_RE.match(line)
        if verdict_match:
            total += 1
            verdict = verdict_match.group("verdict")
            node = verdict_match.group("node")
            if verdict == "PASS":
                passed += 1
            elif verdict in ("SKIP", "XFAIL"):
                skipped += 1
            else:
                findings.append(
                    Finding(
                        fingerprint=_fingerprint(spec.name, node, verdict),
                        rule_id=f"qtest.{verdict.rstrip('!').lower()}",
                        message=f"{node} {verdict.rstrip('!').lower()}",
                        severity="high",
                        confidence="high",
                        primary_location=SourceSpan(
                            path=pending_loc[0] if pending_loc else _test_source(node, spec.cwd),
                            start_line=pending_loc[1] if pending_loc else 1,
                        ),
                        provider="qtest",
                        task_id=spec.name,
                        evidence=EvidenceLevel.MEASURED,
                    )
                )
            pending_loc = None
            continue
        loc_match = _QTEST_LOC.search(line)
        if loc_match and findings and pending_loc is None:
            path = loc_match.group("path")
            last = findings[-1]
            findings[-1] = Finding(
                fingerprint=last.fingerprint,
                rule_id=last.rule_id,
                message=last.message,
                severity=last.severity,
                confidence=last.confidence,
                primary_location=SourceSpan(path=path, start_line=int(loc_match.group("line"))),
                provider=last.provider,
                task_id=last.task_id,
                evidence=last.evidence,
            )
    if not total:
        totals = _QTEST_TOTALS.search(text)
        if totals and int(totals.group("passed")) == 0 and int(totals.group("failed")) == 0:
            return ParsedOutput(failed_to_parse="qtest binary ran no cases")
        return ParsedOutput(failed_to_parse="qtest output held no case verdicts")
    limitations = (
        f"no test ran green ({skipped} skipped of {total} cases)" if total and passed == 0 else ""
    )
    return ParsedOutput(
        findings=tuple(findings),
        measurements=(
            Measurement(
                name="qtest.cases",
                value=float(total),
                numerator=passed,
                denominator=total,
            ),
        ),
        limitations=tuple(item for item in (limitations,) if item),
    )


def _test_source(name: str, cwd: Path | None) -> str:
    """Map a suite/case name onto a file — the stem heuristic the legacy
    engine used, because neither ctest nor QTest prints a source path."""

    stem = name.split("::")[0].split("/")[-1]
    root = cwd or Path.cwd()
    try:
        candidates = sorted(root.rglob(f"{stem}*.cpp")) or sorted(root.rglob(f"*{stem}*.cpp"))
    except (OSError, ValueError):
        candidates = []
    if candidates:
        return candidates[0].relative_to(root).as_posix()
    # No source file matches the suite name — the name itself is still the
    # truest location, and a bare stem is a valid contained path.
    return stem or "test-suite"


def _fingerprint(task_id: str, node: str, verdict: str) -> str:
    digest = hashlib.sha256("\x00".join((task_id, node, verdict)).encode()).hexdigest()
    return f"sha256:{digest}"
