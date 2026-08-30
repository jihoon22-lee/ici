"""Shared issues-first selection for baseline-aware presentation layers."""

from __future__ import annotations

from dataclasses import dataclass

from ici.core.models import BaselineComparison, DeltaState, FindingDelta, SourceLocation

BASELINE_DETAIL_LIMIT = 20
BASELINE_UNCHANGED_DETAIL_LIMIT = 3

_STATE_ORDER = {
    DeltaState.NEW: 0,
    DeltaState.MOVED: 1,
    DeltaState.RESOLVED: 2,
    DeltaState.UNCHANGED: 3,
}


@dataclass(frozen=True)
class BaselineDetailSelection:
    """Bounded detail rows and separately counted omissions."""

    visible: list[FindingDelta]
    omitted_changed: int
    omitted_unchanged: int


def enum_value(value: object) -> str:
    """Return a readable value for an enum or a compatibility string."""

    return str(getattr(value, "value", value))


def severity_transition(entry: FindingDelta) -> tuple[str, str]:
    """Return baseline/current severity labels without renderer-specific markup."""

    before = enum_value(entry.baseline_severity) if entry.baseline_severity is not None else "—"
    after = enum_value(entry.current_severity) if entry.current_severity is not None else "—"
    return before, after


def _location_sort_key(location: SourceLocation | None) -> tuple[object, ...]:
    if location is None:
        return ("", 0, 0, "")
    return (location.path, location.start_line, location.end_line or 0, location.label)


def select_baseline_details(
    comparison: BaselineComparison,
    *,
    detail_limit: int = BASELINE_DETAIL_LIMIT,
    unchanged_limit: int = BASELINE_UNCHANGED_DETAIL_LIMIT,
) -> BaselineDetailSelection:
    """Select gated/changed rows first while retaining a small unchanged sample."""

    entries = sorted(
        comparison.entries,
        key=lambda entry: (
            not entry.gated,
            _STATE_ORDER.get(entry.state, 99),
            entry.engine_name,
            entry.fingerprint,
            _location_sort_key(entry.current_location or entry.baseline_location),
        ),
    )
    changed = [entry for entry in entries if entry.state != DeltaState.UNCHANGED]
    unchanged = [entry for entry in entries if entry.state == DeltaState.UNCHANGED]
    visible_changed = changed[:detail_limit]
    unchanged_slots = max(0, detail_limit - len(visible_changed))
    visible_unchanged = unchanged[: min(unchanged_limit, unchanged_slots)]
    return BaselineDetailSelection(
        visible=[*visible_changed, *visible_unchanged],
        omitted_changed=len(changed) - len(visible_changed),
        omitted_unchanged=len(unchanged) - len(visible_unchanged),
    )
