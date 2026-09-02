"""Issues tab — every actionable (non-PASS) finding across all engines."""

import html
from pathlib import Path

from ici.core.models import SourceLocation
from ici.reporters.html.utils import HtmlIssue, _location_controls, _status_color


def _related_location_label(location: SourceLocation) -> str:
    start_line = location.start_line
    end_line = location.end_line or start_line
    start_column = location.start_column
    end_column = location.end_column
    label = f"{location.path}:L{start_line}"
    if start_column is not None:
        label += f":C{start_column}"
    if end_line > start_line:
        label += f"-L{end_line}"
        if end_column is not None:
            label += f":C{end_column}"
    elif end_column is not None and end_column != start_column:
        label += f"-C{end_column}"
    return label


def _render_issues_section(all_issues: list[HtmlIssue], base: Path) -> str:
    """Renders aggregated actionable issues tab."""
    if not all_issues:
        return "<div class='empty-clean'>✨ No actionable issues found! All active quality gate checks passed cleanly.</div>"

    items = []
    for issue in all_issues:
        badge_color = _status_color(issue.status)
        end_line = issue.end_line or issue.start_line
        location_label = f"{issue.file_path}:L{issue.start_line}"
        if end_line > issue.start_line:
            location_label += f"-L{end_line}"
        location = (
            _location_controls(
                issue.file_path,
                issue.start_line,
                base,
                label=location_label,
            )
            if issue.file_path
            else "<span class='issue-no-location'>engine result</span>"
        )

        snippet_block = ""
        if issue.snippet:
            num_lines = len(issue.snippet.splitlines())
            snippet_block = (
                f"<details class='issue-snippet-details'>"
                f"  <summary class='issue-snippet-summary'>📄 View Finding Code ({num_lines} lines) ▾</summary>"
                f"  <pre class='snippet'><code>{html.escape(issue.snippet)}</code></pre>"
                f"</details>"
            )

        related_block = ""
        if issue.related_locations:
            related_items = []
            for related in issue.related_locations:
                controls = _location_controls(
                    related.path,
                    related.start_line,
                    base,
                    label=_related_location_label(related),
                )
                message = html.escape(related.label or "Related diagnostic location")
                related_items.append(
                    f"<li><span class='issue-related-location'>{controls}</span>"
                    f"<span class='issue-related-message'>{message}</span></li>"
                )
            related_block = (
                "<div class='issue-related'>"
                "<div class='issue-related-title'>Related evidence</div>"
                f"<ul>{''.join(related_items)}</ul>"
                "</div>"
            )

        items.append(
            f"<div class='issue-item'>"
            f"  <div class='issue-header'>"
            f"    <span class='badge' style='color:{badge_color}; border:1px solid {badge_color}44'>{html.escape(issue.badge)}</span>"
            f"    <span class='issue-engine'>[{html.escape(issue.engine_name)}]</span>"
            f"    {location}"
            f"    <span class='target-sym'>[{html.escape(issue.rule_id)}]</span>"
            f"  </div>"
            f"  <div class='issue-msg'>{html.escape(issue.message)}</div>"
            f"  {related_block}"
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
