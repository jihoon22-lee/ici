"""Tests & Coverage tab — coverage KPIs, per-module table, test suite detail."""

import html
from pathlib import Path

from ici.core.models import EngineResult
from ici.reporters.html.utils import _cov_color, _location_controls


def _render_coverage_table(
    coverage_files: list[dict], totals: dict | None, source: str, base: Path
) -> str:
    """Renders a coverage.py/gcov-style per-module table (Stmts/Miss/Cover/Branch)."""
    source_map = {
        "coverage.py": "coverage.py 실측",
        "gcov": "gcov 실측",
        "coverage.py/gcov": "coverage.py + gcov 실측",
    }
    source_label = source_map.get(source, "추정 (coverage.py/gcov 미설치)")
    if not coverage_files:
        return """
    <div class="card" style="margin-bottom: 1.5rem;">
      <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">📈 Module Coverage Table</h2>
      <p style="font-size: 0.875rem; color: var(--text-muted);">
        모듈별 실측 커버리지를 수집하지 못했습니다 — Python은 <code>pip install coverage</code>,
        C++은 <code>g++</code>/<code>gcov</code> 설치 환경에서 다시 실행하면 모듈별 Stmts/Miss/Cover
        테이블이 채워집니다 (현재 KPI 수치는 추정치입니다).
      </p>
    </div>
    """

    rows_html = []
    for f in coverage_files:
        raw_fname = str(f.get("file", "?"))
        stmts = f.get("stmts", 0)
        miss = f.get("miss", 0)
        cover = f.get("cover")
        branch_cover = f.get("branch_cover")
        missing = f.get("missing_lines") or []
        miss_tip = html.escape(", ".join(str(x) for x in missing)) if missing else ""
        location = _location_controls(raw_fname, 1, base, label=raw_fname)
        cov_color = _cov_color(cover)
        br_color = _cov_color(branch_cover)
        cover_str = f"{cover:.1f}%" if isinstance(cover, (int, float)) else "—"
        branch_str = f"{branch_cover:.1f}%" if isinstance(branch_cover, (int, float)) else "—"
        miss_style = "color: var(--fail); font-weight: 700;" if miss > 0 else ""
        rows_html.append(
            {
                "folder": str(Path(raw_fname).parent),
                "html": (
                    f"<tr>"
                    f"<td style='max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' "
                    f"title='{miss_tip}'>"
                    f"{location}"
                    f"</td>"
                    f"<td class='num'>{stmts}</td>"
                    f"<td class='num' style='{miss_style}'>{miss}</td>"
                    f"<td><div class='cov-pct-cell'>"
                    f"<span class='cov-pct' style='color:{cov_color}'>{cover_str}</span>"
                    f"<div class='cov-bar-bg'><div class='cov-bar-fill' "
                    f"style='width: {min(100.0, cover or 0.0)}%; background: {cov_color};'></div></div>"
                    f"</div></td>"
                    f"<td><div class='cov-pct-cell'>"
                    f"<span class='cov-pct' style='color:{br_color}'>{branch_str}</span>"
                    f"<div class='cov-bar-bg'><div class='cov-bar-fill' "
                    f"style='width: {min(100.0, branch_cover or 0.0)}%; background: {br_color};'></div></div>"
                    f"</div></td>"
                    f"</tr>"
                ),
                "cover": cover if isinstance(cover, (int, float)) else 100.0,
                "miss": miss,
            }
        )

    # Group rows by directory into collapsible blocks — open only problem folders.
    folder_order: list[str] = []
    folder_groups: dict[str, list[dict]] = {}
    for row in rows_html:
        if row["folder"] not in folder_groups:
            folder_groups[row["folder"]] = []
            folder_order.append(row["folder"])
        folder_groups[row["folder"]].append(row)

    folder_blocks = []
    for folder in sorted(folder_order):
        group = folder_groups[folder]
        needs_attention = any(r["miss"] > 0 or r["cover"] < 80.0 for r in group)
        avg_cover = sum(r["cover"] for r in group) / len(group)
        avg_color = _cov_color(avg_cover)
        open_attr = " open" if needs_attention else ""
        folder_blocks.append(
            f"<details class='cov-folder-group'{open_attr}>"
            f"  <summary>📁 <strong>{html.escape(folder)}</strong>"
            f"   <span style='color:var(--text-muted); font-size:0.8rem;'>({len(group)} modules)</span>"
            f"   <span class='badge' style='color:{avg_color}; border:1px solid {avg_color}44'>avg {avg_cover:.1f}%</span>"
            f"  </summary>"
            f"  <table class='cov-table'>"
            f"    <thead><tr><th>Module / File</th><th class='num'>Stmts</th><th class='num'>Miss</th><th>Cover</th><th>Branch</th></tr></thead>"
            f"    <tbody>{''.join(r['html'] for r in group)}</tbody>"
            f"  </table>"
            f"</details>"
        )

    totals_html = ""
    if totals:
        t_stmts = totals.get("stmts", 0)
        t_miss = totals.get("miss", 0)
        t_cover = totals.get("cover")
        t_branch = totals.get("branch_cover")
        t_cover_str = f"{t_cover:.1f}%" if isinstance(t_cover, (int, float)) else "—"
        t_branch_str = f"{t_branch:.1f}%" if isinstance(t_branch, (int, float)) else "—"
        totals_html = (
            f"<div style='display:flex; gap:1.25rem; align-items:center; padding:0.6rem 1rem;"
            f" background:var(--card-hover); border-top:1px solid var(--border); font-size:0.85rem;'>"
            f"<strong>Totals ({len(coverage_files)} modules)</strong>"
            f"<span>Stmts <code>{t_stmts}</code></span>"
            f"<span>Miss <code style='color:{'#ef4444' if t_miss else '#10b981'}'>{t_miss}</code></span>"
            f"<span>Cover <strong style='color:{_cov_color(t_cover)}'>{t_cover_str}</strong></span>"
            f"<span>Branch <strong style='color:{_cov_color(t_branch)}'>{t_branch_str}</strong></span>"
            f"</div>"
        )

    return f"""
    <!-- Module Coverage Table (grouped by directory) -->
    <div style="margin-bottom: 1rem;">
      <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">📈 Module Coverage Table ({len(coverage_files)} Modules)</h2>
      <p style="font-size: 0.875rem; color: var(--text-muted);">
        디렉터리별로 묶은 모듈 커버리지 — 문제 폴더(80% 미만·미스 존재)만 기본 펼쳐집니다.
        데이터 출처: <strong style="color: {"#10b981" if source != "estimated" else "#f59e0b"}">{source_label}</strong>
        · 파일명에 마우스를 올리면 미실행 라인 목록이 표시됩니다.
      </p>
    </div>
    <div class="card" style="padding: 0; overflow: hidden; margin-bottom: 1.5rem;">
      {"".join(folder_blocks)}
      {totals_html}
    </div>
    """


