"""GitHub Markdown Reporter for $GITHUB_STEP_SUMMARY and workflow annotations."""

import html
import os
import re
from urllib.parse import quote, urlsplit

from ici.core.models import (
    EngineStatus,
    VerificationSuiteResult,
    format_score_display,
)


def generate_markdown_report(
    suite: VerificationSuiteResult,
    repo_url: str | None = None,
    commit_sha: str | None = None,
) -> str:
    """Generates a complete, beautiful GitHub-flavored Markdown report."""
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

    md = [
        f"## {status_emoji} `ici` Verification Report — {status_str}{tem_part}\n",
        f"> **Summary**: Total {suite.total_count} engines executed in {suite.duration:.2f}s. "
        f"(**{suite.passed_count} Passed**, **{suite.warned_count} Warnings**, "
        f"**{suite.failed_count} Failed**, **{suite.error_count} Errors**, "
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
            score_val = f"**`{score_val}`**"
        duration_val = f"{res.duration:.2f}s" if res.duration > 0 else "-"
        md.append(
            f"| **`{_escape_table_cell(res.engine_name)}`** | {badge} | "
            f"{_escape_table_cell(res.summary)} | {score_val} | {duration_val} |"
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
            sym = _escape_table_cell(target.target_name or "-")
            msg = _escape_table_cell(target.message or "-")
            md.append(f"| {loc_link} | `{sym}` | {status_b} | {msg} |")

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

    for res in suite.results:
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
            print(f"::{command} file={file_path},line={t.start_line}::[{engine_name}] {message}")


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
    escaped_display = _escape_table_cell(display)
    if repo_url and commit_sha:
        parsed = urlsplit(repo_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            encoded_file = quote(file_path, safe="/-._~")
            encoded_commit = quote(commit_sha, safe="-._~")
            base = repo_url.rstrip("/")
            url = f"{base}/blob/{encoded_commit}/{encoded_file}#{line_anchor}"
            return f"[{escaped_display}](<{url}>)"
    return f"`{escaped_display}`"


def _escape_inline(value: object) -> str:
    """Escape untrusted text used inside Markdown code spans or labels."""
    return html.escape(str(value), quote=False).replace("`", "\\`")


def _escape_table_cell(value: object) -> str:
    """Escape untrusted Markdown table content without allowing row injection."""
    escaped = html.escape(str(value), quote=False).replace("`", "\\`")
    escaped = escaped.replace("\\", "\\\\").replace("|", "\\|")
    return escaped.replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


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
