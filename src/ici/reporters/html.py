"""Zero-CDN Standalone Interactive HTML Reporter for ici."""

import html
from pathlib import Path
from typing import Any

from ici.core.models import (
    EngineResult,
    EngineStatus,
    VerificationSuiteResult,
    format_score_display,
)
from ici.reporters.html_assets import HTML_CSS, HTML_JS


def generate_html_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_name: str = "Project",
    base_dir: Path | None = None,
) -> None:
    """Generates a state-of-the-art, zero-CDN, standalone HTML report with universal editor links."""
    base = (base_dir or Path.cwd()).resolve()

    status_color = (
        "#10b981"
        if suite.suite_status == EngineStatus.PASS
        else ("#f59e0b" if suite.suite_status == EngineStatus.WARN else "#ef4444")
    )
    status_bg = (
        "rgba(16, 185, 129, 0.15)"
        if suite.suite_status == EngineStatus.PASS
        else (
            "rgba(245, 158, 11, 0.15)"
            if suite.suite_status == EngineStatus.WARN
            else "rgba(239, 68, 68, 0.15)"
        )
    )
    status_border = (
        "#10b981"
        if suite.suite_status == EngineStatus.PASS
        else ("#f59e0b" if suite.suite_status == EngineStatus.WARN else "#ef4444")
    )

    pass_engines = sum(1 for r in suite.results if r.status == EngineStatus.PASS)
    warn_engines = sum(1 for r in suite.results if r.status == EngineStatus.WARN)
    fail_engines = sum(1 for r in suite.results if r.status == EngineStatus.FAIL)

    all_issues: list[tuple[str, Any]] = []
    line_result: EngineResult | None = None
    complexity_result: EngineResult | None = None
    dup_result: EngineResult | None = None

    for r in suite.results:
        if r.engine_name == "line":
            line_result = r
        elif r.engine_name == "complexity":
            complexity_result = r
        elif r.engine_name == "dup":
            dup_result = r

        for t in r.targets:
            if t.status in (EngineStatus.WARN, EngineStatus.FAIL):
                all_issues.append((r.engine_name, t))

    # 1. Build Main Engines Table Rows
    engine_rows = []
    for res in suite.results:
        badge_color = (
            "#10b981"
            if res.status == EngineStatus.PASS
            else ("#f59e0b" if res.status == EngineStatus.WARN else "#ef4444")
        )
        badge_bg = (
            "rgba(16, 185, 129, 0.15)"
            if res.status == EngineStatus.PASS
            else (
                "rgba(245, 158, 11, 0.15)"
                if res.status == EngineStatus.WARN
                else "rgba(239, 68, 68, 0.15)"
            )
        )
        score_str = format_score_display(res)
        duration_str = f"{res.duration:.2f}s" if res.duration > 0 else "-"

        summary_col = _render_main_row_summary(res, base)

        engine_rows.append(
            f"<tr class='engine-row'>"
            f"  <td class='engine-name'><strong>{html.escape(res.engine_name)}</strong></td>"
            f"  <td><span class='badge' style='color:{badge_color}; background:{badge_bg}; border: 1px solid {badge_color}33'>{res.status.value}</span></td>"
            f"  <td>{summary_col}</td>"
            f"  <td class='text-right'><code>{html.escape(score_str)}</code></td>"
            f"  <td class='text-right text-muted'>{duration_str}</td>"
            f"</tr>"
        )

    # 2. Section Renders
    line_tab_content = _render_line_section(line_result, base)
    complexity_tab_content = _render_complexity_section(complexity_result, base)
    dup_tab_content = _render_dup_section(dup_result, base)
    issues_tab_content = _render_issues_section(all_issues, base)

    # TEM KPI Card
    tem_score_card = ""
    if suite.tem_score is not None:
        tem_pct = min(100.0, (suite.tem_score / 5.0) * 100.0)
        tem_score_card = f"""
        <div class="card stat-card">
            <div class="stat-label">TEM Quality Score</div>
            <div class="stat-value" style="color:#38bdf8">{suite.tem_score:.2f} <span class="stat-sub">/ 5.0</span></div>
            <div class="mini-progress-bg">
                <div class="mini-progress-fill" style="width: {tem_pct}%; background: #38bdf8;"></div>
            </div>
        </div>
        """

    clone_groups_count = len(dup_result.extra.get("clone_groups", [])) if dup_result else 0

    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ici Verification Report — {html.escape(project_name)}</title>
