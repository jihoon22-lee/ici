"""GitHub Markdown Reporter for $GITHUB_STEP_SUMMARY and workflow annotations."""

import html
import os
import re
from urllib.parse import quote, urlsplit

from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineStatus,
    FindingDelta,
    SourceLocation,
    VerificationSuiteResult,
    format_score_display,
    gate_reason,
)
from ici.core.redaction import redact_suite

_BASELINE_DETAIL_LIMIT = 20
_BASELINE_UNCHANGED_DETAIL_LIMIT = 3
_DELTA_STATE_ORDER = {
    DeltaState.NEW: 0,
    DeltaState.MOVED: 1,
    DeltaState.RESOLVED: 2,
    DeltaState.UNCHANGED: 3,
}


def _enum_value(value: object) -> str:
    """Return a readable value for an enum or a legacy string payload."""
    return str(getattr(value, "value", value))


def _baseline_detail_entries(
    comparison: BaselineComparison,
) -> tuple[list[FindingDelta], int, int]:
    """Select a deterministic, issues-first view without truncating JSON data."""
    entries = list(comparison.entries or [])
    entries.sort(
        key=lambda entry: (
            not entry.gated,
            _DELTA_STATE_ORDER.get(entry.state, 99),
            entry.engine_name,
            entry.fingerprint,
            _location_sort_key(entry.current_location or entry.baseline_location),
        )
    )
    changed = [entry for entry in entries if entry.state != DeltaState.UNCHANGED]
    unchanged = [entry for entry in entries if entry.state == DeltaState.UNCHANGED]
    visible = changed[:_BASELINE_DETAIL_LIMIT]
    remaining_slots = _BASELINE_DETAIL_LIMIT - len(visible)
    visible.extend(unchanged[: min(_BASELINE_UNCHANGED_DETAIL_LIMIT, remaining_slots)])
    return (
        visible,
        len(entries) - len(visible),
        max(0, len(unchanged) - len(visible[len(changed[:_BASELINE_DETAIL_LIMIT]) :])),
    )


def _location_sort_key(location: SourceLocation | None) -> tuple[object, ...]:
    if location is None:
        return ("", 0, 0, "")
    return (location.path, location.start_line, location.end_line or 0, location.label)


def _render_baseline_location(
    location: SourceLocation | None,
    repo_url: str | None,
    commit_sha: str | None,
) -> str:
    if location is None:
        return _render_code("—")
    return _make_gh_link(
        location.path,
        location.start_line,
        location.end_line,
        repo_url,
        commit_sha,
    )


def _render_severity_transition(entry: FindingDelta) -> str:
    before = _enum_value(entry.baseline_severity) if entry.baseline_severity is not None else "—"
    after = _enum_value(entry.current_severity) if entry.current_severity is not None else "—"
    return _render_code(f"{before} → {after}")


def _render_baseline_markdown(
    comparison: BaselineComparison,
    suite: VerificationSuiteResult,
    repo_url: str | None,
    commit_sha: str | None,
) -> list[str]:
    """Render a compact baseline summary and an issues-first delta table."""
    visible, omitted, omitted_unchanged = _baseline_detail_entries(comparison)
    gate_label = (
        "❌ FAILED"
        if comparison.gate_failed
        else ("✅ PASSED" if comparison.fail_on_new else "INFO: NOT ENFORCED")
    )
    lines = [
        "### Baseline finding delta\n",
        f"> **Source**: {_render_code(comparison.source_path)}  ",
        f"> **Fail-on-new gate**: {gate_label}  ",
        f"> **Gate reason**: {_escape_inline(gate_reason(suite.results, suite.suite_status, comparison))}\n",
        "| Delta | Count |",
        "|---|---:|",
        f"| New | **{comparison.count(DeltaState.NEW)}** |",
        f"| Unchanged | **{comparison.count(DeltaState.UNCHANGED)}** |",
        f"| Moved | **{comparison.count(DeltaState.MOVED)}** |",
        f"| Resolved | **{comparison.count(DeltaState.RESOLVED)}** |",
        f"| Regressed | **{comparison.regressed_count}** |",
        f"| Gated | **{comparison.gated_count}** |",
    ]

    if comparison.warnings:
        lines.extend(["\n**Compatibility warnings:**"])
        lines.extend(f"> ⚠️ {_escape_inline(warning)}" for warning in comparison.warnings)

    if visible:
        lines.extend(
            [
                "\n<details>",
                f"<summary><b>Issues-first delta details ({len(visible)} shown)</b></summary>\n",
                "| Delta | Engine / Rule | Current location | Baseline location | Severity transition | Gate | Message |",
                "|---|---|---|---|:---:|:---:|---|",
            ]
        )
        for entry in visible:
            state = _enum_value(entry.state).upper()
            gated = "❌" if entry.gated else "—"
            lines.append(
                f"| `{_escape_table_cell(state)}` | {_render_code(f'{entry.engine_name} / {entry.rule_id}')} | "
                f"{_render_baseline_location(entry.current_location, repo_url, commit_sha)} | "
                f"{_render_baseline_location(entry.baseline_location, repo_url, commit_sha)} | "
                f"{_render_severity_transition(entry)} | {gated} | {_escape_table_cell(entry.message or '—')} |"
            )
        lines.append("</details>\n")
    elif comparison.entries:
        lines.append("\n<details><summary>Delta details omitted</summary></details>\n")

    omitted_note = []
    if omitted:
        omitted_note.append(f"{omitted} additional delta row(s) omitted from this view")
    if omitted_unchanged:
        omitted_note.append(f"{omitted_unchanged} unchanged row(s) omitted")
    if omitted_note:
        lines.append(
            f"> Note: {'; '.join(omitted_note)}. The JSON report retains the full inventory.\n"
        )
    return lines


