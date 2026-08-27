"""Static type checking section — its own tab, split by why each file is listed."""

import html
from collections import Counter
from pathlib import Path

from ici.core.models import EngineResult, EngineStatus
from ici.reporters.html.utils import _get_status_theme, _location_controls


def _summary_cards(res: EngineResult, counts: Counter) -> str:
    color, bg, _ = _get_status_theme(res.status)
    cards = [
        ("Status", f"<span style='color:{color}'>{res.status.value}</span>", bg),
        ("Findings", str(counts.get(EngineStatus.FAIL, 0) + counts.get(EngineStatus.ERROR, 0)), ""),
        ("Warnings", str(counts.get(EngineStatus.WARN, 0)), ""),
        ("Not checked", str(counts.get(EngineStatus.SKIP, 0)), ""),
    ]
    tiles = "".join(
        f"<div class='stat-card'><div class='stat-label'>{label}</div>"
        f"<div class='stat-value'>{value}</div></div>"
        for label, value, _ in cards
    )
    return f"<div class='stat-grid'>{tiles}</div>"


def _finding_rows(targets: list, base: Path) -> str:
    rows = []
    for t in targets:
        location = (
            _location_controls(t.file_path, t.start_line, base)
            if t.file_path
            else "<span class='issue-no-location'>engine result</span>"
        )
        rows.append(
            f"<div class='target-item'>"
            f"  <span class='badge' style='color:{_get_status_theme(t.status)[0]}'>"
            f"{t.status.value}</span> {location}"
            f"  <span class='target-sym'>[{html.escape(t.target_name or 'target')}]</span> "
            f"  <span class='target-msg'>{html.escape(t.message)}</span>"
            f"</div>"
        )
    return "".join(rows)


def _skipped_block(skipped: list, base: Path) -> str:
    """Files the checker did not examine, collapsed.

    C++ type checking is not implemented, so on a C++ project every source file
    lands here. Listing them expanded — which is what happened before this tab
    existed — buries whatever real findings there are under a wall of
    "not checked", and there is nothing to act on in that wall.
    """
    if not skipped:
        return ""
    reasons = Counter(t.message for t in skipped)
    reason_line = "; ".join(f"{msg} ({count})" for msg, count in reasons.most_common(3))
    return (
        f"<details class='target-details'>"
        f"  <summary>{len(skipped)} file(s) not type-checked — {html.escape(reason_line)}</summary>"
        f"  <div class='targets-list'>{_finding_rows(skipped, base)}</div>"
        f"</details>"
    )


def _render_type_section(type_res: EngineResult | None, base: Path) -> str:
    """Renders the dedicated Static Types tab."""
    if not type_res:
        return "<div class='card'>No type checking data available.</div>"

    counts = Counter(t.status for t in type_res.targets)
    findings = [
        t
        for t in type_res.targets
        if t.status in (EngineStatus.FAIL, EngineStatus.ERROR, EngineStatus.WARN)
    ]
    skipped = [t for t in type_res.targets if t.status == EngineStatus.SKIP]

    body = _summary_cards(type_res, counts)
    body += f"<div class='card'><div class='card-title'>{html.escape(type_res.summary)}</div></div>"

    if findings:
        body += (
            "<div class='card'><div class='card-title'>Findings</div>"
            f"<div class='targets-list'>{_finding_rows(findings, base)}</div></div>"
        )
    elif not skipped:
        body += "<div class='card'>✅ No type findings.</div>"

    skipped_html = _skipped_block(skipped, base)
    if skipped_html:
        body += f"<div class='card'>{skipped_html}</div>"
    return body
