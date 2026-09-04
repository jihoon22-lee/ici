"""Top-level HTML report assembly — page shell, tabs, and stat cards."""

import html
from pathlib import Path

from ici.core.models import VerificationSuiteResult
from ici.core.redaction import redact_suite
from ici.reporters.html.assets_loader import HTML_CSS, HTML_JS
from ici.reporters.html.large import (
    HTML_LARGE_REPORT_JS,
    LARGE_REPORT_FINDING_THRESHOLD,
    LARGE_REPORT_INITIAL_ROWS,
    canonical_finding_count,
    serialize_large_report_data,
)
from ici.reporters.html.sections.baseline import _render_baseline_section
from ici.reporters.html.sections.complexity import _render_complexity_section
from ici.reporters.html.sections.cycles import _render_cycles_section
from ici.reporters.html.sections.dup import _render_dup_section
from ici.reporters.html.sections.issues import _render_issues_section
from ici.reporters.html.sections.line import _render_line_section
from ici.reporters.html.sections.static_analysis import _render_static_analysis_section
from ici.reporters.html.sections.summary import _render_engine_table_rows, _render_tem_card
from ici.reporters.html.sections.support import _render_support_section
from ici.reporters.html.sections.test import _render_test_section
from ici.reporters.html.sections.type_check import _render_type_section
from ici.reporters.html.utils import _extract_suite_data, _get_status_theme


