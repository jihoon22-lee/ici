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
    if any(
        r.required
        and (
            r.status in (EngineStatus.ERROR, EngineStatus.SKIP)
            or r.evidence == EvidenceState.NOT_RUN
        )
        for r in results
    ):
        return EngineStatus.ERROR
    if any(r.required and r.status == EngineStatus.FAIL for r in results):
        return EngineStatus.FAIL
    if any(
        r.status == EngineStatus.WARN
        or (
            not r.required
            and (r.status != EngineStatus.PASS or r.evidence != EvidenceState.MEASURED)
        )
        for r in results
    ):
        return EngineStatus.WARN
    return EngineStatus.PASS


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
        return sum(1 for r in self.results if r.status in (EngineStatus.FAIL, EngineStatus.ERROR))

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
