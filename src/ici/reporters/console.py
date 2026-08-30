"""Rich Console Reporter with OSC 8 Hyperlinks for IDE Navigation."""

from pathlib import Path
from urllib.parse import quote

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
from ici.reporters.baseline_view import select_baseline_details, severity_transition
from ici.reporters.issue_view import (
    DEFAULT_MAX_LOCATIONS,
    ConsoleOptions,
    IssueGroup,
    IssueLocation,
    IssueSelection,
    issue_bucket,
    select_issue_groups,
)

_DELTA_STATE_COLOR = {
    DeltaState.NEW: "red",
    DeltaState.MOVED: "yellow",
    DeltaState.RESOLVED: "green",
    DeltaState.UNCHANGED: "dim",
}

console = Console()


def _capability_state(available: bool, complete: bool) -> str:
    if not available:
        return "unavailable"
    return "ready" if complete else "incomplete"


def _print_capability_inventory(
    inventory: CapabilityInventory,
    output_console: Console,
) -> None:
    """Print one compact projection of the suite-owned tool snapshot."""

    capabilities = list(inventory.capabilities.values())
    ready = sum(item.available and item.complete for item in capabilities)
    incomplete = sum(item.available and not item.complete for item in capabilities)
    unavailable = sum(not item.available for item in capabilities)
    required = [
        inventory.capabilities[name]
        for name, policy in inventory.requirements.items()
        if policy.required
    ]
    required_rows = (
        ", ".join(
            f"{escape(item.name)}={_capability_state(item.available, item.complete)}"
            for item in required
        )
        or "none"
    )
    health = "[bold green]READY[/]" if inventory.healthy else "[bold yellow]ATTENTION[/]"
    summary = (
        f"[bold]Snapshot:[/] {len(capabilities)} tools · [green]{ready} ready[/] · "
        f"[yellow]{incomplete} incomplete[/] · [dim]{unavailable} unavailable[/]\n"
        f"[bold]Required policy:[/] {required_rows}\n"
        f"[bold]Capability health:[/] {health}"
    )
    if inventory.missing_required or inventory.incomplete_required:
        issues = [
            *(f"missing: {escape(name)}" for name in inventory.missing_required),
            *(f"incomplete: {escape(name)}" for name in inventory.incomplete_required),
        ]
        summary += "\n[bold yellow]Required attention:[/] " + "; ".join(issues)
    output_console.print(
        Panel(
            summary,
            title="[bold cyan]Tool Capability Snapshot[/]",
            border_style="cyan" if inventory.healthy else "yellow",
            box=box.ROUNDED,
        )
    )


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
    output_console: Console,
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
    output_console.print(
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
        output_console.print(
            Panel(
                "\n".join(lines),
                title="[bold yellow]Baseline Issues-First Details[/]",
                border_style="yellow",
                box=box.SQUARE,
            )
        )
    elif comparison.entries:
        output_console.print(
            Panel(
                "[dim]Delta details omitted from the terminal view; JSON retains the full inventory.[/]",
                title="[bold yellow]Baseline Details[/]",
                border_style="yellow",
                box=box.SQUARE,
            )
        )


def _issue_color(group: IssueGroup) -> str:
    if group.severity.value in ("critical", "high"):
        return "red"
    if group.severity.value == "medium":
        return "yellow"
    return "cyan"


def _issue_location_link(location: IssueLocation, base_dir: Path) -> str:
    link = make_terminal_link(location.path, location.start_line, base_dir)
    if location.end_line > location.start_line:
        link += f"[dim]-L{location.end_line}[/]"
    return link


def _render_issue_group(
    group: IssueGroup,
    base_dir: Path,
    options: ConsoleOptions,
) -> list[str]:
    color = _issue_color(group)
    identity = f"{group.engine_name} / {group.rule_id}"
    if group.clone_group_id:
        identity += f" / clone {group.clone_group_id}"
    lines = [
        f"[{color}]• {group.severity.value.upper()}[/] "
        f"[bold]{escape(identity)}[/] — {escape(group.message or '—')}"
    ]
    location_limit = len(group.locations) if options.verbose else DEFAULT_MAX_LOCATIONS
    for location in group.locations[:location_limit]:
        lines.append(f"    [dim]↳[/] {_issue_location_link(location, base_dir)}")
    hidden_locations = len(group.locations) - location_limit
    if hidden_locations > 0:
        lines.append(f"    [dim]… {hidden_locations} additional location(s) hidden[/]")
    snippet_limit = 4 if options.verbose else 1
    for snippet_line in group.snippet.strip().splitlines()[:snippet_limit]:
        lines.append(f"    [dim white]│[/dim white] {escape(snippet_line)}")
    if group.original_finding_count > 1:
        lines.append(f"    [dim]{group.original_finding_count} original findings represented[/]")
    return lines


def _print_issue_details(
    selection: IssueSelection,
    base_dir: Path,
    options: ConsoleOptions,
    output_console: Console,
) -> None:
    if selection.visible_groups:
        output_console.print("\n[bold red]── Issues-First Drill-Down ──[/bold red]")
        buckets: dict[str, list[IssueGroup]] = {}
        for group in selection.visible_groups:
            buckets.setdefault(issue_bucket(group, options.group_by), []).append(group)
        for bucket, groups in buckets.items():
            content = [
                line for group in groups for line in _render_issue_group(group, base_dir, options)
            ]
            output_console.print(
                Panel(
                    "\n".join(content),
                    title=f"[bold]{escape(options.group_by.value.title())}: {escape(bucket)}[/]",
                    border_style="yellow",
                    box=box.SQUARE,
                )
            )

    if selection.total_findings:
        inventory = (
            f"[bold]Actionable findings:[/] {selection.total_findings}  ·  "
            f"[bold]Display groups:[/] {len(selection.visible_groups)}/{selection.total_groups}  ·  "
            f"[bold]Represented:[/] {selection.visible_findings}"
        )
        if selection.hidden_findings or selection.hidden_groups:
            inventory += (
                f"  ·  [yellow]Hidden:[/] {selection.hidden_findings} finding(s) "
                f"in {selection.hidden_groups} group(s)\n"
                f"[dim]Re-run with: {escape(options.rerun_command)}[/]"
            )
        output_console.print(
            Panel(
                inventory,
                title="[bold cyan]Terminal Finding Inventory[/]",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )


def print_suite_dashboard(
    suite: VerificationSuiteResult,
    base_dir: Path | None = None,
    *,
    options: ConsoleOptions | None = None,
    output_console: Console | None = None,
) -> None:
    """Prints the comprehensive Rich terminal dashboard for all verification engines."""
    suite = redact_suite(suite)
    base = (base_dir or Path.cwd()).resolve()
    selected_options = options or ConsoleOptions()
    active_console = output_console or console

    status_color = (
        "green"
        if suite.suite_status == EngineStatus.PASS
        else ("yellow" if suite.suite_status == EngineStatus.WARN else "red")
    )
    banner_text = Text(
        f"ici Unified Verification Suite — {suite.suite_status.value}", style=f"bold {status_color}"
    )
    active_console.print(Panel(banner_text, box=box.DOUBLE, border_style=status_color))

    selection = select_issue_groups(suite, base, selected_options)
    findings_by_engine: dict[str, int] = {}
    for group in selection.all_groups:
        findings_by_engine[group.engine_name] = (
            findings_by_engine.get(group.engine_name, 0) + group.original_finding_count
        )

    compact = active_console.width < 100
    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=True)
    table.add_column("Engine", style="bold", width=12 if compact else 16, no_wrap=True)
    table.add_column("Status", justify="center", width=8 if compact else 10, no_wrap=True)
    table.add_column("Summary", style="white", ratio=1, overflow="fold")
    table.add_column("Issues", justify="right", width=7, no_wrap=True)
    table.add_column(
        "Score / Metrics",
        justify="right",
        width=14 if compact else 22,
        overflow="ellipsis",
        no_wrap=True,
    )
    if not compact:
        table.add_column("Duration", justify="right", width=10, no_wrap=True)

    for res in suite.results:
        status_badge = format_status_badge(res.status)
        score_str = format_score_display(res)
        duration_str = f"{res.duration:.2f}s" if res.duration > 0 else "-"
        row = [
            escape(res.engine_name),
            status_badge,
            escape(res.summary),
            str(findings_by_engine.get(res.engine_name, 0)),
            escape(score_str),
        ]
        if not compact:
            row.append(escape(duration_str))
        table.add_row(*row)

    active_console.print(table)
    if suite.capability_inventory is not None:
        _print_capability_inventory(suite.capability_inventory, active_console)
    _print_issue_details(selection, base, selected_options, active_console)

    if suite.baseline_comparison is not None:
        _print_baseline_comparison(suite.baseline_comparison, suite, active_console)

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
    active_console.print(Panel(summary_text, style="white", border_style="cyan"))


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
