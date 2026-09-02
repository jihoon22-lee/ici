"""GitHub Markdown Reporter for $GITHUB_STEP_SUMMARY and workflow annotations."""

import html
import os
import re
from urllib.parse import quote, urlsplit

from ici.core.capabilities import CapabilityInventory
from ici.core.models import (
    BaselineComparison,
    DeltaState,
    EngineStatus,
    SourceLocation,
    VerificationSuiteResult,
    format_score_display,
    gate_reason,
)
from ici.core.redaction import redact_suite
from ici.reporters.baseline_view import enum_value, select_baseline_details, severity_transition

MAX_GITHUB_TARGET_ROWS_PER_ENGINE = 100
MAX_GITHUB_ANNOTATIONS = 50
MAX_GITHUB_SUMMARY_BYTES = 900_000
_SUMMARY_TRUNCATION_NOTICE = (
    "\n\n> ⚠️ GitHub step summary truncated at its bounded byte limit. "
    "Full JSON and HTML reports retain all details.\n"
)


def _render_capability_markdown(inventory: CapabilityInventory) -> list[str]:
    """Render a compact summary plus a collapsed complete tool inventory."""

    capabilities = list(inventory.capabilities.values())
    ready = sum(item.available and item.complete for item in capabilities)
    incomplete = sum(item.available and not item.complete for item in capabilities)
    unavailable = sum(not item.available for item in capabilities)
    health = "✅ READY" if inventory.healthy else "⚠️ ATTENTION"
    lines = [
        "### Tool capability snapshot\n",
        f"> **Health**: {health}  ",
        f"> **Inventory**: {len(capabilities)} total · {ready} ready · "
        f"{incomplete} incomplete · {unavailable} unavailable  ",
    ]
    if inventory.missing_required:
        lines.append(
            f"> **Missing required**: {_render_code(', '.join(inventory.missing_required))}  "
        )
    if inventory.incomplete_required:
        lines.append(
            f"> **Incomplete required**: {_render_code(', '.join(inventory.incomplete_required))}  "
        )
    lines.extend(
        [
            "\n<details>",
            f"<summary><b>Complete tool inventory ({len(capabilities)})</b></summary>\n",
            "| Tool | State | Policy | Version | Details |",
            "|---|:---:|---|---|---|",
        ]
    )
    for name, capability in inventory.capabilities.items():
        requirement = inventory.requirements[name]
        state = (
            "ready"
            if capability.available and capability.complete
            else ("incomplete" if capability.available else "unavailable")
        )
        if requirement.required:
            policy = "required by " + ", ".join(requirement.required_by)
        elif requirement.optional:
            policy = "optional for " + ", ".join(requirement.optional_by)
        else:
            policy = "registry"
        details = ", ".join(f"{key}={value}" for key, value in capability.details.items()) or "—"
        lines.append(
            f"| {_render_code(capability.name)} | {_render_code(state)} | "
            f"{_escape_table_cell(policy)} | {_escape_table_cell(capability.version or '—')} | "
            f"{_escape_table_cell(details)} |"
        )
    lines.append("</details>\n")
    return lines


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


def _render_severity_transition(before: str, after: str) -> str:
    return _render_code(f"{before} → {after}")


