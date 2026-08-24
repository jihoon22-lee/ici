"""Summary tab — main engine result table, jump-to-tab buttons, TEM card."""

import html
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus, format_score_display
from ici.reporters.html.utils import _get_status_theme, _location_controls, _status_color

_JUMP_MAP = {
    "line": ("tab-line", "📊 View File Tree & Charts →"),
    "test": ("tab-test", "🧪 View Test Suites & Coverage Details →"),
    "complexity": ("tab-complexity", "🧩 View Complexity Details →"),
    "dup": ("tab-dup", "📦 View Clone Groups →"),
    "cycle": ("tab-cycles", "🔁 View Dependency Cycles →"),
    "cognitive": ("tab-complexity", "🧠 View Complexity Details →"),
    "security": ("tab-security", "🔐 View Security & Resources →"),
    "resource": ("tab-security", "🔐 View Security & Resources →"),
}


def _render_engine_table_rows(results: list[EngineResult], base: Path) -> list[str]:
    """Renders main summary table rows."""
    engine_rows = []
    for res in results:
        if res.status == EngineStatus.SKIP:
            skip_color, skip_bg, _ = _get_status_theme(EngineStatus.SKIP)
            engine_rows.append(
                f"<tr style='opacity: 0.55;'>"
                f"  <td class='engine-name' style='color: var(--text-muted);'>{html.escape(res.engine_name)}</td>"
                f"  <td><span class='badge' style='color:{skip_color}; background:{skip_bg}; "
                f"border:1px solid {skip_color}44'>N/A</span></td>"
                f"  <td colspan='3'>"
                f"    <details>"
                f"      <summary style='cursor:pointer; color:var(--text-muted); font-size:0.85rem;'>Not applicable — why?</summary>"
                f"      <div class='engine-summary-text' style='margin-top:0.4rem;'>{html.escape(res.summary)}</div>"
                f"    </details>"
                f"  </td>"
                f"</tr>"
            )
            continue
        color, bg, _ = _get_status_theme(res.status)
        score_str = format_score_display(res)
        duration_str = f"{res.duration:.2f}s" if res.duration > 0 else "-"
        summary_col = _render_main_row_summary(res, base)

        engine_rows.append(
            f"<tr class='engine-row'>"
            f"  <td class='engine-name'><strong>{html.escape(res.engine_name)}</strong></td>"
            f"  <td><span class='badge' style='color:{color}; background:{bg}; border: 1px solid {color}33'>{res.status.value}</span></td>"
            f"  <td>{summary_col}</td>"
            f"  <td class='text-right'><code>{html.escape(score_str)}</code></td>"
            f"  <td class='text-right text-muted'>{duration_str}</td>"
            f"</tr>"
        )
    return engine_rows


def _render_tem_card(tem_score: float | None) -> str:
    """Renders TEM score KPI card."""
    if tem_score is None:
        return ""
    tem_pct = min(100.0, (tem_score / 5.0) * 100.0)
    return f"""
    <div class="card stat-card">
        <div class="stat-label">TEM Quality Score</div>
        <div class="stat-value" style="color:#38bdf8">{tem_score:.2f} <span class="stat-sub">/ 5.0</span></div>
        <div class="mini-progress-bg">
            <div class="mini-progress-fill" style="width: {tem_pct}%; background: #38bdf8;"></div>
        </div>
    </div>
    """


def _render_main_row_summary(res: EngineResult, base: Path) -> str:
    """Renders main summary column with quick jump buttons and noise-free presentation."""
    eng = res.engine_name

    # Engines with a dedicated tab — one shared template for all jump links.
    if eng in _JUMP_MAP:
        tab_id, label = _JUMP_MAP[eng]
        return (
            f"<div class='engine-summary-text'>{html.escape(res.summary)}</div>"
            f"<button class='jump-tab-btn' data-tab-target='{tab_id}'>"
            f"{label}</button>"
        )

    # Sanitize Engine
    if eng == "sanitize" and not res.targets:
        return "<div class='engine-summary-text'>✅ 0 Defect — Memory Safety & Resource Management Clean</div>"

    # Type Engine
    if eng == "type" and not res.targets:
        return "<div class='engine-summary-text'>✅ Static Type Check Passed (0 Errors)</div>"

    # Default Target List (if any violations remain)
    targets_html = []
    for t in res.targets:
        if t.status != EngineStatus.PASS:
            t_badge_color = _status_color(t.status)
            location = (
                _location_controls(t.file_path, t.start_line, base)
                if t.file_path
                else "<span class='issue-no-location'>engine result</span>"
            )

            targets_html.append(
                f"<div class='target-item'>"
                f"  <span class='badge' style='color:{t_badge_color}'>{t.status.value}</span> "
                f"  {location}"
                f"  <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span> "
                f"  <span class='target-msg'>{html.escape(t.message)}</span>"
                f"</div>"
            )

    details_section = ""
    if targets_html:
        details_section = (
            f"<details class='target-details' open>"
            f"  <summary>{len(targets_html)} Actionable Items</summary>"
            f"  <div class='targets-list'>{''.join(targets_html)}</div>"
            f"</details>"
        )

    return f"<div class='engine-summary-text'>{html.escape(res.summary)}</div>{details_section}"
