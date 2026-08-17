"""Zero-CDN Standalone HTML Reporter for ici."""

import html
from pathlib import Path

from ici.core.models import EngineStatus, VerificationSuiteResult, format_score_display


def generate_html_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_name: str = "Project",
    base_dir: Path | None = None,
) -> None:
    """Generates a zero-CDN, standalone, self-contained HTML report."""
    base = (base_dir or Path.cwd()).resolve()

    status_color = (
        "#10b981"
        if suite.suite_status == EngineStatus.PASS
        else ("#f59e0b" if suite.suite_status == EngineStatus.WARN else "#ef4444")
    )
    status_bg = (
        "#064e3b"
        if suite.suite_status == EngineStatus.PASS
        else ("#78350f" if suite.suite_status == EngineStatus.WARN else "#7f1d1d")
    )

    # Build rows
    rows_html = []
    for res in suite.results:
        badge_color = (
            "#10b981"
            if res.status == EngineStatus.PASS
            else ("#f59e0b" if res.status == EngineStatus.WARN else "#ef4444")
        )
        badge_bg = (
            "#064e3b"
            if res.status == EngineStatus.PASS
            else ("#78350f" if res.status == EngineStatus.WARN else "#7f1d1d")
        )
        score_str = format_score_display(res)
        duration_str = f"{res.duration:.2f}s" if res.duration > 0 else "-"

        # Targets breakdown
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

            snippet_block = ""
            if t.snippet:
                snippet_block = f"<pre class='snippet'><code>{html.escape(t.snippet)}</code></pre>"

            targets_html.append(
                f"<div class='target-item'>"
                f"  <span class='badge' style='color:{t_badge_color}'>{t.status.value}</span> "
                f"  <a href='{vscode_link}' class='loc-link' title='Open in VS Code'><code>{t_loc}</code></a> "
                f"  <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span> "
                f"  <span class='target-msg'>{html.escape(t.message)}</span>"
                f"  {snippet_block}"
                f"</div>"
            )

        targets_section = ""
        if targets_html:
            targets_section = (
                f"<details class='target-details'>"
                f"  <summary>{len(res.targets)} Inspected Locations</summary>"
                f"  <div class='targets-list'>{''.join(targets_html)}</div>"
                f"</details>"
            )

        rows_html.append(
            f"<tr class='engine-row'>"
            f"  <td><strong>{html.escape(res.engine_name)}</strong></td>"
            f"  <td><span class='badge' style='color:{badge_color}; background:{badge_bg}'>{res.status.value}</span></td>"
            f"  <td>{html.escape(res.summary)}{targets_section}</td>"
            f"  <td class='text-right'><code>{html.escape(score_str)}</code></td>"
            f"  <td class='text-right text-muted'>{duration_str}</td>"
            f"</tr>"
        )

    tem_score_card = ""
    if suite.tem_score is not None:
        tem_score_card = f"""
        <div class="card stat-card">
            <div class="stat-label">TEM Quality Score</div>
            <div class="stat-value" style="color:#06b6d4">{suite.tem_score:.2f} <span class="stat-sub">/ {suite.max_tem_score:.1f}</span></div>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ici Verification Report — {html.escape(project_name)}</title>
<style>
  :root {{
    --bg: #0f172a;
    --card-bg: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #38bdf8;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); padding: 2rem; line-height: 1.5; }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid var(--border); }}
  .header h1 {{ font-size: 1.75rem; font-weight: 700; color: var(--primary); }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }}
  .stat-label {{ font-size: 0.875rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }}
  .stat-value {{ font-size: 1.875rem; font-weight: 700; margin-top: 0.25rem; }}
  .stat-sub {{ font-size: 1rem; color: var(--text-muted); font-weight: 400; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }}
  th, td {{ padding: 1rem; text-align: left; border-bottom: 1px solid var(--border); }}
  th {{ background: #0b1120; color: var(--text-muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .text-right {{ text-align: right; }}
  .text-muted {{ color: var(--text-muted); }}
  .badge {{ display: inline-block; padding: 0.25rem 0.6rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 700; letter-spacing: 0.05em; }}
  .target-details {{ margin-top: 0.75rem; font-size: 0.875rem; }}
  .target-details summary {{ cursor: pointer; color: var(--primary); outline: none; }}
  .targets-list {{ margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.5rem; }}
  .target-item {{ background: #0b1120; padding: 0.5rem 0.75rem; border-radius: 4px; border-left: 3px solid var(--border); font-size: 0.8125rem; }}
  .loc-link {{ color: var(--primary); text-decoration: none; font-weight: 600; }}
  .loc-link:hover {{ text-decoration: underline; }}
  .target-sym {{ color: #a78bfa; font-weight: 500; margin-left: 0.5rem; }}
  .target-msg {{ color: var(--text-muted); margin-left: 0.5rem; }}
  .snippet {{ margin-top: 0.4rem; background: #020617; padding: 0.5rem; border-radius: 4px; overflow-x: auto; font-family: monospace; font-size: 0.75rem; color: #e2e8f0; }}
  .footer {{ margin-top: 2rem; text-align: center; color: var(--text-muted); font-size: 0.875rem; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>🚀 ici Verification Report</h1>
      <p class="text-muted">Target: <strong>{html.escape(project_name)}</strong></p>
    </div>
    <div>
      <span class="badge" style="color:{status_color}; background:{status_bg}; font-size:1.1rem; padding: 0.5rem 1.25rem;">
        {suite.suite_status.value}
      </span>
    </div>
  </div>

  <div class="stats-grid">
    <div class="card stat-card">
      <div class="stat-label">Engines Run</div>
      <div class="stat-value">{suite.total_count}</div>
    </div>
    <div class="card stat-card">
      <div class="stat-label">Passed / Failed</div>
      <div class="stat-value"><span style="color:#10b981">{suite.passed_count}</span> <span class="stat-sub">/</span> <span style="color:#ef4444">{suite.failed_count}</span></div>
    </div>
    {tem_score_card}
    <div class="card stat-card">
      <div class="stat-label">Duration</div>
      <div class="stat-value">{suite.duration:.2f} <span class="stat-sub">sec</span></div>
    </div>
  </div>

  <table>
    <thead>
      <tr>
        <th>Engine</th>
        <th>Status</th>
        <th>Summary & Inspected Targets</th>
        <th class="text-right">Score / Metrics</th>
        <th class="text-right">Time</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rows_html)}
    </tbody>
  </table>

  <div class="footer">
    Generated by <strong>ici (Integrated CI)</strong> Engine v0.1.0 — Offline Zero-CDN HTML Report
  </div>
</div>
</body>
</html>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
