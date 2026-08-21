"""Generated section - see html.py original."""

import html
from pathlib import Path
from typing import Any

from ici.reporters.html.utils import _location_controls, _status_color


def _render_issues_section(all_issues: list[tuple[str, Any]], base: Path) -> str:
    """Renders aggregated actionable issues tab."""
    if not all_issues:
        return "<div class='empty-clean'>✨ No actionable issues found! All active quality gate checks passed cleanly.</div>"

    items = []
    for eng_name, t in all_issues:
        t_badge_color = _status_color(t.status)
        location = (
            _location_controls(t.file_path, t.start_line, base)
            if t.file_path
            else "<span class='issue-no-location'>engine result</span>"
        )

        snippet_block = ""
        if t.snippet:
            num_lines = len(t.snippet.splitlines())
            snippet_block = (
                f"<details class='issue-snippet-details'>"
                f"  <summary class='issue-snippet-summary'>📄 View Finding Code ({num_lines} lines) ▾</summary>"
                f"  <pre class='snippet'><code>{html.escape(t.snippet)}</code></pre>"
                f"</details>"
            )

        items.append(
            f"<div class='issue-item'>"
            f"  <div class='issue-header'>"
            f"    <span class='badge' style='color:{t_badge_color}; border:1px solid {t_badge_color}44'>{t.status.value}</span>"
            f"    <span class='issue-engine'>[{html.escape(eng_name)}]</span>"
            f"    {location}"
            f"    <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span>"
            f"  </div>"
            f"  <div class='issue-msg'>{html.escape(t.message)}</div>"
            f"  {snippet_block}"
            f"</div>"
        )

    return f"""
    <div class="issues-header-bar">
      <div>
        <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">⚠️ Active Quality Gate Issues ({len(all_issues)} Findings)</h2>
        <p style="font-size: 0.875rem; color: var(--text-muted);">
          전체 검증 엔진에서 PASS가 아닌 WARN/FAIL/ERROR/SKIP 항목을 통합하여 확인합니다.
        </p>
      </div>
      <div>
        <button class="jump-tab-btn" data-toggle-details=".issue-snippet-details">📂 Toggle All Code</button>
      </div>
    </div>
    {"".join(items)}
    """