def generate_html_report(
    suite: VerificationSuiteResult,
    output_path: Path,
    project_name: str = "Project",
    base_dir: Path | None = None,
) -> None:
    """Generates a state-of-the-art, zero-CDN, standalone HTML report with universal editor links."""
    suite = redact_suite(suite)
    base = (base_dir or Path.cwd()).resolve()
    status_color, status_bg, status_border = _get_status_theme(suite.suite_status)

    (
        eng_map,
        all_issues,
        pass_engines,
        warn_engines,
        fail_engines,
        error_engines,
        skip_engines,
    ) = _extract_suite_data(suite.results, base)
    cache_hits = sum(result.cache_hit for result in suite.results)
    engine_rows = _render_engine_table_rows(suite.results, base)
    line_tab_content = _render_line_section(eng_map.get("line"), base)
    test_tab_content = _render_test_section(eng_map.get("test"), base)
    complexity_tab_content = _render_complexity_section(
        eng_map.get("complexity"), base, eng_map.get("cognitive")
    )
    type_tab_content = _render_type_section(eng_map.get("type"), base)
    dup_tab_content = _render_dup_section(eng_map.get("dup"), base)
    cycles_tab_content = _render_cycles_section(eng_map.get("cycle"), base)
    security_engines = [eng_map[name] for name in ("security", "resource") if name in eng_map]
    security_tab_content = _render_static_analysis_section(security_engines, base)
    large_report = canonical_finding_count(all_issues) > LARGE_REPORT_FINDING_THRESHOLD
    if large_report:
        issues_tab_content = _render_issues_section(
            all_issues,
            base,
            initial_limit=LARGE_REPORT_INITIAL_ROWS,
        )
        large_report_data = serialize_large_report_data(all_issues, base)
        report_scripts = f"""<script type="application/json" id="ici-report-data">{large_report_data}</script>
<script>
{HTML_JS}
{HTML_LARGE_REPORT_JS}
</script>"""
    else:
        issues_tab_content = _render_issues_section(all_issues, base)
        report_scripts = f"""<script>
{HTML_JS}
</script>"""
    support_tab_content = _render_support_section(
        suite.support_matrix,
        suite.capability_inventory,
    )
    baseline_tab_content = (
        _render_baseline_section(suite.baseline_comparison, suite, base)
        if suite.baseline_comparison is not None
        else ""
    )
    tem_score_card = _render_tem_card(suite.tem_score)

    support_tab_button = (
        '<button class="tab-btn" id="btn-support" data-tab-target="tab-support">'
        "🧭 Support &amp; Capabilities"
        "</button>"
        if support_tab_content
        else ""
    )
    support_tab_panel = (
        f'<div id="tab-support" class="tab-content">{support_tab_content}</div>'
        if support_tab_content
        else ""
    )
    baseline_tab_button = (
        '<button class="tab-btn" id="btn-baseline" data-tab-target="tab-baseline">'
        "🧭 Baseline Delta"
        "</button>"
        if baseline_tab_content
        else ""
    )
    baseline_tab_panel = (
        f'<div id="tab-baseline" class="tab-content">{baseline_tab_content}</div>'
        if baseline_tab_content
        else ""
    )

    dup_res = eng_map.get("dup")
    clone_groups_count = len(dup_res.extra.get("clone_groups", [])) if dup_res else 0

    cycle_res = eng_map.get("cycle")
    cycles_count = len(cycle_res.targets) if cycle_res else 0

    test_res = eng_map.get("test")
    t_passed = test_res.extra.get("passed_tests", 0) if test_res else 0
    t_total = test_res.extra.get("total_tests", 0) if test_res else 0

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
        <select id="editorSelect" class="editor-select">
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
        <span style="color:var(--fail)">{fail_engines} Fail</span> &bull;
        <span style="color:var(--fail)">{error_engines} Error</span> &bull;
        <span style="color:var(--text-muted)">{skip_engines} Skip</span> &bull;
        <span style="color:var(--text-muted)">{cache_hits} Cache Hits</span>
      </div>
      <div style="margin-top: 0.8rem; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; display: flex;">
        <div style="flex: {pass_engines}; background: var(--pass);" title="Pass {pass_engines}"></div>
        <div style="flex: {warn_engines};" title="Warn {warn_engines}"><div style="height: 100%; background: var(--warn);"></div></div>
        <div style="flex: {fail_engines}; background: var(--fail);" title="Fail {fail_engines}"></div>
        <div style="flex: {error_engines}; background: #b91c1c;" title="Error {error_engines}"></div>
        <div style="flex: {skip_engines}; background: var(--text-muted);" title="Skip {skip_engines}"></div>
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
    <button class="tab-btn active" id="btn-summary" data-tab-target="tab-summary">📋 Verification Suites</button>
    {support_tab_button}{baseline_tab_button}
    <button class="tab-btn" id="btn-line" data-tab-target="tab-line">📊 Line Analysis & Explorer</button>
    <button class="tab-btn" id="btn-test" data-tab-target="tab-test">🧪 Tests & Coverage ({t_passed}/{t_total})</button>
    <button class="tab-btn" id="btn-complexity" data-tab-target="tab-complexity">🧩 Complexity</button>
    <button class="tab-btn" id="btn-type" data-tab-target="tab-type">🏷️ Static Types</button>
    <button class="tab-btn" id="btn-dup" data-tab-target="tab-dup">📦 Clone Groups ({clone_groups_count})</button>
    <button class="tab-btn" id="btn-cycles" data-tab-target="tab-cycles">🔁 Cycles ({cycles_count})</button>
    <button class="tab-btn" id="btn-security" data-tab-target="tab-security">🔐 Security & Resources</button>
    <button class="tab-btn" id="btn-issues" data-tab-target="tab-issues">⚠️ Issues ({len(all_issues)})</button>
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
          <th class="text-right">Cache</th>
          <th class="text-right">Time</th>
        </tr>
      </thead>
      <tbody>
        {"".join(engine_rows)}
      </tbody>
    </table>
  </div>

  {support_tab_panel}{baseline_tab_panel}

  <!-- Tab 2: Line Analysis & Real Tree Explorer -->
  <div id="tab-line" class="tab-content">
    {line_tab_content}
  </div>

  <!-- Tab 3: Dedicated Tests & Coverage Explorer -->
  <div id="tab-test" class="tab-content">
    {test_tab_content}
  </div>

  <!-- Tab 4: Dedicated Complexity Analysis -->
  <div id="tab-complexity" class="tab-content">
    {complexity_tab_content}
  </div>

  <!-- Tab 5: Static Type Checking -->
  <div id="tab-type" class="tab-content">
    {type_tab_content}
  </div>

  <!-- Tab 6: Merged Non-Overlapping Clone Groups -->
  <div id="tab-dup" class="tab-content">
    {dup_tab_content}
  </div>

  <!-- Tab 6: Dependency Cycles -->
  <div id="tab-cycles" class="tab-content">
    {cycles_tab_content}
  </div>

  <!-- Tab 7: Security & Resources -->
  <div id="tab-security" class="tab-content">
    {security_tab_content}
  </div>

  <!-- Tab 8: Actionable Issues Only -->
  <div id="tab-issues" class="tab-content">
    {issues_tab_content}
  </div>

  <div class="footer">
    Generated by <strong>ici</strong> &bull; Zero-CDN Standalone Security & Quality Inspector
  </div>
</div>

{report_scripts}
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_content, encoding="utf-8")
