"""Main Typer CLI Application Entrypoint for ici."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from ici import __version__
from ici.config import ConfigError, load_config
from ici.core.baseline import BaselineError
from ici.core.cache import CACHE_KEY_VERSION, AnalysisCache
from ici.core.models import EngineResult, EngineStatus, exit_code_for_status
from ici.core.path_utils import resolve_project_path
from ici.core.pipeline import AnalysisProfile
from ici.core.redaction import redact_engine_result
from ici.core.support import evaluate_support_matrix
from ici.doctor import collect_diagnostics, render_doctor_brief, render_doctor_table
from ici.engines.build import BuildEngine
from ici.engines.cognitive import CognitiveEngine  # noqa: F401
from ici.engines.complexity import (
    ComplexityEngine,  # noqa: F401 - resolved dynamically by CLI registry
)
from ici.engines.cycle import CycleEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.dead import DeadCodeEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.dup import DuplicateEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.exception import (
    ExceptionSafetyEngine,  # noqa: F401 - resolved dynamically by CLI registry
)
from ici.engines.line import LineCountEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.lint import LintEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.publish import ReportInput, ReportPublisher, load_suite_from_json
from ici.engines.resource import ResourceEngine  # noqa: F401
from ici.engines.sanitize import SanitizeEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.security import SecurityEngine  # noqa: F401
from ici.engines.test import TestEngine  # noqa: F401 - resolved dynamically by CLI registry
from ici.engines.type_check import (
    TypeCheckEngine,  # noqa: F401 - resolved dynamically by CLI registry
)
from ici.engines.verify import VerifyOrchestrator
from ici.reporters.console import print_line_distribution_chart
from ici.reporters.issue_view import DEFAULT_MAX_FINDINGS, ConsoleGroupBy, ConsoleOptions
from ici.reporters.json_rep import save_engine_json_report

app = typer.Typer(
    name="ici",
    help=f"Integrated CI Engine v{__version__} — Multi-Language CI/CD Verification & Build Tool",
    add_completion=False,
)
console = Console()

_VERIFY_VERBOSE_OPTION = typer.Option(
    False,
    "--verbose",
    help="Show every console finding instead of the issues-first cap",
)
_VERIFY_MAX_FINDINGS_OPTION = typer.Option(
    DEFAULT_MAX_FINDINGS,
    "--max-findings",
    min=0,
    help="Maximum console issue groups per engine (0 shows summaries only)",
)
_VERIFY_GROUP_BY_OPTION = typer.Option(
    ConsoleGroupBy.ENGINE,
    "--group-by",
    help="Group console findings by engine, severity, category, file, or rule",
)
_VERIFY_PROFILE_OPTION = typer.Option(
    None,
    "--profile",
    help="Analysis cost profile: fast, standard, or deep (defaults to ici.profile)",
)
_VERIFY_NO_CACHE_OPTION = typer.Option(
    False,
    "--no-cache",
    help="Disable analysis cache reads and writes for this verification run",
)


def version_callback(value: bool):
    if value:
        print(f"ici {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
):
    if not version:
        ctx.ensure_object(dict)
        try:
            ctx.obj["config"] = load_config()
        except ConfigError as err:
            typer.echo(f"Configuration error: {err}", err=True)
            raise typer.Exit(code=2) from err


def _resolve_baseline_cli_paths(
    root: Path,
    *,
    baseline: str | None,
    write_baseline: str | None,
    fail_on_new: bool,
    report: bool,
) -> tuple[Path | None, Path | None]:
    """Validate baseline option relationships and root-contained paths."""

    if fail_on_new and baseline is None:
        typer.echo("Baseline error: --fail-on-new requires --baseline", err=True)
        raise typer.Exit(code=2)
    try:
        baseline_path = resolve_project_path(root, baseline) if baseline is not None else None
        baseline_output = (
            resolve_project_path(root, write_baseline) if write_baseline is not None else None
        )
    except ValueError as err:
        typer.echo(f"Baseline error: {err}", err=True)
        raise typer.Exit(code=2) from err
    if report and baseline_output == root / "verify_report.json":
        typer.echo(
            "Baseline error: --write-baseline must not overwrite --report output",
            err=True,
        )
        raise typer.Exit(code=2)
    return baseline_path, baseline_output


@app.command("verify")
def cmd_verify(
    ctx: typer.Context,
    report: bool = typer.Option(
        False, "--report", "-r", help="Save JSON report (verify_report.json)"
    ),
    html: str | None = typer.Option(
        None, "--html", help="Save standalone HTML report to specified path"
    ),
    open_browser: bool = typer.Option(
        False, "--open", help="Open generated HTML report in default browser"
    ),
    github_summary: bool = typer.Option(
        False, "--github-summary", help="Emit GitHub Actions step summary & annotations"
    ),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Publish HTML report to GitHub (gh-pages/hub) and post sticky PR comment",
    ),
    baseline: str | None = typer.Option(
        None, "--baseline", help="Compare findings with a project-contained ici.result/v3 report"
    ),
    fail_on_new: bool = typer.Option(
        False,
        "--fail-on-new",
        help="Fail when the baseline comparison finds new or regressed actionable findings",
    ),
    write_baseline: str | None = typer.Option(
        None,
        "--write-baseline",
        help="Write the current inventory as a project-contained v3 baseline",
    ),
    verbose: bool = _VERIFY_VERBOSE_OPTION,
    max_findings: int = _VERIFY_MAX_FINDINGS_OPTION,
    group_by: ConsoleGroupBy = _VERIFY_GROUP_BY_OPTION,
    profile: AnalysisProfile | None = _VERIFY_PROFILE_OPTION,
    no_cache: bool = _VERIFY_NO_CACHE_OPTION,
):
    """Runs the full verification engine suite and outputs a unified quality gate dashboard."""
    root = Path.cwd().resolve()
    console_options = ConsoleOptions(
        verbose=verbose,
        max_findings=max_findings,
        group_by=group_by,
    )
    orchestrator = VerifyOrchestrator(root, _effective_config(ctx))
    json_path = "verify_report.json" if report else None
    html_path = html if html else ("verify_report.html" if open_browser else None)
    if publish and not html_path:
        html_path = "verify_report.html"

    baseline_path, baseline_output = _resolve_baseline_cli_paths(
        root,
        baseline=baseline,
        write_baseline=write_baseline,
        fail_on_new=fail_on_new,
        report=report,
    )

    try:
        suite = orchestrator.run_all(
            report_json=json_path,
            report_html=html_path,
            github_summary=github_summary,
            publish=publish,
            baseline_path=baseline_path,
            fail_on_new=fail_on_new,
            write_baseline=baseline_output,
            console_options=console_options,
            profile=profile,
            use_cache=not no_cache,
        )
    except BaselineError as err:
        typer.echo(f"Baseline error: {err}", err=True)
        raise typer.Exit(code=2) from err

    if html_path and open_browser:
        _open_in_browser(html_path)

    _exit_for_safety_status(suite.suite_status)


@app.command("build")
def cmd_build(ctx: typer.Context):
    """Compiles and packages release artifacts and env loaders into vX.Y.Z/x86_64/."""
    engine = _create_engine(BuildEngine, _effective_config(ctx))
    res = redact_engine_result(engine.run())
    _print_engine_result(res)
    _exit_for_safety_status(res.status)


# --- Engine subcommands are generated from a single registry (keeps CLI
# boilerplate free of per-engine duplication; see dogfood dup gate). ---

_ENGINE_COMMANDS = [
    (
        "resource",
        "ResourceEngine",
        "resource_report.json",
        "Detects resource leaks and mutable defaults via AST.",
    ),
    (
        "security",
        "SecurityEngine",
        "security_report.json",
        "Detects hardcoded secrets and weak crypto via offline regex.",
    ),
    (
        "cognitive",
        "CognitiveEngine",
        "cognitive_report.json",
        "Measures cognitive complexity with nesting penalty.",
    ),
    (
        "cycle",
        "CycleEngine",
        "cycle_report.json",
        "Detects cyclic dependencies in Python imports and C++ includes.",
    ),
    (
        "line",
        "LineCountEngine",
        "line_report.json",
        "Analyzes code/comment/blank line distribution and verifies 500/1000 lines threshold.",
    ),
    (
        "lint",
        "LintEngine",
        "lint_report.json",
        "Runs Ruff/AST Python checks and g++ syntax diagnostics for C/C++ sources.",
    ),
    (
        "test",
        "TestEngine",
        "test_report.json",
        "Runs unit tests, measures branch/function coverage, and calculates TEM 5.0 score.",
    ),
    (
        "type",
        "TypeCheckEngine",
        "type_report.json",
        "Runs Mypy or the labeled AST fallback; C++ type checking is explicitly skipped.",
    ),
    (
        "complexity",
        "ComplexityEngine",
        "complexity_report.json",
        "Analyzes Cyclomatic Complexity and maximum block nesting depth per function.",
    ),
    (
        "sanitize",
        "SanitizeEngine",
        "sanitize_report.json",
        "Runs AddressSanitizer/UBSan for C++ and resource leak checks for Python.",
    ),
    (
        "dead",
        "DeadCodeEngine",
        "dead_report.json",
        "Detects unused functions, unreachable statements, and orphaned symbols.",
    ),
    (
        "dup",
        "DuplicateEngine",
        "dup_report.json",
        "Detects copy-pasted duplicate code blocks and calculates codebase duplication rate.",
    ),
    (
        "exception",
        "ExceptionSafetyEngine",
        "exception_report.json",
        "Detects exception swallowing (except: pass), lost tracebacks, and destructor throws.",
    ),
]


def _run_engine_command(
    engine_cls_name: str, report_filename: str, ctx: typer.Context, report: bool
):
    # Resolve via module attribute so tests can monkeypatch engine classes.
    engine_cls = getattr(sys.modules[__name__], engine_cls_name)
    config = _effective_config(ctx)
    engine = _create_engine(engine_cls, config)
    raw_result = engine.run()
    raw_result.support_matrix = evaluate_support_matrix(
        Path.cwd().resolve(),
        config,
        [raw_result],
        engine_names={raw_result.engine_name},
    )
    res = redact_engine_result(raw_result)
    _print_engine_result(res)
    if res.engine_name == "line":
        extra = res.extra
        print_line_distribution_chart(
            extra.get("code", 0),
            extra.get("comment", 0),
            extra.get("blank", 0),
            extra.get("total", 0),
        )
    if report:
        _save_single_report(report_filename, res)
    _exit_for_safety_status(res.status)


for _name, _cls, _filename, _doc in _ENGINE_COMMANDS:

    def _make_handler(_cls_name=_cls, _report_filename=_filename, _doc=_doc):
        def handler(
            ctx: typer.Context,
            report: bool = typer.Option(False, "--report", "-r", help="Save JSON report"),
        ):
            _run_engine_command(_cls_name, _report_filename, ctx, report)

        handler.__doc__ = _doc
        return handler

    app.command(_name)(_make_handler())


# Held at module level because a typer.Option() call in a list-typed default
# trips bugbear's B008; the scalar options above are not flagged.
_REPORT_DIR_OPTION = typer.Option(
    None,
    "--report-dir",
    help=(
        "Directory holding verify_report.html/json, optionally as label=path. "
        "Repeatable; each becomes one row of a single sticky comment and is "
        "published under its own gh-pages path. Use label=path for the "
        "repository root, whose directory name would otherwise be '.'. "
        "Overrides --html/--json."
    ),
)


def _collect_report_inputs(
    report_dirs: list[str] | None, html: str, json_report: str
) -> list[ReportInput]:
    """Build the publish set from either --report-dir (monorepo) or --html/--json."""
    if not report_dirs:
        json_path = Path(json_report)
        suite = load_suite_from_json(json_path) if json_path.exists() else None
        return [ReportInput("", Path(html), suite)]
    inputs: list[ReportInput] = []
    for raw in report_dirs:
        # "label=path" spells out the name explicitly, which the repository root
        # needs: its directory name is "." and would make a nonsense path segment.
        label, _, path_part = raw.partition("=")
        if not path_part:
            label, path_part = "", raw
        directory = Path(path_part)
        label = label or directory.name or directory.resolve().name
        json_path = directory / "verify_report.json"
        inputs.append(
            ReportInput(
                label=label,
                html_path=directory / "verify_report.html",
                suite=load_suite_from_json(json_path) if json_path.exists() else None,
            )
        )
    return inputs


@app.command("publish")
def cmd_publish(
    ctx: typer.Context,
    html: str = typer.Option("verify_report.html", "--html", help="Published HTML report path"),
    json_report: str = typer.Option(
        "verify_report.json", "--json", help="verify_report.json used to enrich the comment"
    ),
    report_dir: list[str] = _REPORT_DIR_OPTION,
):
    """Publishes existing HTML report(s) to gh-pages and updates the sticky PR comment."""
    reports = _collect_report_inputs(report_dir, html, json_report)
    result = ReportPublisher(project_name=None).publish_many(reports)
    print(f"[publish] {result.message}")
    if result.comment_url:
        print(f"[publish] PR comment: {result.comment_url}")
    # Publishing is this command's only job — a real upload failure must be a
    # visible CI failure, not a silent exit 0 (an intentional skip, e.g. no
    # GITHUB_ACTIONS environment, still exits 0 via success=True).
    if not result.success:
        raise typer.Exit(code=1)


@app.command("doctor")
def cmd_doctor(
    ctx: typer.Context,
    brief: bool = typer.Option(
        False, "--brief", help="Concise single-screen brief for closed-network survey"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Diagnoses toolchains, Python candidates, compilers, and shared paths."""
    data = collect_diagnostics(Path.cwd().resolve(), _effective_config(ctx))
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif brief:
        render_doctor_brief(data)
    else:
        render_doctor_table(data)


