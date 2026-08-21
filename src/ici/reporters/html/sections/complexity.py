"""Generated section - see html.py original."""

import html
from pathlib import Path

from ici.core.models import EngineResult
from ici.reporters.html.utils import _location_controls


def _render_complexity_section(comp_res: EngineResult | None, base: Path) -> str:
    """Renders the dedicated Complexity & Quality tab with leaderboard and actual source snippets."""
    if not comp_res:
        return "<div class='card'>No complexity results available.</div>"

    top_funcs = comp_res.extra.get("top_complex_funcs", [])
    if not top_funcs:
        return "<div class='empty-clean'>✨ All functions are simple and clean!</div>"

    cards = []
    for rank, t in enumerate(top_funcs, 1):
        if isinstance(t, dict):
            t_file = t.get("file_path", "")
            t_start = t.get("start_line", 1)
            t_name = t.get("target_name", "")
            t_msg = t.get("message", "")
            t_snippet = t.get("snippet", "")
            metrics = t.get("metrics", {})
        else:
            t_file = t.file_path
            t_start = t.start_line
            t_name = t.target_name
            t_msg = t.message
            t_snippet = t.snippet
            metrics = t.metrics

        cc = metrics.get("complexity", 1)
        nesting = metrics.get("nesting", 1)
        location = _location_controls(str(t_file), int(t_start), base, label=f"{t_file}:{t_start}")

        if cc > 25:
            badge_style = (
                "background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid #ef4444;"
            )
        elif cc > 15:
            badge_style = (
                "background: rgba(245, 158, 11, 0.2); color: #f59e0b; border: 1px solid #f59e0b;"
            )
        else:
            badge_style = (
                "background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981;"
            )

        snippet_html = ""
        if t_snippet:
            num_lines = len(t_snippet.splitlines())
            snippet_html = (
                f"<details class='cc-snippet-details'>"
                f"  <summary class='cc-snippet-summary'>📄 View Source Code ({num_lines} lines) ▾</summary>"
                f"  <pre class='snippet'><code>{html.escape(t_snippet)}</code></pre>"
                f"</details>"
            )

        cards.append(
            f"<div class='cc-card'>"
            f"  <div class='cc-header'>"
            f"    <div>"
            f"      <span style='color:var(--text-muted); font-weight:700; margin-right:0.5rem;'>#{rank}</span>"
            f"      <span class='cc-name'>{html.escape(t_name)}</span>"
            f"      {location}"
            f"    </div>"
            f"    <div style='display:flex; gap:0.5rem;'>"
            f"      <span class='cc-badge' style='{badge_style}'>CC: {cc}</span>"
            f"      <span class='cc-badge' style='background:#1e293b; color:#38bdf8;'>Nesting: {nesting}</span>"
            f"    </div>"
            f"  </div>"
            f"  <div style='font-size:0.85rem; color:var(--text-muted); margin-bottom:0.4rem;'>{html.escape(t_msg)}</div>"
            f"  {snippet_html}"
            f"</div>"
        )

    return f"""
    <div class="cc-header-bar">
      <div>
        <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">🧩 Cyclomatic Complexity & Nesting Analysis</h2>
        <p style="font-size: 0.875rem; color: var(--text-muted);">
          함수의 분기점(If, While, Match 등)과 블록 중첩 깊이를 분석하여 리팩토링이 필요한 고복잡도 함수를 진단합니다.
        </p>
      </div>
      <div>
        <button class="jump-tab-btn" data-toggle-details=".cc-snippet-details">📂 Toggle All Code</button>
      </div>
    </div>
    {"".join(cards)}
    """