def _render_function_table(function_rows: list[dict], source: str, base: Path) -> str:
    """Renders a per-function coverage table (gcov-style: called at least once)."""
    if not function_rows:
        return ""

    covered = sum(1 for r in function_rows if r["covered"])
    total = len(function_rows)
    pct = covered / total * 100.0 if total else 0.0
    pct_color = _cov_color(pct)

    rows_html = []
    for r in function_rows:
        raw_fname = str(r.get("file", "?"))
        name = html.escape(r.get("name", "?"))
        start = r.get("start_line", 1)
        end = r.get("end_line", start)
        covered_flag = bool(r.get("covered"))
        missing = r.get("missing_lines") or []
        miss_tip = html.escape(", ".join(str(x) for x in missing)) if missing else ""
        location = _location_controls(raw_fname, start, base, label=f"{raw_fname}:{start}")
        badge = (
            "<span class='badge' style='color:#10b981; border:1px solid #10b98144'>✓ 실행됨</span>"
            if covered_flag
            else "<span class='badge' style='color:#ef4444; border:1px solid #ef444444'>✗ 미실행</span>"
        )
        rows_html.append(
            f"<tr>"
            f"<td>{badge} <code>{name}()</code></td>"
            f"<td style='max-width: 380px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;' title='{miss_tip}'>"
            f"{location}</td>"
            f"<td class='num'>{start}-{end}</td>"
            f"<td class='num'>{len(missing)}</td>"
            f"</tr>"
        )

    return f"""
    <!-- Function Coverage Table (collapsed by default) -->
    <details class='cov-folder-group'>
      <summary>📈 <strong>Function Coverage Table</strong>
        <span style='color:var(--text-muted); font-size:0.8rem;'>({covered}/{total} 호출됨)</span>
        <span class='badge' style='color:{pct_color}; border:1px solid {pct_color}44'>{pct:.1f}%</span>
      </summary>
      <div style="padding: 0.75rem 1rem;">
        <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 0.5rem;">
          gcov 기준 — 함수 본문이 한 번 이상 실행되면 커버된 것으로 간주합니다. 미실행 함수의 missing 라인은 파일명에 마우스를 올리면 표시됩니다.
        </p>
        <div style="overflow-x: auto;">
          <table class="cov-table">
            <thead><tr><th>Function</th><th>Location</th><th class="num">Lines</th><th class="num">Miss</th></tr></thead>
            <tbody>{"".join(rows_html)}</tbody>
          </table>
        </div>
      </div>
    </details>
    """


