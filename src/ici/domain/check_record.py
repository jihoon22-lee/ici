"""Persisted evidence scope for one selected check, independent of its verdict."""

from __future__ import annotations

from dataclasses import dataclass

from ici.domain._validation import require_digest, require_identifier
from ici.domain.enums import TaskState


@dataclass(frozen=True)
class CheckRecord:
    """A missing finding is resolved only by a complete check of the same scope.

    ``scope_digest`` describes input membership and invocation, not source
    contents: changing code may fix a finding; omitting its inputs cannot.
    """

    task_id: str
    check_id: str
    component_id: str
    language: str
    scope_digest: str
    state: TaskState
    evidence_complete: bool
    analysis_unit_id: str | None = None
    variant: str | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "check_id", "component_id", "language"):
            object.__setattr__(self, name, require_identifier(getattr(self, name), name))
        for name in ("analysis_unit_id", "variant"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, require_identifier(getattr(self, name), name))
        object.__setattr__(self, "scope_digest", require_digest(self.scope_digest, "check scope"))
        if not isinstance(self.state, TaskState):
            raise ValueError("check state must be a TaskState")
        if not isinstance(self.evidence_complete, bool):
            raise ValueError("check evidence_complete must be a boolean")
        if self.evidence_complete and self.state is not TaskState.SUCCEEDED:
            raise ValueError("only a succeeded check can carry complete evidence")

    @property
    def comparison_key(self) -> tuple[object, ...]:
        return (
            self.task_id,
            self.check_id,
            self.component_id,
            self.language,
            self.scope_digest,
            self.analysis_unit_id,
            self.variant,
        )
