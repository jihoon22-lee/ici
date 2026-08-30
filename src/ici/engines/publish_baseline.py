"""Strict baseline-summary loading and sticky-comment rendering."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any

from ici.core.models import BaselineComparison, DeltaState, VerificationSuiteResult

_COUNT_FIELDS = {
    "new_count": DeltaState.NEW,
    "unchanged_count": DeltaState.UNCHANGED,
    "moved_count": DeltaState.MOVED,
    "resolved_count": DeltaState.RESOLVED,
}
_REQUIRED_FIELDS = {
    "source_path",
    "warnings",
    "baseline_metadata",
    "fail_on_new",
    "gate_failed",
    *(_COUNT_FIELDS.keys()),
    "regressed_count",
    "gated_count",
    "entries",
}


@dataclass(frozen=True)
class _SummaryCounts:
    by_state: dict[DeltaState, int]
    regressed: int
    gated: int


@dataclass
class LoadedBaselineComparison(BaselineComparison):
    """Validated summary counts without manufacturing full finding deltas."""

    summary_counts: dict[DeltaState, int] = field(default_factory=dict, repr=False, compare=False)
    summary_regressed_count: int = field(default=0, repr=False, compare=False)
    summary_gated_count: int = field(default=0, repr=False, compare=False)

    def count(self, state: DeltaState) -> int:
        return self.summary_counts[state]

    @property
    def regressed_count(self) -> int:
        return self.summary_regressed_count

    @property
    def gated_count(self) -> int:
        return self.summary_gated_count


def _strict_nonnegative_int(value: Any) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _parse_declared_counts(value: dict[str, Any]) -> _SummaryCounts | None:
    by_state: dict[DeltaState, int] = {}
    for field_name, state in _COUNT_FIELDS.items():
        count = _strict_nonnegative_int(value[field_name])
        if count is None:
            return None
        by_state[state] = count
    regressed = _strict_nonnegative_int(value["regressed_count"])
    gated = _strict_nonnegative_int(value["gated_count"])
    if regressed is None or gated is None:
        return None
    return _SummaryCounts(by_state, regressed, gated)


def _parse_delta_flags(value: Any) -> tuple[DeltaState, bool, bool] | None:
    if not isinstance(value, dict):
        return None
    if not all(key in value for key in ("state", "regressed", "gated")):
        return None
    try:
        state = DeltaState(value["state"])
    except (TypeError, ValueError):
        return None
    regressed = value["regressed"]
    gated = value["gated"]
    if type(regressed) is not bool or type(gated) is not bool:
        return None
    if regressed and state not in (DeltaState.MOVED, DeltaState.UNCHANGED):
        return None
    if gated and state != DeltaState.NEW and not regressed:
        return None
    return state, regressed, gated


def _summarize_entries(value: Any) -> _SummaryCounts | None:
    if not isinstance(value, list):
        return None
    by_state = {state: 0 for state in DeltaState}
    regressed = 0
    gated = 0
    for item in value:
        flags = _parse_delta_flags(item)
        if flags is None:
            return None
        state, is_regressed, is_gated = flags
        by_state[state] += 1
        regressed += int(is_regressed)
        gated += int(is_gated)
    return _SummaryCounts(by_state, regressed, gated)


def _parse_header(value: dict[str, Any]) -> tuple[str, list[str], bool, bool] | None:
    source_path = value["source_path"]
    warnings = value["warnings"]
    metadata = value["baseline_metadata"]
    fail_on_new = value["fail_on_new"]
    gate_failed = value["gate_failed"]
    if not isinstance(source_path, str) or not source_path:
        return None
    if not isinstance(warnings, list) or not all(
        isinstance(item, str) and bool(item) for item in warnings
    ):
        return None
    if metadata is not None and not isinstance(metadata, dict):
        return None
    if type(fail_on_new) is not bool or type(gate_failed) is not bool:
        return None
    return source_path, list(warnings), fail_on_new, gate_failed


def parse_baseline_summary(value: Any) -> BaselineComparison | None:
    """Read a complete v3 baseline comparison without trusting malformed data."""

    if value is None or not isinstance(value, dict):
        return None
    if not _REQUIRED_FIELDS.issubset(value):
        return None
    header = _parse_header(value)
    declared = _parse_declared_counts(value)
    observed = _summarize_entries(value["entries"])
    if header is None or declared is None or observed is None or declared != observed:
        return None
    source_path, warnings, fail_on_new, gate_failed = header
    if gate_failed != (fail_on_new and declared.gated > 0):
        return None
    return LoadedBaselineComparison(
        source_path=source_path,
        warnings=warnings,
        baseline_metadata=None,
        fail_on_new=fail_on_new,
        gate_failed=gate_failed,
        summary_counts=declared.by_state,
        summary_regressed_count=declared.regressed,
        summary_gated_count=declared.gated,
    )


def _escape_comment_value(value: str) -> str:
    compact = " ".join(value.replace("\r", "\n").splitlines())
    return html.escape(compact, quote=False).replace(chr(96), "&#96;").replace("|", "&#124;")


def baseline_summary_lines(suite: VerificationSuiteResult | None) -> list[str]:
    """Render a compact baseline summary for a single project comment block."""

    comparison = suite.baseline_comparison if suite is not None else None
    if comparison is None:
        return []
    new_count = comparison.count(DeltaState.NEW)
    if comparison.gate_failed:
        gate = "❌ FAILED"
    elif comparison.fail_on_new:
        gate = "✅ PASSED"
    else:
        gate = "NOT ENFORCED"
    lines = [
        f"> 🔎 **Baseline delta**: new **{new_count}** · "
        f"regressed **{comparison.regressed_count}** · gated **{comparison.gated_count}** "
        f"· gate **{gate}**"
    ]
    if comparison.warnings:
        first_warning = _escape_comment_value(comparison.warnings[0])
        remaining = len(comparison.warnings) - 1
        suffix = f" (+{remaining} more)" if remaining else ""
        lines.append(
            f"> ⚠️ **Baseline warnings ({len(comparison.warnings)})**: {first_warning}{suffix}"
        )
    return lines