@app.command("env")
def cmd_env(
    csh: bool = typer.Option(False, "--csh", help="Generate tcsh / csh environment loader syntax"),
    sh: bool = typer.Option(
        False, "--sh", help="Generate bash / zsh / sh environment loader syntax"
    ),
):
    """Generates shell environment setup lines to paste into .cshrc or .bashrc."""
    local_bin = str(Path.home() / ".local/bin")
    if csh:
        print(f'setenv PATH "{local_bin}:$PATH"')
    else:
        print(f'export PATH="{local_bin}:$PATH"')


@app.command("cache")
def cmd_cache(
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Remove analysis cache entry files from the exact local cache directory",
    ),
):
    """Shows local cache inventory and the inputs that invalidate cached analysis."""

    cache = AnalysisCache()
    removed = cache.clear() if clear else 0
    inventory = cache.inventory()
    if clear:
        typer.echo(f"Removed {removed} cache file(s).")
    typer.echo(f"Cache directory: {inventory.root}")
    typer.echo(
        f"Entries: {inventory.entries} valid, {inventory.corrupt_entries} corrupt, "
        f"{inventory.bytes} bytes"
    )
    typer.echo(f"Key contract: {CACHE_KEY_VERSION}")
    typer.echo(
        "Invalidated by project root, source/build-config content, effective ici config, "
        "tool versions, engine implementation, build variant, or ici version."
    )


