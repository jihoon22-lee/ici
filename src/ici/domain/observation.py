"""What a provider saw, before any policy was applied.

An ``Observation`` is the honest record of one task's outcome: the findings and
measurements it produced, how it terminated, and what it could not see. Policy
is applied afterwards, to observations — SPEC-04 section 2 keeps "the tool ran"
and "the code is clean" on separate axes, and that separation only survives if
there is a stage that records the first without deciding the second.

``truncated`` is here because an empty finding list means two different things.
If a parser's input was cut off, zero findings is missing data, not a clean
result, and SPEC-02 section 5 forbids treating the two the same.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.domain._validation import (
    require_identifier,
    require_non_negative,
    require_text,
    require_tuple,
)
from ici.domain.enums import EvidenceLevel, TaskState
from ici.domain.finding import Finding

__all__ = ["Measurement", "Observation"]


@dataclass(frozen=True)
class Measurement:
    """A raw numeric fact with its unit and how it was obtained.

    Numerator and denominator are kept apart from any ratio on purpose. SPEC-04
    section 4 forbids averaging percentages across components; only compatible
    raw counts may be combined, and that is only possible if the raw counts
    survive to this point.
    """

    name: str
    value: float
    unit: str = ""
    numerator: int | None = None
    denominator: int | None = None
    evidence: EvidenceLevel = EvidenceLevel.MEASURED

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, "measurement name"))
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise ValueError("measurement value must be a number")
        if self.value != self.value:
            raise ValueError("measurement value must not be NaN")
        object.__setattr__(self, "value", float(self.value))
        if not isinstance(self.unit, str):
            raise ValueError("measurement unit must be a string")
        for name in ("numerator", "denominator"):
            value = getattr(self, name)
            if value is not None:
                require_non_negative(value, f"measurement {name}")
        if self.denominator == 0 and self.numerator is not None:
            raise ValueError("measurement denominator must not be zero when a numerator is given")
        if not isinstance(self.evidence, EvidenceLevel):
            raise ValueError("measurement evidence must be an EvidenceLevel")


@dataclass(frozen=True)
class Observation:
    """One task's evidence: what ran, what it found, what it could not see."""

    task_id: str
    provider: str
    state: TaskState
    findings: tuple[Finding, ...] = ()
    measurements: tuple[Measurement, ...] = ()
    exit_code: int | None = None
    signal: str | None = None
    timed_out: bool = False
    truncated: bool = False
    duration_seconds: float | None = None
    tool_versions: tuple[tuple[str, str], ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", require_identifier(self.task_id, "observation task id"))
        object.__setattr__(
            self, "provider", require_identifier(self.provider, "observation provider")
        )
        if not isinstance(self.state, TaskState):
            raise ValueError("observation state must be a TaskState")
        object.__setattr__(
            self, "findings", require_tuple(self.findings, Finding, "observation findings")
        )
        object.__setattr__(
            self,
            "measurements",
            require_tuple(self.measurements, Measurement, "observation measurements"),
        )
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool) or not isinstance(self.exit_code, int)
        ):
            raise ValueError("observation exit code must be an integer")
        if self.signal is not None:
            require_text(self.signal, "observation signal")
        for name in ("timed_out", "truncated"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"observation {name} must be a boolean")
        if self.duration_seconds is not None:
            value = self.duration_seconds
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError("observation duration must be a non-negative number")
            object.__setattr__(self, "duration_seconds", float(value))
        versions = require_tuple(self.tool_versions, tuple, "observation tool versions")
        for item in versions:
            if len(item) != 2 or not all(isinstance(part, str) for part in item):
                raise ValueError("observation tool versions must be (name, version) string pairs")
        object.__setattr__(self, "tool_versions", versions)
        object.__setattr__(
            self,
            "limitations",
            tuple(
                require_text(item, "observation limitation")
                for item in require_tuple(self.limitations, str, "observation limitations")
            ),
        )

    @property
    def evidence_is_complete(self) -> bool:
        """Whether this observation can support a pass.

        A task that succeeded but was truncated or timed out has incomplete
        evidence even though it produced output, so a check relying on it is
        INCOMPLETE rather than COMPLETED.
        """

        return self.state is TaskState.SUCCEEDED and not self.truncated and not self.timed_out
