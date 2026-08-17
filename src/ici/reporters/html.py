"""Zero-CDN Standalone Interactive HTML Reporter for ici."""

import html
from pathlib import Path

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

    # Calculate aggregate metrics
    pass_engines = sum(1 for r in suite.results if r.status == EngineStatus.PASS)
    warn_engines = sum(1 for r in suite.results if r.status == EngineStatus.WARN)
    fail_engines = sum(1 for r in suite.results if r.status == EngineStatus.FAIL)

    all_issues = []
    for r in suite.results:
        for t in r.targets:
            if t.status in (EngineStatus.WARN, EngineStatus.FAIL):
                all_issues.append((r.engine_name, t))

    # Build Engines Table Rows
    engine_rows = []
    line_result = None

    for res in suite.results:
        if res.engine_name == "line":
            line_result = res

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

        # Custom rendering per engine
        details_html = _render_engine_details(res, base)

        engine_rows.append(
            f"<tr class='engine-row'>"
            f"  <td class='engine-name'><strong>{html.escape(res.engine_name)}</strong></td>"
            f"  <td><span class='badge' style='color:{badge_color}; background:{badge_bg}; border: 1px solid {badge_color}33'>{res.status.value}</span></td>"
            f"  <td>"
            f"    <div class='engine-summary-text'>{html.escape(res.summary)}</div>"
            f"    {details_html}"
            f"  </td>"
            f"  <td class='text-right'><code>{html.escape(score_str)}</code></td>"
            f"  <td class='text-right text-muted'>{duration_str}</td>"
            f"</tr>"
        )

    # Line Chart & Explorer Section
    line_section_html = _render_line_section(line_result, base)

    # TEM Card
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

    # Issues Tab Rows
    issues_html = []
    for eng_name, t in all_issues:
        t_badge_color = "#ef4444" if t.status == EngineStatus.FAIL else "#f59e0b"
        t_loc = f"{html.escape(t.file_path)}:{t.start_line}"
        abs_t_path = (base / t.file_path).resolve()
        vscode_link = f"vscode://file/{abs_t_path}:{t.start_line}"

        snippet_block = ""
        if t.snippet:
            snippet_block = f"<pre class='snippet'><code>{html.escape(t.snippet)}</code></pre>"

        issues_html.append(
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

    issues_tab_content = (
        "".join(issues_html)
        if issues_html
        else "<div class='empty-clean'>✨ No issues found! All active checks passed cleanly.</div>"
    )

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

  /* Interactive Target Details */
  .target-details {{ margin-top: 0.75rem; }}
  .target-details summary {{
    cursor: pointer;
    color: var(--primary);
    font-size: 0.85rem;
    font-weight: 600;
    outline: none;
    user-select: none;
  }}
  .targets-list {{ margin-top: 0.75rem; display: flex; flex-direction: column; gap: 0.5rem; }}
  .target-item {{
    background: #090e17;
    padding: 0.6rem 0.85rem;
    border-radius: 6px;
    border-left: 3px solid var(--border);
    font-size: 0.825rem;
  }}
  .loc-link {{ color: var(--primary); text-decoration: none; font-weight: 600; }}
  .loc-link:hover {{ text-decoration: underline; }}
  .target-sym {{ color: #a78bfa; font-weight: 500; margin-left: 0.4rem; }}
  .target-msg {{ color: var(--text-muted); margin-left: 0.5rem; }}
  .snippet {{
    margin-top: 0.5rem;
    background: #030712;
    padding: 0.6rem;
    border-radius: 6px;
    overflow-x: auto;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    font-size: 0.75rem;
    color: #e2e8f0;
    border: 1px solid var(--border);
  }}

  /* Clone Group Cards */
  .clone-group-card {{
    background: #090e17;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    margin-top: 0.75rem;
  }}
  .clone-group-title {{
    font-size: 0.875rem;
    font-weight: 700;
    color: var(--warn);
    margin-bottom: 0.5rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }}
  .clone-occurrences {{ display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.6rem; }}
  .occ-pill {{
    background: #172033;
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    font-size: 0.775rem;
    border: 1px solid var(--border-highlight);
  }}

  /* Line Charts & Explorer */
  .line-grid {{
    display: grid;
    grid-template-columns: 1fr 1.5fr;
    gap: 1.5rem;
    margin-top: 1rem;
  }}
  @media (max-width: 900px) {{
    .line-grid {{ grid-template-columns: 1fr; }}
  }}
  .chart-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; }}
  .chart-title {{ font-size: 1rem; font-weight: 700; margin-bottom: 1rem; color: #fff; }}

  .ratio-bar-wrapper {{ margin-bottom: 1.5rem; }}
  .ratio-bar {{
    display: flex;
    height: 18px;
    border-radius: 9999px;
    overflow: hidden;
    margin: 0.6rem 0;
    background: #1e293b;
  }}
  .ratio-legend {{ display: flex; gap: 1.25rem; font-size: 0.8125rem; }}
  .legend-item {{ display: flex; align-items: center; gap: 0.4rem; }}
  .legend-dot {{ width: 10px; height: 10px; border-radius: 50%; }}

  .top-file-row {{ margin-bottom: 0.85rem; }}
  .top-file-info {{ display: flex; justify-content: space-between; font-size: 0.8125rem; margin-bottom: 0.25rem; }}
  .top-bar-bg {{ height: 8px; background: #1e293b; border-radius: 9999px; overflow: hidden; }}
  .top-bar-fill {{ height: 100%; border-radius: 9999px; background: #38bdf8; }}

  /* File Tree Table */
  .file-tree-table {{ width: 100%; font-size: 0.85rem; }}
  .file-tree-table th {{ padding: 0.75rem 1rem; }}
  .file-tree-table td {{ padding: 0.6rem 1rem; }}

  /* Issues View */
  .issue-item {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-bottom: 0.75rem;
  }}
  .issue-header {{ display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.4rem; font-size: 0.85rem; }}
  .issue-engine {{ font-weight: 700; color: #94a3b8; }}
  .issue-msg {{ font-size: 0.9rem; color: #e2e8f0; }}
  .empty-clean {{
    padding: 3rem;
    text-align: center;
    background: var(--card-bg);
    border: 1px dashed var(--border);
    border-radius: 12px;
    color: var(--pass);
    font-size: 1.1rem;
    font-weight: 600;
  }}

  .btn-toggle {{
    background: #1e293b;
    border: 1px solid var(--border-highlight);
    color: var(--primary);
    padding: 0.35rem 0.8rem;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    margin-top: 0.5rem;
    display: inline-block;
  }}
  .btn-toggle:hover {{ background: #283548; }}

  .footer {{ margin-top: 3rem; text-align: center; color: var(--text-muted); font-size: 0.8125rem; }}
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
    <button class="tab-btn active" onclick="switchTab('tab-engines')">📋 Verification Suites</button>
    <button class="tab-btn" onclick="switchTab('tab-explorer')">📊 Code Explorer & Charts</button>
    <button class="tab-btn" onclick="switchTab('tab-issues')">⚠️ Issues & Warnings ({len(all_issues)})</button>
  </div>

  <!-- Tab 1: Engines -->
  <div id="tab-engines" class="tab-content active">
    <table>
      <thead>
        <tr>
          <th>Verification Engine</th>
          <th>Status</th>
          <th>Summary & Inspection Targets</th>
          <th class="text-right">Score / Metrics</th>
          <th class="text-right">Time</th>
        </tr>
      </thead>
      <tbody>
        {"".join(engine_rows)}
      </tbody>
    </table>
  </div>

  <!-- Tab 2: Explorer & Line Charts -->
  <div id="tab-explorer" class="tab-content">
    {line_section_html}
  </div>

  <!-- Tab 3: Issues Only -->
  <div id="tab-issues" class="tab-content">
    {issues_tab_content}
  </div>

  <div class="footer">
    Generated by <strong>ici</strong> &bull; Zero-CDN Standalone Security & Quality Inspector
  </div>
</div>

<script>
function switchTab(tabId) {{
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));

  event.target.classList.add('active');
  const target = document.getElementById(tabId);
  if (target) target.classList.add('active');
}}

function toggleElement(id, btnId, showText, hideText) {{
  const el = document.getElementById(id);
  const btn = document.getElementById(btnId);
  if (!el) return;
  if (el.style.display === 'none' || el.style.display === '') {{
    el.style.display = 'block';
    if (btn) btn.innerText = hideText;
  }} else {{
    el.style.display = 'none';
    if (btn) btn.innerText = showText;
  }}
}}
</script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")


def _render_engine_details(res: EngineResult, base: Path) -> str:
    """Renders engine specific details, filtering out noise and highlighting key groups."""
    # 1. Duplicate Engine (Clone Groups)
    if res.engine_name == "dup" and "clone_groups" in res.extra:
        groups = res.extra["clone_groups"]
        if not groups:
            return ""
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
                f"  <div class='clone-group-title'>📦 Clone Group #{g['id']} ({g['lines_count']} lines &bull; {g['occurrences_count']} occurrences)</div>"
                f"  <div class='clone-occurrences'>{''.join(occ_html)}</div>"
                f"  {snippet_html}"
                f"</div>"
            )
        return (
            f"<details class='target-details' open>"
            f"  <summary>{len(groups)} Duplicate Clone Groups</summary>"
            f"  {''.join(cards)}"
            f"</details>"
        )

    # 2. Complexity Engine (Issues only by default + Toggle)
    if res.engine_name == "complexity":
        issues = [t for t in res.targets if t.status in (EngineStatus.WARN, EngineStatus.FAIL)]
        passed = [t for t in res.targets if t.status == EngineStatus.PASS]

        issue_items = []
        for t in issues:
            t_badge_color = "#ef4444" if t.status == EngineStatus.FAIL else "#f59e0b"
            t_loc = f"{html.escape(t.file_path)}:{t.start_line}"
            abs_t_path = (base / t.file_path).resolve()
            vscode_link = f"vscode://file/{abs_t_path}:{t.start_line}"
            issue_items.append(
                f"<div class='target-item'>"
                f"  <span class='badge' style='color:{t_badge_color}'>{t.status.value}</span> "
                f"  <a href='{vscode_link}' class='loc-link'><code>{t_loc}</code></a> "
                f"  <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span> "
                f"  <span class='target-msg'>{html.escape(t.message)}</span>"
                f"</div>"
            )

        passed_items = []
        for t in passed:
            t_loc = f"{html.escape(t.file_path)}:{t.start_line}"
            abs_t_path = (base / t.file_path).resolve()
            vscode_link = f"vscode://file/{abs_t_path}:{t.start_line}"
            passed_items.append(
                f"<div class='target-item'>"
                f"  <span class='badge' style='color:#10b981'>PASS</span> "
                f"  <a href='{vscode_link}' class='loc-link'><code>{t_loc}</code></a> "
                f"  <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span> "
                f"  <span class='target-msg'>{html.escape(t.message)}</span>"
                f"</div>"
            )

        issues_block = (
            f"<div class='targets-list'>{''.join(issue_items)}</div>" if issue_items else ""
        )
        passed_block = (
            f"<div id='cc-all-funcs' style='display:none; margin-top:0.5rem;'>"
            f"  <div class='targets-list'>{''.join(passed_items)}</div>"
            f"</div>"
            f"<button id='cc-toggle-btn' class='btn-toggle' onclick=\"toggleElement('cc-all-funcs', 'cc-toggle-btn', '👁️ Show all {len(res.targets)} functions', '🙈 Hide clean functions')\">👁️ Show all {len(res.targets)} functions</button>"
        )

        return (
            f"<details class='target-details' {'open' if issues else ''}>"
            f"  <summary>{len(issues)} Complexity Issues (Total {len(res.targets)} functions inspected)</summary>"
            f"  {issues_block}"
            f"  {passed_block}"
            f"</details>"
        )

    # 3. Sanitize Engine (Clean or Defects)
    if res.engine_name == "sanitize" and not res.targets:
        return "<div style='font-size:0.8rem; color:#10b981; margin-top:0.3rem;'>✅ 0 Defect — Memory Safety & Resource Management Clean</div>"

    # Default Target List
    if not res.targets:
        return ""

    targets_html = []
    for t in res.targets:
        t_badge_color = (
            "#10b981"
            if t.status == EngineStatus.PASS
            else ("#f59e0b" if t.status == EngineStatus.WARN else "#ef4444")
        )
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

    return (
        f"<details class='target-details'>"
        f"  <summary>{len(res.targets)} Inspected Locations</summary>"
        f"  <div class='targets-list'>{''.join(targets_html)}</div>"
        f"</details>"
    )


def _render_line_section(line_res: EngineResult | None, base: Path) -> str:
    """Renders line ratio bar, top 5 files chart, and directory explorer tree."""
    if not line_res or "files_data" not in line_res.extra:
        return "<div class='card'>No line statistics available</div>"

    code = line_res.extra.get("code", 0)
    comment = line_res.extra.get("comment", 0)
    blank = line_res.extra.get("blank", 0)
    total = line_res.extra.get("total", 1) or 1

    code_pct = (code / total) * 100.0
    comment_pct = (comment / total) * 100.0
    blank_pct = (blank / total) * 100.0

    # Top 5 files
    top_files = line_res.extra.get("top_files", [])
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

    # Explorer Tree Table
    all_files = line_res.extra.get("files_data", [])
    tree_rows = []
    for f in all_files:
        abs_f = (base / f["path"]).resolve()
        v_link = f"vscode://file/{abs_f}:1"
        st_badge = (
            "<span class='badge' style='color:#10b981'>PASS</span>"
            if f["status"] == "PASS"
            else f"<span class='badge' style='color:#f59e0b'>{f['status']}</span>"
        )
        tree_rows.append(
            f"<tr>"
            f"  <td><a href='{v_link}' class='loc-link'><code>{html.escape(f['path'])}</code></a></td>"
            f"  <td><span class='badge' style='background:#1f293d; color:#a78bfa'>{html.escape(f['lang'])}</span></td>"
            f"  <td>{st_badge}</td>"
            f"  <td class='text-right'><strong>{f['code']:,}</strong></td>"
            f"  <td class='text-right text-muted'>{f['comment']:,}</td>"
            f"  <td class='text-right text-muted'>{f['blank']:,}</td>"
            f"  <td class='text-right'><code>{f['total']:,}</code></td>"
            f"</tr>"
        )

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

        <div class="chart-title" style="margin-top: 2rem;">🏆 Top 5 Largest Files</div>
        {"".join(top_bars_html)}
      </div>

      <!-- Right: Explorer Tree Table -->
      <div class="chart-card">
        <div class="chart-title">📁 File Explorer & Size Breakdown ({len(all_files)} Files)</div>
        <div style="max-height: 480px; overflow-y: auto;">
          <table class="file-tree-table">
            <thead>
              <tr>
                <th>File Path</th>
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
