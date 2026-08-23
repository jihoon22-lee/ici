"""Security & Resources section — dedicated findings view for hygiene engines."""

import html
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus
from ici.reporters.html.utils import _get_status_theme, _location_controls, _status_color

_ENGINE_META = {
    "security": ("🔐", "Security Patterns"),
    "resource": ("💧", "Resource Leaks"),
}


def _render_static_analysis_section(results: list[EngineResult], base: Path) -> str:
    """Renders grouped findings for security/resource engines."""
    blocks = []
    for res in results:
        meta = _ENGINE_META.get(res.engine_name)
        if not meta or res.status == EngineStatus.SKIP:
            continue
        icon, label = meta
        color, bg, _ = _get_status_theme(res.status)

        if res.targets:
            items = []
            for t in res.targets:
                t_color = _status_color(t.status)
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
                    f"    <span class='badge' style='color:{t_color}; border:1px solid {t_color}44'>{t.status.value}</span>"
                    f"    {location}"
                    f"    <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span>"
                    f"  </div>"
                    f"  <div class='issue-msg'>{html.escape(t.message)}</div>"
                    f"  {snippet_block}"
                    f"</div>"
                )
            body = f"<div class='targets-list'>{''.join(items)}</div>"
        else:
            body = "<div class='empty-clean'>✨ No findings</div>"

        blocks.append(
            f"<div class='card' style='padding: 1rem 1.25rem; margin-bottom: 1rem;'>"
            f"  <div style='display:flex; align-items:center; gap:0.6rem; margin-bottom:0.75rem;'>"
            f"    <span class='badge' style='color:{color}; background:{bg}; border:1px solid {color}33'>{res.status.value}</span>"
            f"    <strong style='font-size:1.05rem;'>{icon} {label}</strong>"
            f"    <span style='font-size:0.85rem; color:var(--text-muted);'>{html.escape(res.summary)}</span>"
            f"  </div>"
            f"  {body}"
            f"</div>"
        )

    if not blocks:
        return "<div class='empty-clean'>🔐 No security or resource findings.</div>"

    return (
        "<div class='issues-header-bar'>"
        "  <div>"
        '    <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">🔐 Security & Resources</h2>'
        "    <p style='font-size: 0.875rem; color: var(--text-muted);'>"
        "      보안 위생(security)과 리소스 누수(resource) 정적 분석 결과입니다."
        "    </p>"
        "  </div>"
        "</div>"
        f"{''.join(blocks)}"
    )