def _effective_config(ctx: typer.Context):
    """Return the effective policy loaded by the CLI callback."""

    obj = ctx.ensure_object(dict)
    config = obj.get("config")
    if isinstance(config, dict):
        return config
    return load_config(Path.cwd().resolve())


def _create_engine(engine_cls, config=None):
    """Construct an engine with the effective policy for the current project."""

    root = Path.cwd().resolve()
    return engine_cls(root, config if config is not None else load_config(root))


def _exit_for_safety_status(status: EngineStatus) -> None:
    code = exit_code_for_status(status)
    if code:
        raise typer.Exit(code=code)


def _save_single_report(filename: str, res: EngineResult) -> None:
    save_engine_json_report(res, Path(filename), project_root=Path.cwd().resolve())
    console.print(f"[dim]Report saved to: {filename}[/dim]")


def _print_engine_result(res: EngineResult) -> None:
    """Render one engine result with a status-aware icon and escaped summary."""
    style, icon = {
        EngineStatus.PASS: ("bold green", "✔"),
        EngineStatus.WARN: ("bold yellow", "⚠"),
        EngineStatus.FAIL: ("bold red", "✘"),
        EngineStatus.ERROR: ("bold red", "✘"),
        EngineStatus.SKIP: ("dim", "↷"),
    }[res.status]
    console.print(f"[{style}]{icon}[/] {escape(res.summary)}")


def _open_in_browser(html_path: str) -> None:
    path_obj = Path(html_path).resolve()
    for opener in ("wslview", "xdg-open", "open"):
        if shutil.which(opener):
            subprocess.run([opener, str(path_obj)], capture_output=True)
            return


def main():
    app()


if __name__ == "__main__":
    main()