def generate_markdown_report(
    suite: VerificationSuiteResult,
    repo_url: str | None = None,
    commit_sha: str | None = None,
) -> str:
    """Generates a complete, beautiful GitHub-flavored Markdown report."""
    suite = redact_suite(suite)
    status_emoji = {
        EngineStatus.PASS: "✅",
        EngineStatus.WARN: "⚠️",
        EngineStatus.FAIL: "❌",
        EngineStatus.ERROR: "🛑",
        EngineStatus.SKIP: "⏭️",
    }[suite.suite_status]
    status_str = f"**`{suite.suite_status.value}`**"
    tem_part = (
        f" (TEM: **`{suite.tem_score:.2f} / {suite.max_tem_score:.1f}`**)"
        if suite.tem_score is not None
        else ""
    )

    failed_count = max(0, suite.failed_count - suite.error_count)
    md = [
        f"## {status_emoji} `ici` Verification Report — {status_str}{tem_part}\n",
        f"> **Summary**: Total {suite.total_count} engines executed in {suite.duration:.2f}s. "
        f"(**{suite.passed_count} Passed**, **{suite.warned_count} Warnings**, "
        f"**{failed_count} Failed**, **{suite.error_count} Errors**, "
        f"**{suite.skipped_count} Skipped**)\n",
        "| Engine | Status | Summary | Score / Metrics | Duration |",
        "|---|:---:|---|---|:---:|",
    ]

    for res in suite.results:
        badge = {
            EngineStatus.PASS: "🟢 `PASS`",
            EngineStatus.WARN: "🟡 `WARN`",
            EngineStatus.FAIL: "🔴 `FAIL`",
            EngineStatus.ERROR: "🛑 `ERROR`",
            EngineStatus.SKIP: "⏭️ `SKIP`",
        }[res.status]

        score_val = format_score_display(res)
        if score_val != "-":
            score_val = f"<strong>{_render_code(score_val)}</strong>"
        duration_val = f"{res.duration:.2f}s" if res.duration > 0 else "-"
        md.append(
            f"| <strong>{_render_code(res.engine_name)}</strong> | {badge} | "
            f"{_escape_table_cell(res.summary)} | {score_val} | {duration_val} |"
        )

    if suite.baseline_comparison is not None:
        md.extend(
            _render_baseline_markdown(
                suite.baseline_comparison,
                suite,
                repo_url,
                commit_sha,
            )
        )

    md.append("\n---\n")

    # Target Inspections & Breakdown for each Engine
    for res in suite.results:
        if not res.targets:
            continue

        target_count = len(res.targets)
        warn_fail_count = sum(1 for t in res.targets if t.status != EngineStatus.PASS)
        header_badge = (
            f" ({warn_fail_count} issues)" if warn_fail_count > 0 else f" ({target_count} targets)"
        )

        md.append("<details>")
        md.append(
            f"<summary><b>🔍 <code>{_escape_inline(res.engine_name)}</code> "
            f"Detailed Targets & Locations{header_badge}</b></summary>\n"
        )

        md.append("| Location | Symbol / Rule | Status | Details |")
        md.append("|---|---|:---:|---|")

        for target in res.targets:
            loc_link = _make_gh_link(
                target.file_path, target.start_line, target.end_line, repo_url, commit_sha
            )
            status_b = f"`{target.status.value}`"
            sym = _render_code(target.target_name or "-")
            msg = _escape_table_cell(target.message or "-")
            md.append(f"| {loc_link} | {sym} | {status_b} | {msg} |")

        # Include snippet if failures exist
        failed_targets = [
            t
            for t in res.targets
            if t.status in (EngineStatus.FAIL, EngineStatus.ERROR) and t.snippet
        ]
        if failed_targets:
            md.append("\n**Failure Snippets:**\n")
            for ft in failed_targets[:5]:
                md.append(_fenced_snippet(ft.file_path, ft.start_line, ft.snippet))

        md.append("</details>\n")

    return "\n".join(md)