<style>
{HTML_CSS}
  .status-banner {{
    border: 1px solid {status_border};
    background: {status_bg};
    color: {status_color};
    box-shadow: 0 0 20px {status_color}22;
  }}
  .status-dot {{
    background: {status_color};
    box-shadow: 0 0 8px {status_color};
  }}
</style>
</head>
<body>
<div class="container">
  <!-- Toast Notification -->
  <div id="toast" class="toast"></div>

  <!-- Header -->
  <div class="header">
    <div class="brand">
      <div class="brand-logo">ici</div>
      <div class="brand-info">
        <h1>{html.escape(project_name)}</h1>
        <p>Unified CI/CD Verification & Code Quality Gate</p>
      </div>
    </div>

    <div class="header-actions">
      <!-- Universal Editor Link Selector -->
      <div class="editor-pref-wrapper">
        <label for="editorSelect" class="editor-label">🛠️ Open With:</label>
        <select id="editorSelect" class="editor-select" onchange="setEditorPref(this.value)">
          <option value="copy">📋 Copy Path (Vim/gvim/CLI)</option>
          <option value="vscode" selected>🚀 VS Code (vscode://)</option>
          <option value="cursor">⚡ Cursor (cursor://)</option>
          <option value="pycharm">🐍 PyCharm / IntelliJ (idea://)</option>
          <option value="sublime">🪟 Sublime Text (subl://)</option>
          <option value="file">🌐 Browser (file://)</option>
        </select>
      </div>

      <div class="status-banner">
        <div class="status-dot"></div>
        {suite.suite_status.value}
      </div>
    </div>
  </div>

  <!-- Key Metrics Stats Grid -->
  <div class="stats-grid">
    <div class="card stat-card">
      <div class="stat-label">Engines Run</div>
      <div class="stat-value">{len(suite.results)} <span class="stat-sub">Engines</span></div>
      <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.4rem;">
        <span style="color:var(--pass)">{pass_engines} Pass</span> &bull;
        <span style="color:var(--warn)">{warn_engines} Warn</span> &bull;
        <span style="color:var(--fail)">{fail_engines} Fail</span>
      </div>
    </div>

    {tem_score_card}

    <div class="card stat-card">
      <div class="stat-label">Active Issues</div>
      <div class="stat-value" style="color:{"#10b981" if len(all_issues) == 0 else "#f59e0b"}">
        {len(all_issues)} <span class="stat-sub">Findings</span>
      </div>
      <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.4rem;">
        Across {len(suite.results)} Verification Checks
      </div>
    </div>

    <div class="card stat-card">
      <div class="stat-label">Total Execution Time</div>
      <div class="stat-value">{suite.duration:.2f} <span class="stat-sub">sec</span></div>
      <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.4rem;">
        Concurrent & AST Subprocess Engine
      </div>
    </div>
  </div>

  <!-- Tabs Navigation -->
  <div class="tabs">
    <button class="tab-btn active" id="btn-summary" onclick="switchTab('tab-summary', this)">📋 Verification Suites</button>
    <button class="tab-btn" id="btn-line" onclick="switchTab('tab-line', this)">📊 Line Analysis & Explorer</button>
    <button class="tab-btn" id="btn-complexity" onclick="switchTab('tab-complexity', this)">🧩 Complexity & Code</button>
    <button class="tab-btn" id="btn-dup" onclick="switchTab('tab-dup', this)">📦 Clone Groups ({clone_groups_count})</button>
    <button class="tab-btn" id="btn-issues" onclick="switchTab('tab-issues', this)">⚠️ Issues ({len(all_issues)})</button>
  </div>

  <!-- Tab 1: Main Suites Summary -->
  <div id="tab-summary" class="tab-content active">
    <table>
      <thead>
        <tr>
          <th>Verification Engine</th>
          <th>Status</th>
          <th>Summary & Inspection Focus</th>
          <th class="text-right">Score / Metrics</th>
          <th class="text-right">Time</th>
        </tr>
      </thead>
      <tbody>
        {"".join(engine_rows)}
      </tbody>
    </table>
  </div>

  <!-- Tab 2: Line Analysis & Real Tree Explorer -->
  <div id="tab-line" class="tab-content">
    {line_tab_content}
  </div>

  <!-- Tab 3: Dedicated Complexity & Source Code Inspector -->
  <div id="tab-complexity" class="tab-content">
    {complexity_tab_content}
  </div>

  <!-- Tab 4: Merged Non-Overlapping Clone Groups -->
  <div id="tab-dup" class="tab-content">
    {dup_tab_content}
  </div>

  <!-- Tab 5: Actionable Issues Only -->
  <div id="tab-issues" class="tab-content">
    {issues_tab_content}
  </div>

  <div class="footer">
    Generated by <strong>ici</strong> &bull; Zero-CDN Standalone Security & Quality Inspector
  </div>
</div>

<script>
{HTML_JS}
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")


def _render_main_row_summary(res: EngineResult, base: Path) -> str:
    """Renders main summary column with quick jump buttons and noise-free presentation."""
    eng = res.engine_name

    # Line Engine
    if eng == "line":
        return (
            f"<div class='engine-summary-text'>{html.escape(res.summary)}</div>"
            f"<button class='jump-tab-btn' onclick=\"switchTab('tab-line')\">📊 View File Tree & Charts →</button>"
        )

    # Complexity Engine
    if eng == "complexity":
        return (
            f"<div class='engine-summary-text'>{html.escape(res.summary)}</div>"
            f"<button class='jump-tab-btn' onclick=\"switchTab('tab-complexity')\">🧩 Open Complexity Inspector & Code →</button>"
        )

    # Duplicate Engine
    if eng == "dup":
        return (
            f"<div class='engine-summary-text'>{html.escape(res.summary)}</div>"
            f"<button class='jump-tab-btn' onclick=\"switchTab('tab-dup')\">📦 View Clone Groups →</button>"
        )

    # Test Engine: Grouped test suites presentation
    if eng == "test":
        suites = res.extra.get("test_suites", [])
        suite_chips = []
        for s in suites:
            st_color = "#10b981" if s["failed"] == 0 else "#ef4444"
            icon = "✅" if s["failed"] == 0 else "❌"
            suite_chips.append(
                f"<span class='occ-pill' style='color:{st_color};'>{icon} {html.escape(s['file'])} ({s['passed']}/{s['total']})</span>"
            )

        suites_block = (
            f"<div style='margin-top:0.45rem; display:flex; flex-wrap:wrap; gap:0.4rem;'>{''.join(suite_chips)}</div>"
            if suite_chips
            else ""
        )
        return f"<div class='engine-summary-text'>{html.escape(res.summary)}</div>{suites_block}"

    # Sanitize Engine
    if eng == "sanitize" and not res.targets:
        return "<div class='engine-summary-text'>✅ 0 Defect — Memory Safety & Resource Management Clean</div>"

    # Type Engine
    if eng == "type" and not res.targets:
        return "<div class='engine-summary-text'>✅ Static Type Check Passed (0 Errors)</div>"

    # Default Target List (if any violations remain)
    targets_html = []
    for t in res.targets:
        if t.status in (EngineStatus.WARN, EngineStatus.FAIL):
            t_badge_color = "#ef4444" if t.status == EngineStatus.FAIL else "#f59e0b"
            t_loc = f"{html.escape(t.file_path)}:{t.start_line}"
            abs_t_path = str((base / t.file_path).resolve())
            rel_t_path = html.escape(t.file_path)

            targets_html.append(
                f"<div class='target-item'>"
                f"  <span class='badge' style='color:{t_badge_color}'>{t.status.value}</span> "
                f"  <span class='loc-link-group'>"
                f"    <a href='javascript:void(0)' onclick=\"openLoc('{abs_t_path}', '{rel_t_path}', {t.start_line})\" class='loc-link'><code>{t_loc}</code></a>"
                f"    <button class='btn-copy-loc' onclick=\"copyLoc('{rel_t_path}', {t.start_line}, event)\" title='경로 복사 (gvim/CLI용)'>📋</button>"
                f"  </span>"
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
        abs_f = str((base / tf["path"]).resolve())
        rel_f = html.escape(tf["path"])
        top_bars_html.append(
            f"<div class='top-file-row'>"
            f"  <div class='top-file-info'>"
            f"    <span class='loc-link-group'>"
            f"      <a href='javascript:void(0)' onclick=\"openLoc('{abs_f}', '{rel_f}', 1)\" class='loc-link'><code>{rel_f}</code></a>"
            f"      <button class='btn-copy-loc' onclick=\"copyLoc('{rel_f}', 1, event)\" title='경로 복사 (gvim/CLI용)'>📋</button>"
            f"    </span>"
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
          <input type="text" id="treeSearchInput" onkeyup="filterTreeFiles(this.value)" placeholder="🔍 Search file by name or path..." class="tree-search-input" />
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
            abs_f = str((base / f["path"]).resolve())
            rel_f = html.escape(f["path"])
            f_indent = indent_px + 22
            icon = icon_map.get(f["lang"], "📄")

            st_badge = (
                "<span class='badge' style='color:#10b981'>PASS</span>"
                if f["status"] == "PASS"
                else f"<span class='badge' style='color:#f59e0b'>{f['status']}</span>"
            )

            rows.append(
                f"<tr class='tree-file-row'>"
                f"  <td style='padding-left: {f_indent}px;'>"
                f"    <span class='tree-icon'>{icon}</span>"
                f"    <span class='loc-link-group'>"
                f"      <a href='javascript:void(0)' onclick=\"openLoc('{abs_f}', '{rel_f}', 1)\" class='loc-link'><code>{html.escape(fname)}</code></a>"
                f"      <button class='btn-copy-loc' onclick=\"copyLoc('{rel_f}', 1, event)\" title='경로 복사 (gvim/CLI용)'>📋</button>"
                f"    </span>"
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
        abs_p = str((base / t_file).resolve())
        rel_p = html.escape(t_file)

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
            f"      <span class='loc-link-group' style='margin-left:0.6rem;'>"
            f"        <a href='javascript:void(0)' onclick=\"openLoc('{abs_p}', '{rel_p}', {t_start})\" class='loc-link'><code>{rel_p}:{t_start}</code></a>"
            f"        <button class='btn-copy-loc' onclick=\"copyLoc('{rel_p}', {t_start}, event)\" title='경로 복사 (gvim/CLI용)'>📋</button>"
            f"      </span>"
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
        <button class="jump-tab-btn" onclick="toggleAllDetails('.cc-snippet-details')">📂 Toggle All Code</button>
      </div>
    </div>
    {"".join(cards)}
    """


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
            abs_f = str((base / occ["file_path"]).resolve())
            rel_f = html.escape(occ["file_path"])
            loc_str = html.escape(occ["loc"])
            s_line = occ["start_line"]
            occ_html.append(
                f"<span class='occ-pill'>"
                f"  <a href='javascript:void(0)' onclick=\"openLoc('{abs_f}', '{rel_f}', {s_line})\" class='loc-link'><code>{loc_str}</code></a>"
                f"  <button class='btn-copy-loc' onclick=\"copyLoc('{rel_f}', {s_line}, event)\" title='경로 복사 (gvim/CLI용)'>📋</button>"
                f"</span>"
            )

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


def _render_issues_section(all_issues: list[tuple[str, Any]], base: Path) -> str:
    """Renders aggregated actionable issues tab."""
    if not all_issues:
        return "<div class='empty-clean'>✨ No actionable issues found! All active quality gate checks passed cleanly.</div>"

    items = []
    for eng_name, t in all_issues:
        t_badge_color = "#ef4444" if t.status == EngineStatus.FAIL else "#f59e0b"
        t_loc = f"{html.escape(t.file_path)}:{t.start_line}"
        abs_t_path = str((base / t.file_path).resolve())
        rel_t_path = html.escape(t.file_path)

        snippet_block = ""
        if t.snippet:
            snippet_block = f"<pre class='snippet'><code>{html.escape(t.snippet)}</code></pre>"

        items.append(
            f"<div class='issue-item'>"
            f"  <div class='issue-header'>"
            f"    <span class='badge' style='color:{t_badge_color}; border:1px solid {t_badge_color}44'>{t.status.value}</span>"
            f"    <span class='issue-engine'>[{html.escape(eng_name)}]</span>"
            f"    <span class='loc-link-group'>"
            f"      <a href='javascript:void(0)' onclick=\"openLoc('{abs_t_path}', '{rel_t_path}', {t.start_line})\" class='loc-link'><code>{t_loc}</code></a>"
            f"      <button class='btn-copy-loc' onclick=\"copyLoc('{rel_t_path}', {t.start_line}, event)\" title='경로 복사 (gvim/CLI용)'>📋</button>"
            f"    </span>"
            f"    <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span>"
            f"  </div>"
            f"  <div class='issue-msg'>{html.escape(t.message)}</div>"
            f"  {snippet_block}"
            f"</div>"
        )

    return "".join(items)
