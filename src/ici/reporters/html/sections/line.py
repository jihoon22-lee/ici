"""Generated section - see html.py original."""

import html
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus
from ici.reporters.html.utils import _location_controls, _status_color


def _split_scope(files_data: list[dict]) -> tuple[list[dict], list[dict]]:
    """Splits files into source-scope and whole-project sets (legacy data = source)."""
    if files_data and "scope" not in files_data[0]:
        return files_data, []
    source = [f for f in files_data if f.get("scope") == "source"]
    return source, files_data


def _render_ratio_card(title: str, totals: dict) -> str:
    total = max(1, totals["total"])
    code_pct = totals["code"] / total * 100.0
    comment_pct = totals["comment"] / total * 100.0
    blank_pct = totals["blank"] / total * 100.0
    return f"""
      <div class="chart-card">
        <div class="chart-title">{title}</div>

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
    """


def _render_top_files_card(title: str, top_files: list[dict], base: Path) -> str:
    max_top_code = top_files[0]["code"] if top_files else 1
    bars = []
    for tf in top_files:
        fill_w = min(100.0, (tf["code"] / max_top_code) * 100.0)
        location = _location_controls(str(tf["path"]), 1, base, label=str(tf["path"]))
        bars.append(
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
    return f"""
      <div class="chart-card">
        <div class="chart-title">{title}</div>
        {"".join(bars) or "<div class='empty-clean'>No files</div>"}
      </div>
    """


def _render_tree_block(view_key: str, title: str, files: list[dict], base: Path) -> str:
    tree_rows = _build_hierarchical_tree_rows(files, base)
    table_id = f"fileTreeTable-{view_key}"
    return f"""
    <div class="chart-card tree-full-card" style="margin-top: 1.5rem;">
      <div class="tree-header-bar">
        <div class="chart-title" style="margin-bottom: 0;">📁 {title} ({len(files)} Files)</div>
        <div class="tree-controls">
          <input type="text" placeholder="🔍 Search file by name or path..."
                 class="tree-search-input" data-tree-target="{table_id}" />
        </div>
      </div>

      <div class="tree-scroll-container">
        <table class="tree-table" id="{table_id}">
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


def _render_line_section(line_res: EngineResult | None, base: Path) -> str:
    """Renders source-scope line stats by default with an All-files explorer toggle."""
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
                "scope": "source",
            }
            for t in line_res.targets
        ]
    source_files, project_files = _split_scope(all_files)

    source_totals = {
        "code": line_res.extra.get("code", sum(x["code"] for x in source_files)),
        "comment": line_res.extra.get("comment", sum(x["comment"] for x in source_files)),
        "blank": line_res.extra.get("blank", sum(x["blank"] for x in source_files)),
    }
    source_totals["total"] = line_res.extra.get(
        "total",
        source_totals["code"] + source_totals["comment"] + source_totals["blank"],
    )
    all_totals = line_res.extra.get("all") or {
        "code": sum(x["code"] for x in project_files),
        "comment": sum(x["comment"] for x in project_files),
        "blank": sum(x["blank"] for x in project_files),
    }
    all_totals["total"] = (
        all_totals.get("total") or all_totals["code"] + all_totals["comment"] + all_totals["blank"]
    )

    top_source = line_res.extra.get("top_files") or source_files[:5]
    top_all = line_res.extra.get("top_files_all") or project_files[:5]

    toggle_html = ""
    if project_files:
        extra_count = len(project_files) - len(source_files)
        toggle_html = (
            "<label style='display:flex; align-items:center; gap:0.45rem;"
            " font-size:0.85rem; color:var(--text-muted); cursor:pointer;'>"
            "<input type='checkbox' id='lineAllFilesToggle' />"
            f"🌐 Include all project files (+{extra_count} non-source)</label>"
        )

    source_view = (
        f'<div data-line-view="source">'
        f'<div class="line-charts-grid">'
        f"{_render_ratio_card('📈 Codebase Distribution (Source)', source_totals)}"
        f"{_render_top_files_card('🏆 Top 5 Largest Source Files', top_source, base)}"
        f"</div>"
        f"{_render_tree_block('source', 'File Explorer Tree — Source Scope', source_files, base)}"
        f"</div>"
    )
    all_view = ""
    if project_files:
        all_view = (
            '<div data-line-view="all" style="display:none;">'
            '<div class="line-charts-grid">'
            f"{_render_ratio_card('📈 Codebase Distribution (All Project Files)', all_totals)}"
            f"{_render_top_files_card('🏆 Top 5 Largest Files (Whole Project)', top_all, base)}"
            "</div>"
            f"{_render_tree_block('all', 'File Explorer Tree — Whole Project', project_files, base)}"
            "</div>"
        )

    return f"""
    <!-- Scope Toggle -->
    <div class="chart-card" style="padding: 0.75rem 1.25rem; margin-bottom: 1rem;">
      <div style="display:flex; align-items:center; justify-content:space-between; gap:1rem;">
        <div style="font-size:0.875rem; color:var(--text-muted);">
          기본 뷰는 소스 스코프(<code>src/include/lib/app</code> + 설정 추가 경로)만 표시합니다.
          게이트 판정은 항상 소스 스코프 기준입니다.
        </div>
        {toggle_html}
      </div>
    </div>

    {source_view}
    {all_view}
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
            scope_tag = (
                ""
                if f.get("scope", "source") == "source"
                else " <span class='badge' style='background:#1f293d; color:#9ca3af'>extra</span>"
            )

            rows.append(
                f"<tr class='tree-file-row'>"
                f"  <td style='padding-left: {f_indent}px;'>"
                f"    <span class='tree-icon'>{icon}</span>"
                f"    {location}"
                f"    {scope_tag}"
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
