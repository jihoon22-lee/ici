"""Baseline comparison tab — compact counts and issues-first finding deltas."""

from __future__ import annotations

import html
from pathlib import Path

from ici.core.models import (
    BaselineComparison,
    DeltaState,
    FindingDelta,
    SourceLocation,
    VerificationSuiteResult,
    gate_reason,
)
from ici.reporters.html.utils import _location_controls

_BASELINE_DETAIL_LIMIT = 20
_BASELINE_UNCHANGED_DETAIL_LIMIT = 3
_DELTA_STATE_ORDER = {
    DeltaState.NEW: 0,
    DeltaState.MOVED: 1,
    DeltaState.RESOLVED: 2,
    DeltaState.UNCHANGED: 3,
}
_DELTA_STATE_TONE = {
    DeltaState.NEW: "new",
    DeltaState.MOVED: "moved",
    DeltaState.RESOLVED: "resolved",
    DeltaState.UNCHANGED: "unchanged",
}


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _location_sort_key(location: SourceLocation | None) -> tuple[object, ...]:
    if location is None:
        return ("", 0, 0, "")
    return (location.path, location.start_line, location.end_line or 0, location.label)


def _select_entries(
    comparison: BaselineComparison,
) -> tuple[list[FindingDelta], int, int]:
    """Bound the visual list while preserving gated and changed rows first."""
    entries = list(comparison.entries or [])
    entries.sort(
        key=lambda entry: (
            not entry.gated,
            _DELTA_STATE_ORDER.get(entry.state, 99),
            entry.engine_name,
            entry.fingerprint,
            _location_sort_key(entry.current_location or entry.baseline_location),
        )
    )
    changed = [entry for entry in entries if entry.state != DeltaState.UNCHANGED]
    unchanged = [entry for entry in entries if entry.state == DeltaState.UNCHANGED]
    visible_changed = changed[:_BASELINE_DETAIL_LIMIT]
    unchanged_slots = _BASELINE_DETAIL_LIMIT - len(visible_changed)
    visible_unchanged = unchanged[: min(_BASELINE_UNCHANGED_DETAIL_LIMIT, unchanged_slots)]
    visible = [*visible_changed, *visible_unchanged]
    return visible, len(entries) - len(visible), len(unchanged) - len(visible_unchanged)


def _render_location(location: SourceLocation | None, base: Path) -> str:
    if location is None:
        return "<span class='baseline-none'>—</span>"
    line = f"L{location.start_line}"
    if location.end_line is not None and location.end_line > location.start_line:
        line += f"-L{location.end_line}"
    label = f"{location.path}:{line}"
    return _location_controls(location.path, location.start_line, base, label=label)


def _render_severity_transition(entry: FindingDelta) -> str:
    before = _enum_value(entry.baseline_severity) if entry.baseline_severity is not None else "—"
    after = _enum_value(entry.current_severity) if entry.current_severity is not None else "—"
    return html.escape(f"{before} → {after}")


def _render_delta(entry: FindingDelta, base: Path) -> str:
    state_value = _enum_value(entry.state)
    tone = _DELTA_STATE_TONE.get(entry.state, "unknown")
    gate = (
        "<span class='baseline-gate-pill baseline-gate-failed'>GATED</span>" if entry.gated else ""
    )
    return f"""
      <article class='baseline-delta baseline-delta-{tone}'>
        <div class='baseline-delta-heading'>
          <span class='baseline-delta-state'>{html.escape(state_value.upper())}</span>
          {gate}
          <strong>{html.escape(entry.engine_name)}</strong>
          <span class='baseline-delta-rule'>{html.escape(entry.rule_id)}</span>
        </div>
        <div class='baseline-delta-grid'>
          <div><span class='baseline-field-label'>Current location</span>{_render_location(entry.current_location, base)}</div>
          <div><span class='baseline-field-label'>Baseline location</span>{_render_location(entry.baseline_location, base)}</div>
          <div><span class='baseline-field-label'>Severity transition</span><code>{_render_severity_transition(entry)}</code></div>
        </div>
        <p class='baseline-delta-message'>{html.escape(entry.message or "—")}</p>
      </article>
    """


