"""Core Domain Models for ici."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ici.core.capabilities import CapabilityInventory
    from ici.core.context import AnalysisContext, ArtifactManifest


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


class FindingCategory(str, Enum):
    """Stable, tool-independent grouping used by the v3 finding contract."""

    CORRECTNESS = "correctness"
    TYPE = "type"
    SECURITY = "security"
    RESOURCE = "resource"
    BUILD = "build"
    TEST = "test"
    MAINTAINABILITY = "maintainability"
    ARCHITECTURE = "architecture"
    COMPATIBILITY = "compatibility"


class FindingSeverity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingConfidence(str, Enum):
    EXACT = "exact"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SuppressionKind(str, Enum):
    NONE = "none"
    INLINE = "inline"
    CONFIG = "config"
    BASELINE = "baseline"


class DeltaState(str, Enum):
    """Relationship between one current finding and the selected baseline."""

    NEW = "new"
    UNCHANGED = "unchanged"
    MOVED = "moved"
    RESOLVED = "resolved"


class SupportLanguage(str, Enum):
    """Source-language scopes declared by analysis engines."""

    PYTHON = "python"
    CPP = "cpp"


class AnalysisMode(str, Enum):
    """How an engine obtains a result for one language scope."""

    EXACT = "exact"
    HEURISTIC = "heuristic"
    TOOL_BACKED = "tool-backed"
    UNSUPPORTED = "unsupported"


@dataclass
class EngineSupport:
    """Declared and observed support for one engine/language pair."""

    engine_name: str
    language: SupportLanguage
    mode: AnalysisMode
    active_mode: AnalysisMode | None
    applicable: bool
    enabled: bool
    evidence: EvidenceState
    confidence: FindingConfidence
    frameworks: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    optional_tools: list[str] = field(default_factory=list)
    fallback_mode: AnalysisMode | None = None
    limitations: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class SupportMatrix:
    """Project scope and every engine's evaluated language support."""

    project_languages: list[SupportLanguage] = field(default_factory=list)
    project_frameworks: list[str] = field(default_factory=list)
    entries: list[EngineSupport] = field(default_factory=list)


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
    start_column: int | None = None  # 1-indexed start column
    end_column: int | None = None  # 1-indexed end column (inclusive)


@dataclass
class SourceLocation:
    """A canonical project-relative source region."""

    path: str
    start_line: int
    end_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None
    label: str = ""


@dataclass
class FindingMetric:
    """A numeric finding measurement with an explicit unit."""

    value: int | float
    unit: str = ""


@dataclass
class FindingSuppression:
    """Describes whether and why a finding is suppressed."""

    suppressed: bool = False
    kind: SuppressionKind = SuppressionKind.NONE
    reason: str = ""


@dataclass
class Finding:
    """Stable v3 issue/inventory record shared by every engine and reporter."""

    rule_id: str
    category: FindingCategory
    severity: FindingSeverity
    confidence: FindingConfidence
    fingerprint: str
    primary_location: SourceLocation
    message: str
    related_locations: list[SourceLocation] = field(default_factory=list)
    explanation: str = ""
    remediation: str = ""
    tool_rule_id: str = ""
    tool_name: str = ""
    tool_version: str = ""
    suppression: FindingSuppression = field(default_factory=FindingSuppression)
    metrics: dict[str, FindingMetric] = field(default_factory=dict)
    snippet: str = ""


@dataclass
class AnalysisMetadata:
    """Compatibility identity embedded in every baseline-capable v3 report."""

    producer_version: str
    fingerprint_version: str
    policy_digest: str
    tool_policy_digest: str


@dataclass
class FindingDelta:
    """Compact, deterministic comparison record for one finding occurrence."""

    state: DeltaState
    engine_name: str
    fingerprint: str
    rule_id: str
    message: str
    current_location: SourceLocation | None = None
    baseline_location: SourceLocation | None = None
    current_severity: FindingSeverity | None = None
    baseline_severity: FindingSeverity | None = None
    regressed: bool = False
    suppressed: bool = False
    gated: bool = False


@dataclass
class BaselineComparison:
    """Full inventory classification plus the optional PR gate decision."""

    source_path: str
    entries: list[FindingDelta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    baseline_metadata: AnalysisMetadata | None = None
    fail_on_new: bool = False
    gate_failed: bool = False

    def count(self, state: DeltaState) -> int:
        return sum(1 for entry in self.entries if entry.state == state)

    @property
    def regressed_count(self) -> int:
        return sum(1 for entry in self.entries if entry.regressed)

    @property
    def gated_count(self) -> int:
        return sum(1 for entry in self.entries if entry.gated)


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
    findings: list[Finding] = field(default_factory=list)
    support_matrix: SupportMatrix | None = None
    artifact_manifests: tuple[ArtifactManifest, ...] = ()


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


def gate_reason(
    results: list[EngineResult],
    suite_status: EngineStatus,
    baseline: BaselineComparison | None = None,
) -> str:
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

    if baseline is not None and baseline.gate_failed:
        return f"baseline gate found {baseline.gated_count} new or regressed actionable finding(s)"

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
    support_matrix: SupportMatrix | None = None
    analysis_metadata: AnalysisMetadata | None = None
    baseline_comparison: BaselineComparison | None = None
    capability_inventory: CapabilityInventory | None = None
    analysis_context: AnalysisContext | None = None

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
