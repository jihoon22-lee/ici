"""Generated section - see html.py original."""

import html
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus
from ici.reporters.html.utils import _location_controls, _status_color


def _render_line_section(line_res: EngineResult | None, base: Path) -> str:
    """Renders line ratio bar, top 5 files chart, and a REAL full-width hierarchical tree explorer."""
    if not line_res:
        return "<div class='card'>No line statistics available</div>"

    all_files = line_res.extra.get("files_data", [])
    if not all_files and line_res.targets:
        all_files = [
            {
                "path": t.file_path,
                "lang": "Python",
                "code": t.metrics.get("code", 10),
                "comment": t.metrics.get("comment", 0),
                "blank": t.metrics.get("blank", 0),
                "total": t.metrics.get("total", 10),
                "status": t.status.value,
            }
            for t in line_res.targets
        ]

    code = line_res.extra.get("code", sum(x["code"] for x in all_files))
    comment = line_res.extra.get("comment", sum(x["comment"] for x in all_files))
    blank = line_res.extra.get("blank", sum(x["blank"] for x in all_files))
    total = line_res.extra.get("total", sum(x["total"] for x in all_files)) or 1

    code_pct = (code / total) * 100.0
    comment_pct = (comment / total) * 100.0
    blank_pct = (blank / total) * 100.0

    # Top 5 files
    top_files = line_res.extra.get("top_files", all_files[:5])
    max_top_code = top_files[0]["code"] if top_files else 1

    top_bars_html = []
    for tf in top_files:
        fill_w = min(100.0, (tf["code"] / max_top_code) * 100.0)
        location = _location_controls(str(tf["path"]), 1, base, label=str(tf["path"]))
        top_bars_html.append(
            f"<div class='top-file-row'>"
            f"  <div class='top-file-info'>"
            f"    {location}"
            f"    <span><strong>{tf['code']:,}</strong> code lines</span>"
            f"  </div>"
            f"  <div class='top-bar-bg'>"
            f"    <div class='top-bar-fill' style='width:{fill_w}%;'></div>"
            f"  </div>"
            f"</div>"
        )

    # Build Real Hierarchical Tree Structure
    tree_rows = _build_hierarchical_tree_rows(all_files, base)

    return f"""
    <!-- Top Row: Charts -->
    <div class="line-charts-grid">
      <!-- Left: Codebase Ratio -->
      <div class="chart-card">
        <div class="chart-title">📈 Codebase Distribution</div>

        <div class="ratio-bar-wrapper">
          <div class="ratio-bar">
            <div style="width: {code_pct}%; background: #10b981;" title="Code: {code_pct:.1f}%"></div>
            <div style="width: {comment_pct}%; background: #38bdf8;" title="Comments: {comment_pct:.1f}%"></div>
            <div style="width: {blank_pct}%; background: #64748b;" title="Blanks: {blank_pct:.1f}%"></div>
          </div>
          <div class="ratio-legend">
            <div class="legend-item"><div class="legend-dot" style="background:#10b981"></div> Code ({code_pct:.1f}%)</div>
            <div class="legend-item"><div class="legend-dot" style="background:#38bdf8"></div> Comments ({comment_pct:.1f}%)</div>
            <div class="legend-item"><div class="legend-dot" style="background:#64748b"></div> Blanks ({blank_pct:.1f}%)</div>
          </div>
        </div>
      </div>

      <!-- Right: Top 5 Files -->
      <div class="chart-card">
        <div class="chart-title">🏆 Top 5 Largest Files</div>
        {"".join(top_bars_html)}
      </div>
    </div>

    <!-- Bottom Full-Width: File Explorer Tree Table -->
    <div class="chart-card tree-full-card" style="margin-top: 1.5rem;">
      <div class="tree-header-bar">
        <div class="chart-title" style="margin-bottom: 0;">📁 File Explorer Tree ({len(all_files)} Files)</div>
        <div class="tree-controls">
          <input type="text" id="treeSearchInput" placeholder="🔍 Search file by name or path..." class="tree-search-input" />
        </div>
      </div>

      <div class="tree-scroll-container">
        <table class="tree-table" id="fileTreeTable">
          <thead>
            <tr>
              <th style="min-width: 380px;">Directory & File Structure</th>
              <th style="width: 100px;">Language</th>
              <th style="width: 90px;">Status</th>
              <th class="text-right" style="width: 110px;">Code</th>
              <th class="text-right" style="width: 110px;">Comment</th>
              <th class="text-right" style="width: 110px;">Blank</th>
              <th class="text-right" style="width: 120px;">Total</th>
            </tr>
          </thead>
          <tbody>
            {"".join(tree_rows)}
          </tbody>
        </table>
      </div>
    </div>
    """


