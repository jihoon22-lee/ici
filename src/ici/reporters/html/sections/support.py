"""Support and capability tab — evaluated engine coverage at a glance."""

import html
from collections.abc import Iterable

from ici.core.models import EngineSupport, EvidenceState, SupportMatrix


def _raw_value(value: object) -> object:
    """Return an enum's value while tolerating legacy/string-like payloads."""
    return getattr(value, "value", value)


def _escaped_value(value: object, *, empty: str = "—") -> str:
    """Escape a matrix value before placing it in HTML text or an attribute."""
    raw = _raw_value(value)
    text = empty if raw is None or raw == "" else str(raw)
    return html.escape(text, quote=True)


def _render_values(values: Iterable[object], *, empty: str = "None declared") -> str:
    items = list(values)
    if not items:
        return f"<span class='support-none'>{html.escape(empty)}</span>"
    return "".join(f"<span class='support-pill'>{_escaped_value(item)}</span>" for item in items)


def _render_list(values: Iterable[object], *, empty: str = "None declared") -> str:
    items = list(values)
    if not items:
        return f"<span class='support-none'>{html.escape(empty)}</span>"
    return (
        "<ul class='support-list'>"
        + "".join(f"<li>{_escaped_value(item)}</li>" for item in items)
        + "</ul>"
    )


def _render_field(label: str, value: str) -> str:
    return f"<div class='support-field'><dt>{html.escape(label)}</dt><dd>{value}</dd></div>"


def _render_mode(mode: object, *, missing: str = "Not active") -> str:
    if mode is None:
        return f"<span class='support-none'>{html.escape(missing)}</span>"
    return f"<code>{_escaped_value(mode)}</code>"


def _render_boolean(value: object) -> str:
    if value is True:
        text = "true"
    elif value is False:
        text = "false"
    else:
        text = str(value)
    return f"<code>{html.escape(text, quote=True)}</code>"


def _needs_attention(entry: EngineSupport) -> bool:
    """Choose which rows to expand first; support decisions remain in core."""
    if entry.applicable is not True:
        return False
    if entry.enabled is not True:
        return True
    return _raw_value(entry.evidence) != EvidenceState.MEASURED.value


def _entry_tone(entry: EngineSupport) -> str:
    if entry.applicable is not True:
        return "neutral"
    return "attention" if _needs_attention(entry) else "healthy"


def _render_entry(entry: EngineSupport, *, expanded: bool) -> str:
    tone = _entry_tone(entry)
    open_attr = " open" if expanded else ""
    engine = _escaped_value(entry.engine_name, empty="Unknown engine")
    language = _escaped_value(entry.language, empty="Unknown language")
    declared_mode = _render_mode(entry.mode, missing="Not declared")
    active_mode = _render_mode(entry.active_mode)
    evidence = _escaped_value(entry.evidence, empty="Unknown")
    confidence = _escaped_value(entry.confidence, empty="Unknown")
    marker = "⚠️ " if tone == "attention" else ""
    fields = "".join(
        (
            _render_field("Engine", f"<strong>{engine}</strong>"),
            _render_field("Language", language),
            _render_field("Declared mode", declared_mode),
            _render_field("Active mode", active_mode),
            _render_field("Applicable", _render_boolean(entry.applicable)),
            _render_field("Enabled", _render_boolean(entry.enabled)),
            _render_field(
                "Evidence", f"<span class='support-badge support-badge-{tone}'>{evidence}</span>"
            ),
            _render_field(
                "Confidence",
                f"<span class='support-badge support-badge-neutral'>{confidence}</span>",
            ),
            _render_field("Framework scope", _render_values(entry.frameworks)),
            _render_field("Required tools", _render_values(entry.required_tools)),
            _render_field("Optional tools", _render_values(entry.optional_tools)),
            _render_field("Fallback", _render_mode(entry.fallback_mode, missing="None")),
            _render_field("Limitations", _render_list(entry.limitations)),
            _render_field("Reason", _escaped_value(entry.reason, empty="No reason recorded")),
        )
    )
    return f"""
    <details class='support-entry support-entry-{tone}'{open_attr}>
      <summary class='support-entry-summary'>
        <span class='support-entry-title'>{marker}{engine}</span>
        <span class='support-entry-language'>{language}</span>
        <span class='support-entry-mode'>
          <span class='support-label'>Declared</span> {declared_mode}
          <span class='support-arrow'>→</span>
          <span class='support-label'>Active</span> {active_mode}
        </span>
        <span class='support-badge support-badge-{tone}'>{evidence}</span>
      </summary>
      <dl class='support-fields'>{fields}</dl>
    </details>
    """


def _render_support_section(matrix: SupportMatrix | None) -> str:
    """Render the evaluated support matrix, or nothing for legacy v3 suites."""
    if matrix is None:
        return ""

    entries = list(matrix.entries or [])
    attention_entries = [entry for entry in entries if _needs_attention(entry)]
    ordered_entries = [
        *attention_entries,
        *(entry for entry in entries if not _needs_attention(entry)),
    ]
    entry_rows = "".join(
        _render_entry(entry, expanded=entry in attention_entries) for entry in ordered_entries
    )
    if not entry_rows:
        entry_rows = "<div class='empty-clean'>No evaluated engine capabilities available.</div>"

    attention_label = (
        f"{len(attention_entries)} item(s) need attention"
        if attention_entries
        else "No active capability caveats"
    )
    return f"""
    <div class='support-section'>
      <div class='card support-scope-card'>
        <div class='support-section-heading'>
          <div>
            <h2>🧭 Support &amp; Capabilities</h2>
            <p>Evaluated engine support for this project. Attention items are expanded first; informational rows remain available below.</p>
          </div>
          <span class='support-badge support-badge-neutral'>{len(entries)} entries</span>
        </div>
        <div class='support-scope-grid'>
          <div class='support-scope-item'>
            <div class='support-label'>Project languages</div>
            <div class='support-pill-list'>{_render_values(matrix.project_languages, empty="None detected")}</div>
          </div>
          <div class='support-scope-item'>
            <div class='support-label'>Project frameworks</div>
            <div class='support-pill-list'>{_render_values(matrix.project_frameworks, empty="None detected")}</div>
          </div>
        </div>
      </div>

      <div class='support-matrix-card'>
        <div class='support-matrix-heading'>
          <div>
            <h2>⚙️ Engine capability matrix</h2>
            <p>{html.escape(attention_label)} · every row shows declared versus active mode, evidence, tools, and limitations.</p>
          </div>
        </div>
        <div class='support-entry-list'>{entry_rows}</div>
      </div>
    </div>
    """


__all__ = ["_render_support_section"]