def _render_baseline_markdown(
    comparison: BaselineComparison,
    suite: VerificationSuiteResult,
    repo_url: str | None,
    commit_sha: str | None,
) -> list[str]:
    """Render a compact baseline summary and an issues-first delta table."""
    selection = select_baseline_details(comparison)
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

    if selection.visible:
        lines.extend(
            [
                "\n<details>",
                f"<summary><b>Issues-first delta details ({len(selection.visible)} shown)</b></summary>\n",
                "| Delta | Engine / Rule | Current location | Baseline location | Severity transition | Gate | Message |",
                "|---|---|---|---|:---:|:---:|---|",
            ]
        )
        for entry in selection.visible:
            state = enum_value(entry.state).upper()
            gated = "❌" if entry.gated else "—"
            before, after = severity_transition(entry)
            lines.append(
                f"| `{_escape_table_cell(state)}` | {_render_code(f'{entry.engine_name} / {entry.rule_id}')} | "
                f"{_render_baseline_location(entry.current_location, repo_url, commit_sha)} | "
                f"{_render_baseline_location(entry.baseline_location, repo_url, commit_sha)} | "
                f"{_render_severity_transition(before, after)} | {gated} | {_escape_table_cell(entry.message or '—')} |"
            )
        lines.append("</details>\n")
    elif comparison.entries:
        lines.append("\n<details><summary>Delta details omitted</summary></details>\n")

    omitted_note = []
    if selection.omitted_changed:
        omitted_note.append(
            f"{selection.omitted_changed} additional delta row(s) omitted from this view"
        )
    if selection.omitted_unchanged:
        omitted_note.append(f"{selection.omitted_unchanged} unchanged row(s) omitted")
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
    """Generate a bounded, issues-first GitHub-flavored Markdown report."""
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
        f"**{suite.skipped_count} Skipped**, "
        f"**{sum(result.cache_hit for result in suite.results)} Cache Hits**)\n",
        "| Engine | Status | Summary | Score / Metrics | Cache | Duration |",
        "|---|:---:|---|---|:---:|:---:|",
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
            f"{_escape_table_cell(res.summary)} | {score_val} | "
            f"{'HIT' if res.cache_hit else '—'} | {duration_val} |"
        )

    if suite.capability_inventory is not None:
        md.extend(_render_capability_markdown(suite.capability_inventory))

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

        ordered_targets = sorted(
            enumerate(res.targets),
            key=lambda item: (_target_status_rank(item[1].status), item[0]),
        )
        visible_targets = [
            target for _, target in ordered_targets[:MAX_GITHUB_TARGET_ROWS_PER_ENGINE]
        ]
        for target in visible_targets:
            loc_link = _make_gh_link(
                target.file_path, target.start_line, target.end_line, repo_url, commit_sha
            )
            status_b = f"`{target.status.value}`"
            sym = _render_code(target.target_name or "-")
            msg = _escape_table_cell(target.message or "-")
            md.append(f"| {loc_link} | {sym} | {status_b} | {msg} |")

        omitted_targets = target_count - len(visible_targets)
        if omitted_targets:
            md.append(
                f"\n> {omitted_targets} target row(s) omitted from this bounded GitHub view. "
                "The JSON and HTML reports retain the full inventory.\n"
            )

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

    candidates = [
        (result_index, target_index, result.engine_name, target)
        for result_index, result in enumerate(redact_suite(suite).results)
        for target_index, target in enumerate(result.targets)
        if target.status
        in (EngineStatus.FAIL, EngineStatus.ERROR, EngineStatus.WARN, EngineStatus.SKIP)
    ]
    candidates.sort(
        key=lambda item: (
            _annotation_status_rank(item[3].status),
            item[0],
            item[1],
        )
    )
    for _, _, engine_name_raw, target in candidates[:MAX_GITHUB_ANNOTATIONS]:
        command = "error" if target.status in (EngineStatus.FAIL, EngineStatus.ERROR) else "warning"
        file_path = _escape_workflow_property(target.file_path)
        engine_name = _escape_workflow_data(engine_name_raw)
        message = _escape_workflow_data(target.message)
        line = _escape_workflow_property(target.start_line)
        print(f"::{command} file={file_path},line={line}::[{engine_name}] {message}")
    omitted = len(candidates) - min(len(candidates), MAX_GITHUB_ANNOTATIONS)
    if omitted:
        notice = _escape_workflow_data(
            f"{omitted} additional ici annotation(s) omitted from this bounded workflow view; "
            "the JSON and HTML reports retain the full inventory"
        )
        print(f"::notice::{notice}")


def write_github_step_summary(markdown_content: str) -> None:
    """Appends the Markdown report to $GITHUB_STEP_SUMMARY if available."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            existing_size = os.path.getsize(summary_path) if os.path.exists(summary_path) else 0
            remaining = max(0, MAX_GITHUB_SUMMARY_BYTES - existing_size)
            payload = _bounded_summary_payload(markdown_content, remaining)
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write(payload)
        except OSError as err:
            _ = err


def _target_status_rank(status: EngineStatus) -> int:
    return {
        EngineStatus.ERROR: 0,
        EngineStatus.FAIL: 0,
        EngineStatus.WARN: 1,
        EngineStatus.SKIP: 2,
        EngineStatus.PASS: 3,
    }[status]


def _annotation_status_rank(status: EngineStatus) -> int:
    return 0 if status in (EngineStatus.ERROR, EngineStatus.FAIL) else 1


def _bounded_summary_payload(markdown_content: str, maximum: int) -> str:
    """Return one valid UTF-8 append payload no larger than ``maximum`` bytes."""

    if maximum <= 0:
        return ""
    payload = f"\n{markdown_content}\n"
    encoded = payload.encode("utf-8")
    if len(encoded) <= maximum:
        return payload

    suffix = _SUMMARY_TRUNCATION_NOTICE.encode("utf-8")
    if maximum <= len(suffix):
        return suffix[:maximum].decode("utf-8", errors="ignore")
    prefix = encoded[: maximum - len(suffix)].decode("utf-8", errors="ignore").rstrip()
    return prefix + _SUMMARY_TRUNCATION_NOTICE


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