def _build_hierarchical_tree_rows(files_data: list[dict], base: Path) -> list[str]:
    """Constructs real indented tree rows for files grouped by directory."""
    folder_map: dict[str, list[dict]] = {}
    for f in sorted(files_data, key=lambda x: x["path"]):
        p = Path(f["path"])
        parent = str(p.parent)
        if parent not in folder_map:
            folder_map[parent] = []
        folder_map[parent].append(f)

    rows = []
    icon_map = {
        "Python": "🐍",
        "C++": "⚙️",
        "Shell": "📜",
        "TOML": "⚙️",
        "Markdown": "📝",
        "YAML": "📄",
        "JSON": "📦",
    }

    for folder, files in sorted(folder_map.items()):
        f_code = sum(x["code"] for x in files)
        f_comment = sum(x["comment"] for x in files)
        f_blank = sum(x["blank"] for x in files)
        f_total = sum(x["total"] for x in files)
        depth = len(Path(folder).parts) if folder != "." else 0
        indent_px = max(4, depth * 18)

        folder_name = "📁 " + (folder if folder != "." else "root")
        rows.append(
            f"<tr class='tree-folder-row'>"
            f"  <td colspan='3' style='padding-left: {indent_px}px;'><strong>{html.escape(folder_name)}</strong> <span style='font-size:0.75rem; color:var(--text-muted); font-weight:normal;'>({len(files)} files)</span></td>"
            f"  <td class='text-right'><strong>{f_code:,}</strong></td>"
            f"  <td class='text-right text-muted'>{f_comment:,}</td>"
            f"  <td class='text-right text-muted'>{f_blank:,}</td>"
            f"  <td class='text-right'><code>{f_total:,}</code></td>"
            f"</tr>"
        )

        for f in files:
            p = Path(f["path"])
            fname = p.name
            location = _location_controls(str(f["path"]), 1, base, label=fname)
            f_indent = indent_px + 22
            icon = icon_map.get(f["lang"], "📄")
            file_status = str(f.get("status", "PASS"))
            try:
                status_color = _status_color(EngineStatus(file_status))
            except ValueError:
                status_color = "#9ca3af"
            status_text = html.escape(file_status)

            st_badge = f"<span class='badge' style='color:{status_color}'>{status_text}</span>"

            rows.append(
                f"<tr class='tree-file-row'>"
                f"  <td style='padding-left: {f_indent}px;'>"
                f"    <span class='tree-icon'>{icon}</span>"
                f"    {location}"
                f"  </td>"
                f"  <td><span class='badge' style='background:#1f293d; color:#a78bfa'>{html.escape(f['lang'])}</span></td>"
                f"  <td>{st_badge}</td>"
                f"  <td class='text-right'><strong>{f['code']:,}</strong></td>"
                f"  <td class='text-right text-muted'>{f['comment']:,}</td>"
                f"  <td class='text-right text-muted'>{f['blank']:,}</td>"
                f"  <td class='text-right'><code>{f['total']:,}</code></td>"
                f"</tr>"
            )

    return rows
