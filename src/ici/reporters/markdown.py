"""GitHub Markdown Reporter for $GITHUB_STEP_SUMMARY and Sticky PR Comments."""

import os

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
    status_emoji = (
        "✅"
        if suite.suite_status == EngineStatus.PASS
        else ("⚠️" if suite.suite_status == EngineStatus.WARN else "❌")
    )
    status_str = f"**`{suite.suite_status.value}`**"
    tem_part = (
        f" (TEM: **`{suite.tem_score:.2f} / {suite.max_tem_score:.1f}`**)"
        if suite.tem_score is not None
        else ""
    )

    md = [
        f"## {status_emoji} `ici` Verification Report — {status_str}{tem_part}\n",
        f"> **Summary**: Total {suite.total_count} engines executed in {suite.duration:.2f}s. "
        f"(**{suite.passed_count} Passed**, **{suite.warned_count} Warnings**, **{suite.failed_count} Failed**)\n",
        "| Engine | Status | Summary | Score / Metrics | Duration |",
        "|---|:---:|---|---|:---:|",
    ]

    for res in suite.results:
        badge = f"`{res.status.value}`"
        if res.status == EngineStatus.PASS:
            badge = "🟢 `PASS`"
        elif res.status == EngineStatus.WARN:
            badge = "🟡 `WARN`"
        elif res.status == EngineStatus.FAIL:
            badge = "🔴 `FAIL`"

        score_val = format_score_display(res)
        if score_val != "-":
            score_val = f"**`{score_val}`**"
        duration_val = f"{res.duration:.2f}s" if res.duration > 0 else "-"
        md.append(
            f"| **`{res.engine_name}`** | {badge} | {res.summary} | {score_val} | {duration_val} |"
        )

    md.append("\n---\n")

    # Target Inspections & Breakdown for each Engine
    for res in suite.results:
        if not res.targets:
            continue

        target_count = len(res.targets)
        warn_fail_count = sum(
            1 for t in res.targets if t.status in (EngineStatus.WARN, EngineStatus.FAIL)
        )
        header_badge = (
            f" ({warn_fail_count} issues)" if warn_fail_count > 0 else f" ({target_count} targets)"
        )

        md.append("<details>")
        md.append(
            f"<summary><b>🔍 <code>{res.engine_name}</code> Detailed Targets & Locations{header_badge}</b></summary>\n"
        )

        md.append("| Location | Symbol / Rule | Status | Details |")
        md.append("|---|---|:---:|---|")

        for target in res.targets:
            loc_link = _make_gh_link(
                target.file_path, target.start_line, target.end_line, repo_url, commit_sha
            )
            status_b = f"`{target.status.value}`"
            sym = target.target_name or "-"
            msg = target.message or "-"
            md.append(f"| {loc_link} | `{sym}` | {status_b} | {msg} |")

        # Include snippet if failures exist
        failed_targets = [t for t in res.targets if t.status == EngineStatus.FAIL and t.snippet]
        if failed_targets:
            md.append("\n**Failure Snippets:**\n")
            for ft in failed_targets[:5]:
                md.append(f"```diff\n# {ft.file_path}:{ft.start_line}\n{ft.snippet}\n```\n")

        md.append("</details>\n")

    return "\n".join(md)


def emit_github_actions_annotations(suite: VerificationSuiteResult) -> None:
    """Emits GitHub Actions workflow commands (::error and ::warning) for PR inline annotations."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    for res in suite.results:
        for t in res.targets:
            if t.status == EngineStatus.FAIL:
                print(
                    f"::error file={t.file_path},line={t.start_line}::[{res.engine_name}] {t.message}"
                )
            elif t.status == EngineStatus.WARN:
                print(
                    f"::warning file={t.file_path},line={t.start_line}::[{res.engine_name}] {t.message}"
                )


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
    if repo_url and commit_sha:
        url = f"{repo_url.rstrip('/')}/blob/{commit_sha}/{file_path}#{line_anchor}"
        return f"[{display}]({url})"
    return f"`{display}`"
