"""ici-next domain models: the common vocabulary, with no I/O.

This package is the base of the dependency direction ARCH section 3 sets out
(``cli -> application -> domain``). Nothing here reads the filesystem, starts a
process, or consults the environment, so every model can be built in a unit
test without a project on disk. ``tests/test_domain_boundaries.py`` enforces
that rather than leaving it to review.

It is additive and opt-in. ``ici.core.models`` and the existing report writers
are untouched and remain the shipping path; ``ici.domain.legacy`` is the single
module allowed to bridge the two, and it names what each direction loses.

WP02 (#200) builds this. The JSON Schema, the result and event IO, and the
legacy reader adapter are the later PR boundaries of the same issue, so the
absence of serialization here is deliberate, not an omission.
"""

from __future__ import annotations

from ici.domain.enums import (
    CheckExecution,
    EvidenceLevel,
    GateVerdict,
    PublicationState,
    ScopeKind,
    TaskKind,
    TaskState,
)
from ici.domain.events import (
    EVENT_SCHEMA_ID,
    EVENT_SCHEMA_VERSION,
    EventType,
    RunEvent,
)
from ici.domain.finding import Finding, FindingSuppression, SourceSpan
from ici.domain.observation import Measurement, Observation
from ici.domain.result import (
    SCHEMA_ID,
    SCHEMA_VERSION,
    ExecutionSummary,
    GateOutcome,
    Producer,
    PublicationOutcome,
    RunIdentity,
    RunResult,
    ScopeSelection,
)
from ici.domain.tasks import TaskSpec
from ici.domain.toolchain import ResolvedTool, ToolRole, ToolSource
from ici.domain.workspace import (
    AnalysisUnit,
    BuildUnit,
    Component,
    SourceSnapshot,
    Workspace,
)

__all__ = [
    "EVENT_SCHEMA_ID",
    "EVENT_SCHEMA_VERSION",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "AnalysisUnit",
    "BuildUnit",
    "CheckExecution",
    "Component",
    "EventType",
    "EvidenceLevel",
    "ExecutionSummary",
    "Finding",
    "FindingSuppression",
    "GateOutcome",
    "GateVerdict",
    "Measurement",
    "Observation",
    "Producer",
    "PublicationOutcome",
    "PublicationState",
    "ResolvedTool",
    "RunEvent",
    "RunIdentity",
    "RunResult",
    "ScopeKind",
    "ScopeSelection",
    "SourceSnapshot",
    "SourceSpan",
    "TaskKind",
    "TaskSpec",
    "TaskState",
    "ToolRole",
    "ToolSource",
    "Workspace",
]
