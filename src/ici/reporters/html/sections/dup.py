"""Clone Groups tab — duplicate-code clusters with original indentation preserved."""

import html
from pathlib import Path

from ici.core.models import EngineResult
from ici.reporters.html.utils import _location_controls


def _render_dup_section(dup_res: EngineResult | None, base: Path) -> str:
    """Renders maximal non-overlapping clone groups with raw indented source code snippets."""
    if not dup_res:
        return "<div class='card'>No duplication results available.</div>"

    groups = dup_res.extra.get("clone_groups", [])
    if not groups:
        return "<div class='empty-clean'>✨ 0 Duplication — No copy-paste clone blocks detected across repository!</div>"

    cards = []
    for g in groups:
        occ_html = []
        for occ in g["occurrences"]:
            s_line = occ["start_line"]
            location = _location_controls(
                str(occ["file_path"]), int(s_line), base, label=str(occ["loc"])
            )
            occ_html.append(f"<span class='occ-pill'>  {location}</span>")

        snippet_html = f"<pre class='snippet'><code>{html.escape(g['snippet'])}</code></pre>"
        cards.append(
            f"<div class='clone-group-card'>"
            f"  <div class='clone-group-title'>📦 Clone Group #{g['id']} ({g['lines_count']} Duplicate Lines &bull; {g['occurrences_count']} Locations)</div>"
            f"  <div class='clone-occurrences'>{''.join(occ_html)}</div>"
            f"  {snippet_html}"
            f"</div>"
        )

    dup_pct = dup_res.score or 0.0
    return f"""
    <div style="margin-bottom: 1.25rem;">
      <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">📦 Duplicate Code Groups (Rate: {dup_pct:.1f}%)</h2>
      <p style="font-size: 0.875rem; color: var(--text-muted);">
        동일한 로직이 여러 파일에 걸쳐 중복 복사된 코드 블록을 탐지하여 공통 모듈화 대상을 식별합니다.
      </p>
    </div>
    {"".join(cards)}
    """
