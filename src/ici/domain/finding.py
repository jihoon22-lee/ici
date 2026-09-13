"""The canonical finding, and the source span it points at.

This is a superset of the existing ``ici.core.models.Finding``, not a rename.
SPEC-04 section 4 requires fields the current record has no room for: which
provider and native rule produced it, which rule version, and which component,
analysis unit, variant and task it came from. Without those, a finding cannot
be attributed when the same header is compiled two ways, and a baseline cannot
tell "this was fixed" from "this component was not analysed this time".

Two rules are encoded here rather than left to convention:

One finding has one identity. A perspective is a ``category`` or a tag, never a
copy — duplicating a record so it can appear under two headings is how
duplication rates and finding counts stop meaning anything.

Nothing is dropped for looking similar. Two providers reporting the same line
stay two findings unless a verified rule equivalence says otherwise, and even
then the raw evidence survives.
"""

from __future__ import annotations

from dataclasses import dataclass

from ici.domain._validation import (
    require_identifier,
    require_non_negative,
    require_relative_path,
    require_text,
    require_tuple,
)
from ici.domain.enums import EvidenceLevel

__all__ = ["Finding", "FindingSuppression", "SourceSpan"]


@dataclass(frozen=True)
class SourceSpan:
    """A project-relative source region, 1-indexed and inclusive.

    Columns are optional because many tools do not report them, and an absent
    column is different from column 1.
    """

    path: str
    start_line: int
    end_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None
    label: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", require_relative_path(self.path, "span path"))
        if require_non_negative(self.start_line, "span start line") < 1:
            raise ValueError("span start line is 1-indexed")
        if (
            self.end_line is not None
            and require_non_negative(self.end_line, "span end line") < self.start_line
        ):
            raise ValueError("span end line must not precede its start line")
        for name in ("start_column", "end_column"):
            value = getattr(self, name)
            if value is not None and require_non_negative(value, f"span {name}") < 1:
                raise ValueError(f"span {name} is 1-indexed")
        if not isinstance(self.label, str):
            raise ValueError("span label must be a string")


@dataclass(frozen=True)
class FindingSuppression:
    """Whether a finding is suppressed, and on whose authority.

    A suppression always carries its reason and origin. SPEC-04 section 4 is
    blunt about the failure this prevents: a required check that did not finish
    must not be hidden behind a suppression or a baseline.
    """

    suppressed: bool = False
    kind: str = ""
    reason: str = ""
    origin: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.suppressed, bool):
            raise ValueError("suppression flag must be a boolean")
        for name in ("kind", "reason", "origin"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"suppression {name} must be a string")
        if self.suppressed and not self.reason:
            raise ValueError("a suppressed finding must record why")


@dataclass(frozen=True)
class Finding:
    """One normalized result, attributable to the run that produced it."""

    fingerprint: str
    rule_id: str
    message: str
    severity: str
    confidence: str
    primary_location: SourceSpan
    provider: str
    native_rule_id: str = ""
    rule_version: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    related_locations: tuple[SourceSpan, ...] = ()
    component_id: str | None = None
    analysis_unit_id: str | None = None
    variant: str | None = None
    task_id: str | None = None
    evidence: EvidenceLevel = EvidenceLevel.MEASURED
    suppression: FindingSuppression = FindingSuppression()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", require_text(self.fingerprint, "fingerprint"))
        object.__setattr__(self, "rule_id", require_text(self.rule_id, "rule id"))
        object.__setattr__(self, "message", require_text(self.message, "finding message"))
        object.__setattr__(self, "severity", require_identifier(self.severity, "severity"))
        object.__setattr__(self, "confidence", require_identifier(self.confidence, "confidence"))
        object.__setattr__(self, "provider", require_identifier(self.provider, "provider"))
        if not isinstance(self.primary_location, SourceSpan):
            raise ValueError("finding primary location must be a SourceSpan")
        object.__setattr__(
            self,
            "related_locations",
            require_tuple(self.related_locations, SourceSpan, "related locations"),
        )
        for name in ("native_rule_id", "rule_version", "category"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"finding {name} must be a string")
        object.__setattr__(
            self,
            "tags",
            tuple(
                require_identifier(item, "finding tag")
                for item in require_tuple(self.tags, str, "finding tags")
            ),
        )
        for name in ("component_id", "analysis_unit_id", "variant", "task_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_identifier(value, f"finding {name}"))
        if not isinstance(self.evidence, EvidenceLevel):
            raise ValueError("finding evidence must be an EvidenceLevel")
        if not isinstance(self.suppression, FindingSuppression):
            raise ValueError("finding suppression must be a FindingSuppression")
        object.__setattr__(
            self,
            "limitations",
            tuple(
                require_text(item, "finding limitation")
                for item in require_tuple(self.limitations, str, "finding limitations")
            ),
        )

    @property
    def counts_against_gate(self) -> bool:
        """Whether this finding may make a completed required check FAIL.

        A suppressed finding is kept and reported but does not fail the gate. An
        estimated one does not either: SPEC-03 forbids promoting a heuristic to
        an exact gate, so a finding that was not measured is advisory until some
        provider measures it.
        """

        return not self.suppression.suppressed and self.evidence is EvidenceLevel.MEASURED