def _render_test_section(test_res: EngineResult | None, base: Path) -> str:
    """Renders dedicated Test & Coverage analysis tab with suite cards and metric progress bars."""
    if not test_res:
        return "<div class='card'>No test execution data available.</div>"

    passed = test_res.extra.get("passed_tests", 0)
    total = test_res.extra.get("total_tests", 0)
    branch = test_res.extra.get("branch_coverage", 0.0)
    func = test_res.extra.get("function_coverage", 0.0)
    tem = test_res.extra.get("tem_score", 0.0)
    line_cov = test_res.extra.get("line_coverage")
    pass_rate = test_res.extra.get("pass_rate")
    suites = test_res.extra.get("test_suites", [])
    coverage_files = test_res.extra.get("coverage_files") or []
    coverage_source = test_res.extra.get("coverage_source", "estimated")
    coverage_totals = test_res.extra.get("coverage_totals")
    function_rows = test_res.extra.get("function_rows") or []

    kpi_cov_label = "Line Coverage" if line_cov is not None else "Branch Coverage"
    kpi_cov_value = line_cov if line_cov is not None else branch
    kpi_cov_est = " (추정)" if line_cov is None else ""

    func_pct = min(100.0, func)
    tem_pct = min(100.0, (tem / 5.0) * 100.0)

    # Build Test Suite Cards — collapsed by default, failures stay visible.
    suite_cards = []
    for s in suites:
        s_file = s.get("file", "tests")
        s_passed = s.get("passed", 0)
        s_failed = s.get("failed", 0)
        s_skipped = s.get("skipped", 0)
        s_total = s.get("total", 0)
        tests_list = s.get("tests", [])

        st_badge_color = "#ef4444" if s_failed else ("#f59e0b" if s_skipped else "#10b981")
        st_badge_text = f"{s_passed}/{s_total} Passed"
        if s_skipped:
            st_badge_text += f" · {s_skipped} Skipped"
        location = _location_controls(str(s_file), 1, base, label=str(s_file))

        failed_rows: list[str] = []
        skipped_rows: list[str] = []
        passed_rows: list[str] = []
        for t in tests_list:
            t_name = html.escape(t.get("name", "test"))
            t_status = t.get("status", "PASS")
            t_status_html = html.escape(str(t_status))
            t_msg = html.escape(t.get("message", ""))
            t_color = (
                "#10b981"
                if t_status == "PASS"
                else ("#f59e0b" if t_status == "SKIP" else "#ef4444")
            )
            row = (
                f"<div class='test-case-row'>"
                f"  <span class='badge' style='color:{t_color}; border:1px solid {t_color}33'>{t_status_html}</span>"
                f"  <span class='test-case-name'><code>{t_name}</code></span>"
                f"  <span class='test-case-msg'>{t_msg}</span>"
                f"</div>"
            )
            if t_status == "PASS":
                passed_rows.append(row)
            elif t_status == "SKIP":
                skipped_rows.append(row)
            else:
                failed_rows.append(row)

        failed_html = "".join(failed_rows)
        skipped_html = "".join(skipped_rows)
        if s_failed == 0 and s_skipped == 0:
            cases_body = (
                f"<div class='test-case-row' style='color:#10b981;'>"
                f"  <span class='badge' style='color:#10b981; border:1px solid #10b98133'>PASS</span>"
                f"  <span>✅ All {s_total} cases passed</span>"
                f"</div>"
                + (
                    f"<details class='test-case-details'>"
                    f"  <summary>Show all {len(passed_rows)} cases ▾</summary>"
                    f"  {''.join(passed_rows)}"
                    f"</details>"
                    if passed_rows
                    else ""
                )
            )
        else:
            cases_body = (
                failed_html
                + skipped_html
                + (
                    f"<details class='test-case-details'>"
                    f"  <summary>Show {len(passed_rows)} passed cases ▾</summary>"
                    f"  {''.join(passed_rows)}"
                    f"</details>"
                    if passed_rows
                    else ""
                )
            )

        suite_cards.append(
            f"<div class='test-suite-card'>"
            f"  <div class='test-suite-header'>"
            f"    <div class='loc-link-group'>"
            f"      <span style='font-size:1.1rem;'>🧪</span>"
            f"      {location}"
            f"    </div>"
            f"    <span class='badge' style='color:{st_badge_color}; border:1px solid {st_badge_color}44'>{st_badge_text}</span>"
            f"  </div>"
            f"  <div class='test-cases-list'>{cases_body}</div>"
            f"</div>"
        )

    return f"""
    <!-- Top Row: 4 Metric KPI Cards -->
    <div class="stats-grid" style="margin-bottom: 1.5rem;">
      <div class="card stat-card">
        <div class="stat-label">TEM Quality Score</div>
        <div class="stat-value" style="color:#38bdf8">{tem:.2f} <span class="stat-sub">/ 5.0</span></div>
        <div class="mini-progress-bg">
          <div class="mini-progress-fill" style="width: {tem_pct}%; background: #38bdf8;"></div>
        </div>
      </div>

      <div class="card stat-card">
        <div class="stat-label">{kpi_cov_label}</div>
        <div class="stat-value" style="color:{"#10b981" if kpi_cov_value >= 80 else "#f59e0b"}">{kpi_cov_value:.1f}% <span class="stat-sub">(Min 80%{kpi_cov_est})</span></div>
        <div class="mini-progress-bg">
          <div class="mini-progress-fill" style="width: {min(100.0, kpi_cov_value)}%; background: {"#10b981" if kpi_cov_value >= 80 else "#f59e0b"};"></div>
        </div>
      </div>

      <div class="card stat-card">
        <div class="stat-label">Function Coverage</div>
        <div class="stat-value" style="color:{"#10b981" if func >= 90 else "#f59e0b"}">{func:.1f}% <span class="stat-sub">(Min 90%)</span></div>
        <div class="mini-progress-bg">
          <div class="mini-progress-fill" style="width: {func_pct}%; background: {"#10b981" if func >= 90 else "#f59e0b"};"></div>
        </div>
      </div>

      <div class="card stat-card">
        <div class="stat-label">Unit Test Pass Rate</div>
        <div class="stat-value" style="color:#10b981">{passed} / {total} <span class="stat-sub">Passed</span></div>
        <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.4rem;">
          Duration: {test_res.duration:.2f}s across {len(suites)} Suites
          {f" · PassRate {pass_rate:.0%}" if pass_rate is not None else ""}
        </div>
      </div>
    </div>

    {_render_coverage_table(coverage_files, coverage_totals, coverage_source, base)}

    {_render_function_table(function_rows, coverage_source, base)}

    <!-- Test Suites List -->
    <div class="issues-header-bar">
      <div>
        <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff; margin-bottom: 0.35rem;">🧪 Detailed Test Suites & Cases ({len(suites)} Suites)</h2>
        <p style="font-size: 0.875rem; color: var(--text-muted);">
          실패 케이스는 항상 표시되고, 통과 스위트는 한 줄 요약으로 접힙니다.
        </p>
      </div>
      <div>
        <button class="jump-tab-btn" data-toggle-details=".test-case-details">📂 Toggle All Cases</button>
      </div>
    </div>

    {"".join(suite_cards)}
    """