def emit_github_actions_annotations(suite: VerificationSuiteResult) -> None:
    """Emits GitHub Actions workflow commands (::error and ::warning) for PR inline annotations."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    for res in redact_suite(suite).results:
        for t in res.targets:
            if t.status in (EngineStatus.FAIL, EngineStatus.ERROR):
                command = "error"
            elif t.status in (EngineStatus.WARN, EngineStatus.SKIP):
                command = "warning"
            else:
                continue
            file_path = _escape_workflow_property(t.file_path)
            engine_name = _escape_workflow_data(res.engine_name)
            message = _escape_workflow_data(t.message)
            line = _escape_workflow_property(t.start_line)
            print(f"::{command} file={file_path},line={line}::[{engine_name}] {message}")


def write_github_step_summary(markdown_content: str) -> None:
    """Appends the Markdown report to $GITHUB_STEP_SUMMARY if available."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("\n" + markdown_content + "\n")
        except OSError as err:
            _ = err


def _make_gh_link(
    file_path: str,
    start_line: int,
    end_line: int | None = None,
    repo_url: str | None = None,
    commit_sha: str | None = None,
) -> str:
    line_anchor = f"L{start_line}"
    if end_line and end_line > start_line:
        line_anchor += f"-L{end_line}"

    display = f"{file_path}#{line_anchor}"
    escaped_display = _render_code(display)
    if repo_url and commit_sha:
        parsed = urlsplit(repo_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            encoded_file = quote(file_path, safe="/-._~")
            encoded_commit = quote(commit_sha, safe="-._~")
            base = repo_url.rstrip("/")
            url = f"{base}/blob/{encoded_commit}/{encoded_file}#{line_anchor}"
            safe_url = html.escape(url, quote=True)
            return f'<a href="{safe_url}">{escaped_display}</a>'
    return escaped_display


def _escape_inline(value: object) -> str:
    """Escape untrusted text used in an inline HTML/code context."""
    return _escape_html_content(value)


def _escape_table_cell(value: object) -> str:
    """Escape untrusted table content without allowing Markdown row injection."""
    return _escape_html_content(value)


def _escape_html_content(value: object, *, encode_backticks: bool = True) -> str:
    """Escape untrusted text while retaining readable HTML table/code content."""
    escaped = html.escape(str(value), quote=False)
    escaped = escaped.replace("|", "&#124;")
    escaped = escaped.replace("[", "&#91;").replace("]", "&#93;")
    if encode_backticks:
        escaped = escaped.replace(chr(96), "&#96;")
    return escaped.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


def _render_code(value: object) -> str:
    """Render untrusted content in a closed HTML code element."""
    return f"<code>{_escape_html_content(value, encode_backticks=False)}</code>"


def _fenced_snippet(file_path: str, start_line: int, snippet: str) -> str:
    """Choose a fence longer than any run in the untrusted snippet."""
    max_ticks = max((len(match) for match in re.findall(r"`+", snippet)), default=0)
    fence = "`" * max(3, max_ticks + 1)
    return f"{fence}diff\n# {_escape_inline(file_path)}:{start_line}\n{snippet}\n{fence}\n"


def _escape_workflow_data(value: object) -> str:
    """Escape GitHub workflow command data fields."""
    return str(value).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_workflow_property(value: object) -> str:
    """Escape GitHub workflow command properties, including separators."""
    return _escape_workflow_data(value).replace(":", "%3A").replace(",", "%2C")
