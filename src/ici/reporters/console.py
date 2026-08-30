"""Rich Console Reporter with OSC 8 Hyperlinks for IDE Navigation."""

from pathlib import Path
from urllib.parse import quote

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ici.core.models import EngineStatus, VerificationSuiteResult, format_score_display, gate_reason
from ici.core.redaction import redact_suite

console = Console()


def format_status_badge(status: EngineStatus) -> str:
    if status == EngineStatus.PASS:
        return "[bold green] PASS [/]"
    elif status == EngineStatus.WARN:
        return "[bold yellow] WARN [/]"
    elif status == EngineStatus.FAIL:
        return "[bold red] FAIL [/]"
    elif status == EngineStatus.ERROR:
        return "[bold red] ERROR [/]"
    else:
        return "[dim] SKIP [/]"


def make_terminal_link(
    file_path: str, line: int | None = None, base_dir: Path | None = None
) -> str:
    """Formats an OSC 8 terminal hyperlink or standard clickable path."""
    base = (base_dir or Path.cwd()).resolve()
    abs_path = (base / file_path).resolve()
    line_str = f":{line}" if line else ""
    display_str = f"{file_path}{line_str}"
    target_url = f"file://{quote(str(abs_path), safe='/:@-._~')}"

    return f"[link={target_url}]{escape(display_str)}[/link]"


def print_suite_dashboard(suite: VerificationSuiteResult, base_dir: Path | None = None) -> None:
    """Prints the comprehensive Rich terminal dashboard for all verification engines."""
    suite = redact_suite(suite)
    base = (base_dir or Path.cwd()).resolve()

    status_color = (
        "green"
        if suite.suite_status == EngineStatus.PASS
        else ("yellow" if suite.suite_status == EngineStatus.WARN else "red")
    )
    banner_text = Text(
        f"ici Unified Verification Suite — {suite.suite_status.value}", style=f"bold {status_color}"
    )
    console.print(Panel(banner_text, box=box.DOUBLE, border_style=status_color))

    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=True)
    table.add_column("Engine", style="bold", width=16)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Summary", style="white")
    table.add_column("Score / Metrics", justify="right", width=22)
    table.add_column("Duration", justify="right", width=10)

    for res in suite.results:
        status_badge = format_status_badge(res.status)
        score_str = format_score_display(res)
        duration_str = f"{res.duration:.2f}s" if res.duration > 0 else "-"
        table.add_row(
            escape(res.engine_name),
            status_badge,
            escape(res.summary),
            escape(score_str),
            escape(duration_str),
        )

    console.print(table)

    # Print Drill-Down for Failures, Errors, and Warnings
    issues = [
        r
        for r in suite.results
        if r.status in (EngineStatus.FAIL, EngineStatus.ERROR, EngineStatus.WARN, EngineStatus.SKIP)
    ]
    if issues:
        console.print(
            "\n[bold red]── Action Required: Violations & Issues Drill-Down ──[/bold red]"
        )
        for issue in issues:
            border = "red" if issue.status in (EngineStatus.FAIL, EngineStatus.ERROR) else "yellow"
            issue_panel_content = []

            if issue.engine_name == "dup" and "clone_groups" in issue.extra:
                for g in issue.extra["clone_groups"]:
                    occ_links = [
                        make_terminal_link(occ["file_path"], occ["start_line"], base)
                        for occ in g["occurrences"]
                    ]
                    line_hdr = (
                        f"[{border}]• [CloneGroup#{escape(str(g['id']))}][/] [bold]"
                        f"{' <-> '.join(occ_links)}[/] ({escape(str(g['lines_count']))} duplicate lines)"
                    )
                    issue_panel_content.append(line_hdr)
                    if g.get("snippet"):
                        for s_line in g["snippet"].strip().splitlines()[:4]:
                            issue_panel_content.append(
                                f"    [dim white]│[/dim white] {escape(s_line)}"
                            )
            else:
                for target in issue.targets:
                    if target.status != EngineStatus.PASS:
                        link_str = make_terminal_link(target.file_path, target.start_line, base)
                        line_hdr = f"[{border}]• [{target.status.value}][/] [bold]{link_str}[/] ({escape(target.target_name or 'issue')})"
                        issue_panel_content.append(line_hdr)
                        if target.message:
                            issue_panel_content.append(f"  [dim]{escape(target.message)}[/dim]")
                        if target.snippet:
                            snippet_lines = target.snippet.strip().splitlines()
                            for s_line in snippet_lines[:6]:
                                issue_panel_content.append(
                                    f"    [dim white]│[/dim white] {escape(s_line)}"
                                )

            if issue_panel_content:
                console.print(
                    Panel(
                        "\n".join(issue_panel_content),
                        title=f"[{border}]{escape(issue.engine_name)} Issues[/]",
                        border_style=border,
                        box=box.SQUARE,
                    )
                )

    # Print Overall Footer
    tem_str = (
        f" | TEM Score: [bold cyan]{suite.tem_score:.2f} / {suite.max_tem_score:.1f}[/bold cyan]"
        if suite.tem_score is not None
        else ""
    )
    failed_count = max(0, suite.failed_count - suite.error_count)
    summary_text = (
        f"[bold]Total Engines:[/] {suite.total_count}  "
        f"([green]Pass: {suite.passed_count}[/green], "
        f"[yellow]Warn: {suite.warned_count}[/yellow], "
        f"[red]Fail: {failed_count}[/red], "
        f"[red]Error: {suite.error_count}[/red], "
        f"[dim]Skip: {suite.skipped_count}[/dim])"
        f"{tem_str}  |  "
        f"[dim]Total Time: {suite.duration:.2f}s[/dim]\n"
        # The tally counts engine statuses; the verdict comes from a different
        # rule. Printing one without the other is how a report could say
        # "Error: 0" and still be an ERROR with nothing on screen explaining it.
        f"[bold {status_color}]Suite: {suite.suite_status.value}[/] — "
        f"{gate_reason(suite.results, suite.suite_status, suite.baseline_comparison)}"
    )
    console.print(Panel(summary_text, style="white", border_style="cyan"))


def print_line_distribution_chart(code: int, comment: int, blank: int, total: int) -> None:
    """Prints Rich-styled line count distribution bar chart."""
    scale = 50.0 / (total if total > 0 else 1)
    code_bar = "█" * int(code * scale)
    comm_bar = "░" * int(comment * scale)
    blnk_bar = "▒" * int(blank * scale)

    console.print(
        f" [cyan]Code Lines[/cyan]    [{code_bar:<50}] {code:>6} ({code / (total or 1) * 100:.1f}%)"
    )
    console.print(
        f" [green]Comment Lines[/green] [{comm_bar:<50}] {comment:>6} ({comment / (total or 1) * 100:.1f}%)"
    )
    console.print(
        f" [dim]Blank Lines[/dim]   [{blnk_bar:<50}] {blank:>6} ({blank / (total or 1) * 100:.1f}%)\n"
    )
    console.print(f" [bold]Total Volume:[/] {total:,} Lines")
