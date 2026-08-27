"""Core Domain Models for ici."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EngineStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    ERROR = "ERROR"
    SKIP = "SKIP"


class EvidenceState(str, Enum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    NOT_RUN = "NOT_RUN"
    # The engine does not apply to this project at all — it analyses a language
    # the project does not contain. Distinct from NOT_RUN, which means the
    # engine should have run and could not. Conflating the two is what made a
    # C++-only project unable to reach a green gate: dead only reads Python, so
    # it skipped, and a required engine that skips escalated the whole suite.
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class ToolEvidence:
    """Records the tool invocation that produced an engine result."""

    name: str
    path: str
    version: str = ""
    argv: list[str] = field(default_factory=list)
    returncode: int | None = None
    timed_out: bool = False
    truncated: bool = False
    error: str = ""


@dataclass
class InspectionTarget:
    """Represents a specific inspected source file location, symbol, or violation."""

    file_path: str  # Relative file path (e.g., src/cyberpunk_sim/engine.py)
    start_line: int  # 1-indexed start line
    end_line: int | None = None  # 1-indexed end line (inclusive)
    target_name: str = ""  # Function/class/token/rule name
    status: EngineStatus = EngineStatus.PASS
    message: str = ""  # Detailed message or rule description
    snippet: str = ""  # Associated code snippet
    metrics: dict[str, Any] = field(default_factory=dict)  # e.g., complexity score, lines


@dataclass
class EngineResult:
    """Structured result returned by each verification engine."""

    engine_name: str
    status: EngineStatus
    summary: str
    score: float | None = None
    max_score: float | None = None
    duration: float = 0.0
    targets: list[InspectionTarget] = field(default_factory=list)
    raw_output: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    required: bool = True
    evidence: EvidenceState = EvidenceState.MEASURED
    tool_evidence: list[ToolEvidence] = field(default_factory=list)


def aggregate_suite_status(results: list[EngineResult]) -> EngineStatus:
    """Aggregates engine results into the suite gate status."""
    if not results:
        return EngineStatus.ERROR
    # A required engine that could not verify anything blocks the gate — except
    # when it never applied in the first place. "This project has no Python for
    # the dead-code engine to read" is not a verification failure, and treating
    # it as one left C++-only projects permanently red no matter how good the
    # code was.
    if any(
        r.required
        and r.evidence != EvidenceState.NOT_APPLICABLE
        and (
            r.status in (EngineStatus.ERROR, EngineStatus.SKIP)
            or r.evidence == EvidenceState.NOT_RUN
        )
        for r in results
    ):
        return EngineStatus.ERROR
    if any(r.required and r.status == EngineStatus.FAIL for r in results):
        return EngineStatus.FAIL
    # Same reasoning for the warning tier: an inapplicable engine is not a
    # warning about anything, so it must not colour the suite either.
    if any(
        r.evidence != EvidenceState.NOT_APPLICABLE
        and (
            r.status == EngineStatus.WARN
            or (
                not r.required
                and (r.status != EngineStatus.PASS or r.evidence != EvidenceState.MEASURED)
            )
        )
        for r in results
    ):
        return EngineStatus.WARN
    return EngineStatus.PASS


def gate_reason(results: list[EngineResult], suite_status: EngineStatus) -> str:
    """Explain, in one line, why the suite landed on this status.

    The console prints a Pass/Warn/Fail/Error tally, but the suite status comes
    from :func:`aggregate_suite_status`, which applies a different rule: a
    required engine that skipped or never ran escalates everything. So a report
    could read "Error: 0" while the suite was ERROR, and nothing on screen said
    why. This mirrors that rule so the verdict explains itself.
    """
    for result in results:
        if not result.required or result.evidence == EvidenceState.NOT_APPLICABLE:
            continue
        if result.evidence == EvidenceState.NOT_RUN:
            return f"required engine '{result.engine_name}' did not run (evidence NOT_RUN)"
        if result.status in (EngineStatus.ERROR, EngineStatus.SKIP):
            return f"required engine '{result.engine_name}' reported {result.status.value}"

    failed = [r for r in results if r.required and r.status == EngineStatus.FAIL]
    if failed:
        return f"required engine '{failed[0].engine_name}' failed"

    warned = [r for r in results if r.status == EngineStatus.WARN]
    if warned:
        names = ", ".join(r.engine_name for r in warned[:3])
        suffix = "" if len(warned) <= 3 else f" (+{len(warned) - 3} more)"
        return f"{len(warned)} engine(s) warned: {names}{suffix}"

    if suite_status == EngineStatus.PASS:
        return "all applicable engines passed"
    return "see engine results"


@dataclass
class VerificationSuiteResult:
    """Combined results across all executed engines."""

    suite_status: EngineStatus
    results: list[EngineResult]
    duration: float = 0.0
    tem_score: float | None = None
    max_tem_score: float = 5.0

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.status == EngineStatus.PASS)

    @property
    def warned_count(self) -> int:
        return sum(1 for r in self.results if r.status == EngineStatus.WARN)

    @property
    def failed_count(self) -> int:
        """Return legacy FAIL+ERROR count for backward-compatible consumers."""
        return sum(1 for r in self.results if r.status in (EngineStatus.FAIL, EngineStatus.ERROR))

    @property
    def error_count(self) -> int:
        """Returns the number of engines that could not produce a result."""
        return sum(1 for r in self.results if r.status == EngineStatus.ERROR)

    @property
    def skipped_count(self) -> int:
        """Returns the number of engines that were intentionally not run."""
        return sum(1 for r in self.results if r.status == EngineStatus.SKIP)

    @property
    def total_count(self) -> int:
        return len(self.results)


def format_score_display(res: EngineResult) -> str:
    """Formats formatted score string or metrics summary."""
    if res.score is not None:
        if res.max_score is not None:
            return f"{res.score:.2f} / {res.max_score:.2f}"
        return f"{res.score:.2f}"
    if "metrics_summary" in res.extra:
        return str(res.extra["metrics_summary"])
    return "-"


def exit_code_for_status(status: EngineStatus) -> int:
    """Maps a result status to the stable command-line exit contract."""
    if status in (EngineStatus.FAIL, EngineStatus.ERROR):
        return 1
    if status == EngineStatus.SKIP:
        return 2
    return 0
