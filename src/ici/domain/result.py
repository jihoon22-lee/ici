"""The run envelope: scope, execution, gate and publication, kept apart.

SPEC-04 section 1 defines the external JSON; this module defines the internal
model behind it. They are deliberately not the same shape — #200 asks for the
JSON contract and the class structure to stay separable so that one can be
refactored without breaking the other.

The four axes never collapse into a single status:

``ScopeSelection`` says what was asked for and what the workspace would require.
``ExecutionSummary`` says whether the required work finished.
``GateOutcome`` says whether the code is acceptable, once per scope.
``PublicationOutcome`` says whether publishing worked, and cannot change the
verdict.

The combination worth naming is INCOMPLETE with violations. A run that could not
finish and also found real problems must report both: dropping the findings
because the run was incomplete throws away work the user paid for, and calling
it FAIL hides that the picture is partial.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.domain._validation import (
    require_digest,
    require_identifier,
    require_text,
    require_tuple,
    require_unique_identifiers,
)
from ici.domain.enums import GateVerdict, PublicationState, ScopeKind
from ici.domain.finding import Finding
from ici.domain.observation import Measurement
from ici.domain.workspace import SourceSnapshot

__all__ = [
    "ExecutionSummary",
    "GateOutcome",
    "Producer",
    "PublicationOutcome",
    "RunIdentity",
    "RunResult",
    "ScopeSelection",
]

SCHEMA_ID = "ici.next.run"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Producer:
    """Which build of ici wrote this result.

    ``bundle_digest`` is optional because the stable pyz path has no bundle. It
    is not defaulted to a placeholder: an absent digest says "this did not come
    from a bundle", which is a different fact from "the bundle is unknown", and
    SPEC-05 forbids reporting one as the other.
    """

    ici_version: str
    bundle_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ici_version", require_text(self.ici_version, "ici version"))
        if self.bundle_digest is not None:
            object.__setattr__(
                self, "bundle_digest", require_digest(self.bundle_digest, "bundle digest")
            )


@dataclass(frozen=True)
class RunIdentity:
    """The inputs that make a result comparable to another result.

    Clock time and duration are excluded on purpose (#200 step 5): the same
    sources, policy and toolchain must produce the same identity on Tuesday as
    on Monday, or a baseline comparison cannot distinguish a real change from a
    second run.
    """

    source: SourceSnapshot
    policy_digest: str
    toolchain_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceSnapshot):
            raise ValueError("run identity source must be a SourceSnapshot")
        for name in ("policy_digest", "toolchain_digest"):
            object.__setattr__(self, name, require_digest(getattr(self, name), f"run {name}"))


@dataclass(frozen=True)
class ScopeSelection:
    """What this run covered, and what a full run would have covered.

    ``full_required_satisfied`` is the field that stops a partial pass from
    reading as a workspace pass (R05). It is stored, not derived at report time,
    so that a consumer cannot reach the wrong conclusion by recomputing it from
    an incomplete list.
    """

    kind: ScopeKind
    selected_components: tuple[str, ...] = ()
    selected_languages: tuple[str, ...] = ()
    required_components: tuple[str, ...] = ()
    omitted_components: tuple[str, ...] = ()
    full_required_satisfied: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ScopeKind):
            raise ValueError("scope kind must be a ScopeKind")
        for name in (
            "selected_components",
            "selected_languages",
            "required_components",
            "omitted_components",
        ):
            values = require_tuple(getattr(self, name), str, f"scope {name}")
            object.__setattr__(
                self,
                name,
                require_unique_identifiers(
                    (require_identifier(item, f"scope {name} entry") for item in values),
                    f"scope {name}",
                ),
            )
        if not isinstance(self.full_required_satisfied, bool):
            raise ValueError("scope full_required_satisfied must be a boolean")
        if self.kind is ScopeKind.FULL and not self.full_required_satisfied:
            raise ValueError("a full scope must satisfy the required components")
        if self.kind is not ScopeKind.FULL and self.full_required_satisfied and self.omitted:
            raise ValueError("a scope that omits components cannot be fully satisfied")

    @property
    def omitted(self) -> bool:
        """Whether any required component was left out of this run."""

        return bool(set(self.required_components) - set(self.selected_components))


@dataclass(frozen=True)
class ExecutionSummary:
    """Whether the work this run needed actually finished."""

    required_complete: bool
    cancelled: bool = False
    blocked_task_ids: tuple[str, ...] = ()
    failed_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("required_complete", "cancelled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"execution {name} must be a boolean")
        for name in ("blocked_task_ids", "failed_task_ids"):
            values = require_tuple(getattr(self, name), str, f"execution {name}")
            object.__setattr__(
                self,
                name,
                tuple(require_identifier(item, f"execution {name} entry") for item in values),
            )
        if self.required_complete and (self.blocked_task_ids or self.cancelled):
            raise ValueError("a run with blocked or cancelled work is not complete")


@dataclass(frozen=True)
class GateOutcome:
    """The verdict, reported once for the selected scope and once for the workspace.

    ``has_violations`` is independent of both verdicts. It stays true even when
    the verdict is INCOMPLETE, which is the whole point: real problems found
    during a run that could not finish are still real problems.
    """

    selected: GateVerdict
    workspace: GateVerdict = GateVerdict.NOT_EVALUATED
    has_violations: bool = False
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("selected", "workspace"):
            if not isinstance(getattr(self, name), GateVerdict):
                raise ValueError(f"gate {name} must be a GateVerdict")
        if not isinstance(self.has_violations, bool):
            raise ValueError("gate has_violations must be a boolean")
        object.__setattr__(
            self,
            "reasons",
            tuple(
                require_text(item, "gate reason")
                for item in require_tuple(self.reasons, str, "gate reasons")
            ),
        )
        if self.selected is GateVerdict.PASS and self.has_violations:
            raise ValueError("a passing scope cannot also report violations")
        if self.selected in (GateVerdict.FAIL, GateVerdict.INCOMPLETE) and not self.reasons:
            raise ValueError(f"a {self.selected.value} gate must say why")

    @property
    def exit_code(self) -> int:
        """The process exit code for this outcome (SPEC-04 section 3).

        INCOMPLETE outranks FAIL: when a run both failed to finish and found
        violations, the caller is told about the incompleteness (3) while
        ``has_violations`` still carries the rest. Reporting 1 there would claim
        a complete verdict the run did not reach.
        """

        if self.selected is GateVerdict.INCOMPLETE:
            return 3
        if self.selected is GateVerdict.FAIL:
            return 1
        return 0


@dataclass(frozen=True)
class PublicationOutcome:
    """Whether publishing succeeded. Never feeds back into the gate."""

    state: PublicationState = PublicationState.NOT_CONFIGURED
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.state, PublicationState):
            raise ValueError("publication state must be a PublicationState")
        if not isinstance(self.detail, str):
            raise ValueError("publication detail must be a string")


@dataclass(frozen=True)
class RunResult:
    """One complete run, ready to serialize as ``ici.next.run`` v1."""

    run_id: str
    producer: Producer
    identity: RunIdentity
    scope: ScopeSelection
    execution: ExecutionSummary
    gate: GateOutcome
    findings: tuple[Finding, ...] = ()
    metrics: tuple[Measurement, ...] = ()
    publication: PublicationOutcome = PublicationOutcome()
    limitations: tuple[str, ...] = ()
    schema_id: str = SCHEMA_ID
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", require_text(self.run_id, "run id"))
        for name, expected in (
            ("producer", Producer),
            ("identity", RunIdentity),
            ("scope", ScopeSelection),
            ("execution", ExecutionSummary),
            ("gate", GateOutcome),
            ("publication", PublicationOutcome),
        ):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"run {name} must be a {expected.__name__}")
        object.__setattr__(self, "findings", require_tuple(self.findings, Finding, "run findings"))
        object.__setattr__(self, "metrics", require_tuple(self.metrics, Measurement, "run metrics"))
        object.__setattr__(
            self,
            "limitations",
            tuple(
                require_text(item, "run limitation")
                for item in require_tuple(self.limitations, str, "run limitations")
            ),
        )
        if self.schema_id != SCHEMA_ID:
            raise ValueError(f"run schema_id must be {SCHEMA_ID!r}, not {self.schema_id!r}")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"run schema_version must be {SCHEMA_VERSION}")
        if self.gate.has_violations and not self.blocking_findings:
            raise ValueError("gate reports violations but no finding counts against it")
        if not self.execution.required_complete and self.gate.selected is GateVerdict.PASS:
            raise ValueError("an incomplete run cannot report a passing gate")

    @property
    def blocking_findings(self) -> tuple[Finding, ...]:
        """Findings that may make a completed required check FAIL."""

        return tuple(item for item in self.findings if item.counts_against_gate)
