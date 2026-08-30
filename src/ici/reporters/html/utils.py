"""Shared HTML rendering helpers — status theming, location links, escaping."""

import html
from dataclasses import dataclass
from pathlib import Path

from ici.core.findings import findings_for_result
from ici.core.models import EngineResult, EngineStatus, FindingSeverity


@dataclass(frozen=True)
class HtmlIssue:
    """Reporter-neutral fields required by the complete HTML issues tab."""

    engine_name: str
    badge: str
    status: EngineStatus
    file_path: str
    start_line: int
    end_line: int | None
    rule_id: str
    message: str
    snippet: str


def _get_status_theme(status: EngineStatus) -> tuple[str, str, str]:
    """Returns color, bg, and border for overall status."""
    if status == EngineStatus.PASS:
        return "#10b981", "rgba(16, 185, 129, 0.15)", "#10b981"
    if status == EngineStatus.WARN:
        return "#f59e0b", "rgba(245, 158, 11, 0.15)", "#f59e0b"
    if status == EngineStatus.SKIP:
        return "#9ca3af", "rgba(156, 163, 175, 0.15)", "#9ca3af"
    if status == EngineStatus.ERROR:
        return "#b91c1c", "rgba(185, 28, 28, 0.15)", "#b91c1c"
    return "#ef4444", "rgba(239, 68, 68, 0.15)", "#ef4444"


def _extract_suite_data(
    results: list[EngineResult],
    project_root: Path | None = None,
) -> tuple[dict[str, EngineResult], list[HtmlIssue], int, int, int, int, int]:
    """Extracts engine map, actionable issues list, and status counts."""
    eng_map: dict[str, EngineResult] = {}
    all_issues: list[HtmlIssue] = []
    p_cnt = 0
    w_cnt = 0
    f_cnt = 0
    e_cnt = 0
    s_cnt = 0

    for r in results:
        eng_map[r.engine_name] = r
        if r.status == EngineStatus.PASS:
            p_cnt += 1
        elif r.status == EngineStatus.WARN:
            w_cnt += 1
        elif r.status == EngineStatus.FAIL:
            f_cnt += 1
        elif r.status == EngineStatus.ERROR:
            e_cnt += 1
        else:
            s_cnt += 1

        try:
            findings = findings_for_result(r, project_root)
        except (TypeError, ValueError):
            findings = []
        issue_count = 0
        for finding in findings:
            if finding.severity == FindingSeverity.INFO or finding.suppression.suppressed:
                continue
            severity_status = (
                EngineStatus.FAIL
                if finding.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH)
                else EngineStatus.WARN
            )
            all_issues.append(
                HtmlIssue(
                    engine_name=r.engine_name,
                    badge=finding.severity.value.upper(),
                    status=severity_status,
                    file_path=finding.primary_location.path,
                    start_line=finding.primary_location.start_line,
                    end_line=finding.primary_location.end_line,
                    rule_id=finding.tool_rule_id or finding.rule_id,
                    message=finding.message,
                    snippet=finding.snippet,
                )
            )
            issue_count += 1
        if r.status in (EngineStatus.ERROR, EngineStatus.SKIP) and issue_count == 0:
            all_issues.append(
                HtmlIssue(
                    engine_name=r.engine_name,
                    badge=r.status.value,
                    status=r.status,
                    file_path="",
                    start_line=1,
                    end_line=None,
                    rule_id="engine",
                    message=r.summary,
                    snippet="",
                )
            )

    return eng_map, all_issues, p_cnt, w_cnt, f_cnt, e_cnt, s_cnt


def _escape_html_attr(value: object) -> str:
    """Escape an attribute and encode line breaks instead of emitting controls."""
    return html.escape(str(value), quote=True).replace("\r", "&#13;").replace("\n", "&#10;")


def _location_controls(file_path: str, line: int, base: Path, label: str | None = None) -> str:
    """Render a location control using escaped data attributes only."""
    rel_path = str(file_path)
    display = label if label is not None else f"{rel_path}:{line}"
    abs_path = str((base / rel_path).resolve())
    rel_attr = _escape_html_attr(rel_path)
    abs_attr = _escape_html_attr(abs_path)
    line_attr = _escape_html_attr(line)
    display_html = html.escape(display)
    return (
        "<span class='loc-link-group'>"
        f"<a href='#' class='loc-link' data-abs-path=\"{abs_attr}\" "
        f'data-rel-path="{rel_attr}" data-line="{line_attr}"><code>{display_html}</code></a>'
        f'<button class=\'btn-copy-loc\' data-rel-path="{rel_attr}" data-line="{line_attr}" '
        "title='경로 복사 (gvim/CLI용)'>📋</button>"
        "</span>"
    )


def _status_color(status: EngineStatus) -> str:
    return {
        EngineStatus.PASS: "#10b981",
        EngineStatus.WARN: "#f59e0b",
        EngineStatus.FAIL: "#ef4444",
        EngineStatus.ERROR: "#dc2626",
        EngineStatus.SKIP: "#9ca3af",
    }[status]


def _cov_color(pct: float | None) -> str:
    if pct is None:
        return "#6b7280"
    if pct >= 90.0:
        return "#10b981"
    if pct >= 75.0:
        return "#f59e0b"
    return "#ef4444"
