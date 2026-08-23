"""Dependency Cycles section — chip-based cycle chain visualization."""

import html
from pathlib import Path

from ici.core.models import EngineResult
from ici.reporters.html.utils import _get_status_theme, _location_controls


def _render_cycle_chips(nodes: list[str], kind: str) -> str:
    """Renders a cycle chain as flex-wrapped chips with arrows."""
    chips = []
    for i, node in enumerate(nodes):
        if i > 0:
            chips.append("<span class='cycle-arrow'>→</span>")
        short = node if len(node) <= 48 else node[:45] + "…"
        chips.append(
            f"<code class='cycle-chip' title='{html.escape(node)}'>{html.escape(short)}</code>"
        )
    chips.append("<span class='cycle-arrow'>↩</span>")
    return f"<div class='cycle-chain'>{(''.join(chips))}<span class='badge' style='background:#1f293d; color:#9ca3af'>{kind}</span></div>"


def _render_cycles_section(cycle_res: EngineResult | None, base: Path) -> str:
    """Renders the dedicated Dependency Cycles tab with per-cycle cards."""
    if not cycle_res:
        return "<div class='card'>No dependency cycle data available.</div>"

    extra = cycle_res.extra or {}
    py_cycles = extra.get("py_cycles", 0)
    cpp_cycles = extra.get("cpp_cycles", 0)

    cards = []
    for t in cycle_res.targets:
        color, bg, _ = _get_status_theme(t.status)
        metrics = t.metrics or {}
        modules = metrics.get("modules") or []
        files = metrics.get("files") or []

        if modules:
            chain_html = _render_cycle_chips([*modules, modules[0]], "Python")
        elif files:
            names = [Path(f).name for f in files]
            chain_html = _render_cycle_chips([*names, names[0]], "C++")
        else:
            chain_html = ""

        location = (
            _location_controls(t.file_path, t.start_line, base, label=t.file_path)
            if t.file_path
            else "<span class='issue-no-location'>engine result</span>"
        )

        full_chain = ""
        raw_nodes = modules or files
        if raw_nodes:
            full_chain = " → ".join([*raw_nodes, raw_nodes[0]])

        cards.append(
            f"<div class='issue-item'>"
            f"  <div class='issue-header'>"
            f"    <span class='badge' style='color:{color}; background:{bg}; border:1px solid {color}44'>{t.status.value}</span>"
            f"    <span class='target-sym'>[{html.escape(t.target_name or 'cycle')}]</span>"
            f"    {location}"
            f"  </div>"
            f"  {chain_html}"
            f"  <details class='issue-snippet-details' style='margin-top:0.5rem;'>"
            f"    <summary class='issue-snippet-summary'>Full path chain ▾</summary>"
            f"    <div class='issue-msg' style='word-break:break-all;'>{html.escape(full_chain)}</div>"
            f"  </details>"
            f"</div>"
        )

    if not cards:
        body = "<div class='empty-clean'>✨ No cyclic dependencies detected</div>"
    else:
        body = f"<div class='targets-list'>{''.join(cards)}</div>"

    return f"""
    <div class="issues-header-bar">
      <div>
        <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">🔁 Dependency Cycles ({len(cycle_res.targets)} Cycles)</h2>
        <p style="font-size: 0.875rem; color: var(--text-muted);">
          Tarjan SCC로 탐지한 순환 참조 — Python {py_cycles}개 · C++ {cpp_cycles}개.
          칩에 마우스를 올리면 전체 경로가 표시됩니다.
        </p>
      </div>
    </div>
    {body}
    """
