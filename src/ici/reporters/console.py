"""Rich Console Reporter with OSC 8 Hyperlinks for IDE Navigation."""

from pathlib import Path
from urllib.parse import quote

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
from ici.reporters.baseline_view import select_baseline_details, severity_transition

_DELTA_STATE_COLOR = {
    DeltaState.NEW: "red",
    DeltaState.MOVED: "yellow",
    DeltaState.RESOLVED: "green",
    DeltaState.UNCHANGED: "dim",
}

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


def _baseline_location_text(location: SourceLocation | None) -> str:
    if location is None:
        return "—"
    line = f"L{location.start_line}"
    if location.end_line is not None and location.end_line > location.start_line:
        line += f"-L{location.end_line}"
    return f"{location.path}:{line}"


def _print_baseline_comparison(
    comparison: BaselineComparison,
    suite: VerificationSuiteResult,
) -> None:
    """Print compact baseline counts followed by bounded, gated-first deltas."""
    gate_label = (
        "[bold red]FAILED[/]"
        if comparison.gate_failed
        else ("[bold green]PASSED[/]" if comparison.fail_on_new else "[dim]NOT ENFORCED[/]")
    )
    summary = (
        f"[bold]Source:[/] {escape(comparison.source_path)}\n"
        f"[bold]Counts:[/] New {comparison.count(DeltaState.NEW)} · "
        f"Unchanged {comparison.count(DeltaState.UNCHANGED)} · "
        f"Moved {comparison.count(DeltaState.MOVED)} · "
        f"Resolved {comparison.count(DeltaState.RESOLVED)} · "
        f"Regressed {comparison.regressed_count} · Gated {comparison.gated_count}\n"
        f"[bold]Fail-on-new gate:[/] {gate_label}\n"
        f"[bold]Gate reason:[/] {escape(gate_reason(suite.results, suite.suite_status, comparison))}"
    )
    if comparison.warnings:
        summary += "\n[bold yellow]Compatibility warnings:[/]"
        summary += "\n" + "\n".join(
            f"  [yellow]•[/] {escape(warning)}" for warning in comparison.warnings
        )
    console.print(
        Panel(
            summary,
            title="[bold cyan]Baseline Finding Delta[/]",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    selection = select_baseline_details(comparison)
    if selection.visible:
        lines: list[str] = []
        for entry in selection.visible:
            state = getattr(entry.state, "value", entry.state)
            state_color = _DELTA_STATE_COLOR.get(entry.state, "white")
            gate_marker = " [bold red]GATED[/]" if entry.gated else ""
            before, after = severity_transition(entry)
            lines.append(
                f"[{state_color}]• {escape(str(state).upper())}[/]"
                f"{gate_marker} [bold]{escape(entry.engine_name)}[/]"
                f" / {escape(entry.rule_id)} — {escape(entry.message or '—')}"
            )
            lines.append(
                f"    [dim]Current:[/] {escape(_baseline_location_text(entry.current_location))}"
                f"  [dim]Baseline:[/] {escape(_baseline_location_text(entry.baseline_location))}"
                f"  [dim]Severity:[/] {escape(f'{before} → {after}')}"
            )
        if selection.omitted_changed or selection.omitted_unchanged:
            notes = []
            if selection.omitted_changed:
                notes.append(f"{selection.omitted_changed} additional delta row(s) omitted")
            if selection.omitted_unchanged:
                notes.append(f"{selection.omitted_unchanged} unchanged row(s) omitted")
            lines.append(f"[dim]Note: {'; '.join(notes)}; JSON retains the full inventory.[/]")
        console.print(
            Panel(
                "\n".join(lines),
                title="[bold yellow]Baseline Issues-First Details[/]",
                border_style="yellow",
                box=box.SQUARE,
            )
        )
    elif comparison.entries:
        console.print(
            Panel(
                "[dim]Delta details omitted from the terminal view; JSON retains the full inventory.[/]",
                title="[bold yellow]Baseline Details[/]",
                border_style="yellow",
                box=box.SQUARE,
            )
        )


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

    if suite.baseline_comparison is not None:
        _print_baseline_comparison(suite.baseline_comparison, suite)

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
