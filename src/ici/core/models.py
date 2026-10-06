"""Core Domain Models for ici."""

from __future__ import annotations

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
    # engine should have run and could not. Conflating the two historically
    # made projects without an applicable language scope unable to reach a
    # green gate even though no analysis had been expected there.
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


@dataclass(frozen=True)
class FindingFixReplacement:
    """One replacement a tool suggested for a specific source region.

    The region is reported exactly as the tool gave it and is never applied:
    ici does not edit source. Keeping it structured rather than folded into
    remediation prose is what lets a SARIF consumer offer the edit — a reader
    can act on `remediation`, but a tool cannot.

    On the end bound, ici does not renormalize between conventions. The
    parseable-text form (`fix-it:"f":{L:C-L:C}:"t"`) and GCC's JSON `next` key
    both name the position *after* the range, which is what SARIF's `endColumn`
    means, so those pass through correctly. The parser also accepts an `end`
    key, and whether a producer means that inclusively is the producer's
    business — verify against the specific tool before relying on the last
    column of a fix from one that uses it.
    """

    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    replacement: str


@dataclass(frozen=True)
class FindingFix:
    """A complete suggested edit: one description over one or more replacements.

    A single diagnostic can suggest edits in more than one place, and they only
    make sense applied together, so they belong to one fix rather than to
    several.
    """

    description: str = ""
    replacements: tuple[FindingFixReplacement, ...] = ()


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
    # Additive within the major: the stability policy keeps existing fields and
    # their meaning, and a consumer that does not read this one is unaffected.
    fixes: list[FindingFix] = field(default_factory=list)