def _gate_label(comparison: BaselineComparison) -> tuple[str, str]:
    if comparison.gate_failed:
        return "FAILED", "failed"
    if comparison.fail_on_new:
        return "PASSED", "passed"
    return "NOT ENFORCED", "not-enforced"


def _render_baseline_section(
    comparison: BaselineComparison,
    suite: VerificationSuiteResult,
    base: Path,
) -> str:
    """Render a safe, bounded baseline view; the JSON report remains complete."""
    visible, omitted, omitted_unchanged = _select_entries(comparison)
    gate_label, gate_tone = _gate_label(comparison)
    warnings = (
        "<ul class='baseline-warning-list'>"
        + "".join(f"<li>{html.escape(str(warning))}</li>" for warning in comparison.warnings)
        + "</ul>"
        if comparison.warnings
        else "<p class='baseline-none'>No compatibility warnings.</p>"
    )
    details = "".join(_render_delta(entry, base) for entry in visible)
    notes = []
    if omitted:
        notes.append(f"{omitted} additional delta row(s) omitted")
    if omitted_unchanged:
        notes.append(f"{omitted_unchanged} unchanged row(s) omitted")
    omitted_note = (
        f"<p class='baseline-omitted-note'>Note: {html.escape('; '.join(notes))}. "
        "The JSON report retains the full inventory.</p>"
        if notes
        else ""
    )
    gate_reason_text = gate_reason(suite.results, suite.suite_status, comparison)
    return f"""
    <div class='baseline-section'>
      <div class='card baseline-summary-card'>
        <div class='baseline-section-heading'>
          <div>
            <h2>🧭 Baseline Finding Delta</h2>
            <p>Issues-first comparison with the selected v3 finding inventory.</p>
          </div>
          <span class='baseline-gate-pill baseline-gate-{gate_tone}'>{gate_label}</span>
        </div>
        <dl class='baseline-summary-grid'>
          <div><dt>Source</dt><dd><code>{html.escape(comparison.source_path)}</code></dd></div>
          <div><dt>New</dt><dd>{comparison.count(DeltaState.NEW)}</dd></div>
          <div><dt>Unchanged</dt><dd>{comparison.count(DeltaState.UNCHANGED)}</dd></div>
          <div><dt>Moved</dt><dd>{comparison.count(DeltaState.MOVED)}</dd></div>
          <div><dt>Resolved</dt><dd>{comparison.count(DeltaState.RESOLVED)}</dd></div>
          <div><dt>Regressed</dt><dd>{comparison.regressed_count}</dd></div>
          <div><dt>Gated</dt><dd>{comparison.gated_count}</dd></div>
          <div><dt>Fail-on-new gate</dt><dd><span class='baseline-gate-pill baseline-gate-{gate_tone}'>{gate_label}</span></dd></div>
        </dl>
        <div class='baseline-reason'><span class='baseline-field-label'>Gate reason</span><span>{html.escape(gate_reason_text)}</span></div>
      </div>

      <div class='card baseline-warning-card'>
        <h3>Compatibility warnings</h3>
        {warnings}
      </div>

      <div class='baseline-deltas-card'>
        <div class='baseline-section-heading'>
          <div>
            <h2>⚠️ Issues-first delta details</h2>
            <p>Gated entries appear first. Current and baseline locations are shown for every visible row.</p>
          </div>
          <span class='baseline-count-pill'>{len(comparison.entries or [])} total entries</span>
        </div>
        {f"<div class='baseline-delta-list'>{details}</div>" if details else "<p class='baseline-none'>No changed finding rows to display.</p>"}
        {omitted_note}
      </div>
    </div>
    """


__all__ = ["_render_baseline_section"]
