"""Core Domain Models for ici."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EngineStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"


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
        return sum(1 for r in self.results if r.status == EngineStatus.FAIL)

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
