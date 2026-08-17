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


def generate_html_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_name: str = "Project",
    base_dir: Path | None = None,
) -> None:
    """Generates a state-of-the-art, zero-CDN, standalone HTML report."""
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

        # Custom summary row presentation
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
  :root {{
    --bg: #090d16;
    --card-bg: #111827;
    --card-hover: #172033;
    --border: #1f293d;
    --border-highlight: #334155;
    --text: #f3f4f6;
    --text-muted: #9ca3af;
    --primary: #38bdf8;
    --pass: #10b981;
    --warn: #f59e0b;
    --fail: #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", Helvetica, Arial, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 2.5rem 1.5rem;
    line-height: 1.6;
  }}
  .container {{ max-width: 1240px; margin: 0 auto; }}

  /* Header */
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2rem;
    padding-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
  }}
  .brand {{ display: flex; align-items: center; gap: 0.75rem; }}
  .brand-logo {{
    font-size: 1.5rem;
    background: linear-gradient(135deg, #0ea5e9, #38bdf8);
    padding: 0.35rem 0.75rem;
    border-radius: 8px;
    font-weight: 800;
    color: #04101e;
    letter-spacing: -0.05em;
  }}
  .brand-info h1 {{ font-size: 1.5rem; font-weight: 700; color: #ffffff; }}
  .brand-info p {{ font-size: 0.875rem; color: var(--text-muted); }}

  .status-banner {{
    display: inline-flex;
    align-items: center;
    gap: 0.6rem;
    padding: 0.6rem 1.5rem;
    border-radius: 9999px;
    font-weight: 700;
    font-size: 1.1rem;
    letter-spacing: 0.05em;
    border: 1px solid {status_border};
    background: {status_bg};
    color: {status_color};
    box-shadow: 0 0 20px {status_color}22;
  }}
  .status-dot {{
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: {status_color};
    box-shadow: 0 0 8px {status_color};
  }}

  /* Stats Grid */
  .stats-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 1.25rem;
    margin-bottom: 2.25rem;
  }}
  .card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
  }}
  .stat-label {{ font-size: 0.8125rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }}
  .stat-value {{ font-size: 1.85rem; font-weight: 700; margin-top: 0.35rem; color: #fff; }}
  .stat-sub {{ font-size: 0.95rem; color: var(--text-muted); font-weight: 400; }}

  .mini-progress-bg {{
    width: 100%;
    height: 6px;
    background: #1e293b;
    border-radius: 9999px;
    margin-top: 0.6rem;
    overflow: hidden;
  }}
  .mini-progress-fill {{ height: 100%; border-radius: 9999px; }}

  /* Tabs Navigation */
  .tabs {{
    display: flex;
    gap: 0.5rem;
    margin-bottom: 1.5rem;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.5rem;
    overflow-x: auto;
  }}
  .tab-btn {{
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 0.95rem;
    font-weight: 600;
    padding: 0.6rem 1.25rem;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
  }}
  .tab-btn:hover {{ color: var(--text); background: var(--card-hover); }}
  .tab-btn.active {{
    color: var(--primary);
    background: rgba(56, 189, 248, 0.1);
    border-bottom: 2px solid var(--primary);
  }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}

  /* Tables */
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }}
  th, td {{ padding: 1.1rem 1.25rem; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{
    background: #0d131f;
    color: var(--text-muted);
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    font-weight: 700;
  }}
  tr:last-child td {{ border-bottom: none; }}
  .text-right {{ text-align: right; }}
  .text-muted {{ color: var(--text-muted); }}
  .badge {{
    display: inline-block;
    padding: 0.25rem 0.65rem;
    border-radius: 6px;
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.05em;
  }}
  .engine-name {{ font-size: 1.05rem; }}
  .engine-summary-text {{ font-size: 0.95rem; color: #e5e7eb; }}

  .jump-tab-btn {{
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    background: rgba(56, 189, 248, 0.08);
    border: 1px solid rgba(56, 189, 248, 0.3);
    color: var(--primary);
    padding: 0.3rem 0.75rem;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 0.45rem;
    transition: all 0.2s;
  }}
  .jump-tab-btn:hover {{
    background: rgba(56, 189, 248, 0.18);
    border-color: var(--primary);
  }}

  /* Interactive Target Details */
  .target-details {{ margin-top: 0.6rem; }}
  .target-details summary {{
    cursor: pointer;
    color: var(--primary);
    font-size: 0.835rem;
    font-weight: 600;
    outline: none;
    user-select: none;
  }}
  .targets-list {{ margin-top: 0.6rem; display: flex; flex-direction: column; gap: 0.45rem; }}
  .target-item {{
    background: #090e17;
    padding: 0.55rem 0.8rem;
    border-radius: 6px;
    border-left: 3px solid var(--border);
    font-size: 0.825rem;
  }}
  .loc-link {{ color: var(--primary); text-decoration: none; font-weight: 600; }}
  .loc-link:hover {{ text-decoration: underline; }}
  .target-sym {{ color: #a78bfa; font-weight: 500; margin-left: 0.4rem; }}
  .target-msg {{ color: var(--text-muted); margin-left: 0.5rem; }}

  .snippet {{
    margin-top: 0.6rem;
    background: #030712;
    padding: 0.75rem 1rem;
    border-radius: 6px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.775rem;
    line-height: 1.45;
    color: #e2e8f0;
    border: 1px solid var(--border);
    white-space: pre;
  }}

  /* Clone Group Cards */
  .clone-group-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
  }}
  .clone-group-title {{
    font-size: 0.95rem;
    font-weight: 700;
    color: var(--warn);
    margin-bottom: 0.6rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }}
  .clone-occurrences {{ display: flex; flex-wrap: wrap; gap: 0.6rem; margin-bottom: 0.75rem; }}
  .occ-pill {{
    background: #172033;
    padding: 0.25rem 0.7rem;
    border-radius: 6px;
    font-size: 0.8125rem;
    border: 1px solid var(--border-highlight);
  }}

  /* Line Charts & Real Tree View */
  .line-grid {{
    display: grid;
    grid-template-columns: 1fr 1.6fr;
    gap: 1.5rem;
  }}
  @media (max-width: 900px) {{
    .line-grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; }}
  .chart-title {{ font-size: 1.05rem; font-weight: 700; margin-bottom: 1.25rem; color: #fff; }}

  .ratio-bar-wrapper {{ margin-bottom: 1.75rem; }}
  .ratio-bar {{
    display: flex;
    height: 20px;
    border-radius: 9999px;
    overflow: hidden;
    margin: 0.75rem 0;
    background: #1e293b;
  }}
  .ratio-legend {{ display: flex; gap: 1.5rem; font-size: 0.8125rem; }}
  .legend-item {{ display: flex; align-items: center; gap: 0.45rem; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}

  .top-file-row {{ margin-bottom: 0.95rem; }}
  .top-file-info {{ display: flex; justify-content: space-between; font-size: 0.825rem; margin-bottom: 0.3rem; }}
  .top-bar-bg {{ height: 8px; background: #1e293b; border-radius: 9999px; overflow: hidden; }}
  .top-bar-fill {{ height: 100%; border-radius: 9999px; background: #38bdf8; }}

  /* Real Tree Table */
  .tree-table {{ width: 100%; font-size: 0.825rem; }}
  .tree-table th {{ padding: 0.75rem 1rem; }}
  .tree-table td {{ padding: 0.55rem 1rem; border-bottom: 1px solid #172033; }}
  .tree-folder-row {{ background: #0c121e; font-weight: 700; color: #38bdf8; }}
  .tree-file-row:hover {{ background: #172033; }}
  .tree-indent {{ display: inline-block; }}
  .tree-icon {{ margin-right: 0.4rem; }}

  /* Test Suites */
  .test-suite-card {{
    background: #090e17;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin-bottom: 0.5rem;
  }}
  .test-suite-header {{ display: flex; justify-content: space-between; align-items: center; cursor: pointer; }}
  .test-suite-name {{ font-weight: 600; font-size: 0.875rem; }}
  .test-suite-cases {{ margin-top: 0.6rem; display: flex; flex-direction: column; gap: 0.35rem; }}

  /* Issues View */
  .issue-item {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.1rem 1.35rem;
    margin-bottom: 0.85rem;
  }}
  .issue-header {{ display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem; font-size: 0.85rem; }}
  .issue-engine {{ font-weight: 700; color: #94a3b8; }}
  .issue-msg {{ font-size: 0.9rem; color: #e2e8f0; }}
  .empty-clean {{
    padding: 3.5rem;
    text-align: center;
    background: var(--card-bg);
    border: 1px dashed var(--border);
    border-radius: 12px;
    color: var(--pass);
    font-size: 1.15rem;
    font-weight: 600;
  }}

  /* Complexity Leaderboard */
  .cc-card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1.25rem;
    margin-bottom: 1.25rem;
  }}
  .cc-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem; }}
  .cc-name {{ font-size: 1rem; font-weight: 700; color: #fff; }}
  .cc-badge {{ padding: 0.25rem 0.65rem; border-radius: 6px; font-weight: 700; font-size: 0.8rem; }}

  .footer {{ margin-top: 3.5rem; text-align: center; color: var(--text-muted); font-size: 0.8125rem; }}
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div class="brand">
      <div class="brand-logo">ici</div>
      <div class="brand-info">
        <h1>{html.escape(project_name)}</h1>
        <p>Unified CI/CD Verification & Code Quality Gate</p>
      </div>
    </div>
    <div>
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
function switchTab(tabId, btnElem) {{
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));

  if (btnElem) {{
    btnElem.classList.add('active');
  }} else {{
    const btn = document.getElementById('btn-' + tabId.replace('tab-', ''));
    if (btn) btn.classList.add('active');
  }}

  const target = document.getElementById(tabId);
  if (target) target.classList.add('active');
  window.scrollTo({{ top: 0, behavior: 'smooth' }});
}}
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
            abs_t_path = (base / t.file_path).resolve()
            vscode_link = f"vscode://file/{abs_t_path}:{t.start_line}"

            targets_html.append(
                f"<div class='target-item'>"
                f"  <span class='badge' style='color:{t_badge_color}'>{t.status.value}</span> "
                f"  <a href='{vscode_link}' class='loc-link'><code>{t_loc}</code></a> "
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
    """Renders line ratio bar, top 5 files chart, and a REAL hierarchical tree explorer."""
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
        abs_f = (base / tf["path"]).resolve()
        v_link = f"vscode://file/{abs_f}:1"
        top_bars_html.append(
            f"<div class='top-file-row'>"
            f"  <div class='top-file-info'>"
            f"    <a href='{v_link}' class='loc-link'><code>{html.escape(tf['path'])}</code></a>"
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
    <div class="line-grid">
      <!-- Left: Charts -->
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

        <div class="chart-title" style="margin-top: 2.25rem;">🏆 Top 5 Largest Files</div>
        {"".join(top_bars_html)}
      </div>

      <!-- Right: Real Explorer Tree Table -->
      <div class="chart-card">
        <div class="chart-title">📁 File Explorer Tree ({len(all_files)} Files)</div>
        <div style="max-height: 520px; overflow-y: auto;">
          <table class="tree-table">
            <thead>
              <tr>
                <th>Directory & File Structure</th>
                <th>Lang</th>
                <th>Status</th>
                <th class="text-right">Code</th>
                <th class="text-right">Comment</th>
                <th class="text-right">Blank</th>
                <th class="text-right">Total</th>
              </tr>
            </thead>
            <tbody>
              {"".join(tree_rows)}
            </tbody>
          </table>
        </div>
      </div>
    </div>
    """


def _build_hierarchical_tree_rows(files_data: list[dict], base: Path) -> list[str]:
    """Constructs real indented tree rows for files grouped by directory."""
    # Organize into dict: folder -> list of files
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
        # Calculate folder totals
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
            abs_f = (base / f["path"]).resolve()
            v_link = f"vscode://file/{abs_f}:1"
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
                f"    <a href='{v_link}' class='loc-link'><code>{html.escape(fname)}</code></a>"
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
        abs_p = (base / t_file).resolve()
        v_link = f"vscode://file/{abs_p}:{t_start}"

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
            snippet_html = f"<pre class='snippet'><code>{html.escape(t_snippet)}</code></pre>"

        cards.append(
            f"<div class='cc-card'>"
            f"  <div class='cc-header'>"
            f"    <div>"
            f"      <span style='color:var(--text-muted); font-weight:700; margin-right:0.5rem;'>#{rank}</span>"
            f"      <span class='cc-name'>{html.escape(t_name)}</span>"
            f"      <span style='margin-left:0.6rem;'><a href='{v_link}' class='loc-link'><code>{html.escape(t_file)}:{t_start}</code></a></span>"
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
    <div style="margin-bottom: 1.25rem;">
      <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">🧩 Cyclomatic Complexity & Nesting Analysis</h2>
      <p style="font-size: 0.875rem; color: var(--text-muted);">
        함수의 분기점(If, While, Match 등)과 블록 중첩 깊이를 분석하여 리팩토링이 필요한 고복잡도 함수를 진단합니다.
      </p>
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
            abs_f = (base / occ["file_path"]).resolve()
            vscode_link = f"vscode://file/{abs_f}:{occ['start_line']}"
            occ_html.append(
                f"<span class='occ-pill'><a href='{vscode_link}' class='loc-link'><code>{html.escape(occ['loc'])}</code></a></span>"
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
        abs_t_path = (base / t.file_path).resolve()
        vscode_link = f"vscode://file/{abs_t_path}:{t.start_line}"

        snippet_block = ""
        if t.snippet:
            snippet_block = f"<pre class='snippet'><code>{html.escape(t.snippet)}</code></pre>"

        items.append(
            f"<div class='issue-item'>"
            f"  <div class='issue-header'>"
            f"    <span class='badge' style='color:{t_badge_color}; border:1px solid {t_badge_color}44'>{t.status.value}</span>"
            f"    <span class='issue-engine'>[{html.escape(eng_name)}]</span>"
            f"    <a href='{vscode_link}' class='loc-link' title='Open in VS Code'><code>{t_loc}</code></a>"
            f"    <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span>"
            f"  </div>"
            f"  <div class='issue-msg'>{html.escape(t.message)}</div>"
            f"  {snippet_block}"
            f"</div>"
        )

    return "".join(items)
