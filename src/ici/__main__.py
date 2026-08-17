"""Main Typer CLI Application Entrypoint for ici."""

import json
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from ici import __version__
from ici.core.models import EngineStatus
from ici.doctor import collect_diagnostics, render_doctor_brief, render_doctor_table
from ici.engines.build import BuildEngine
from ici.engines.complexity import ComplexityEngine
from ici.engines.cov_interface import CoverityInterface
from ici.engines.dead import DeadCodeEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.line import LineCountEngine
from ici.engines.lint import LintEngine
from ici.engines.sam_interface import SAMInterface
from ici.engines.sanitize import SanitizeEngine
from ici.engines.test import TestEngine
from ici.engines.type_check import TypeCheckEngine
from ici.engines.verify import VerifyOrchestrator
from ici.reporters.console import print_line_distribution_chart

app = typer.Typer(
    name="ici",
    help=f"Integrated CI Engine v{__version__} — Multi-Language CI/CD Verification & Build Tool",
    add_completion=False,
)
console = Console()


def version_callback(value: bool):
    if value:
        print(f"ici {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
):
    pass


@app.command("verify")
def cmd_verify(
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
):
    """Runs all 9 verification engines, Coverity/SAM interfaces, and outputs unified dashboard."""
    orchestrator = VerifyOrchestrator()
    json_path = "verify_report.json" if report else None
    html_path = html if html else ("verify_report.html" if open_browser else None)

    suite = orchestrator.run_all(
        report_json=json_path,
        report_html=html_path,
        github_summary=github_summary,
    )

    if html_path and open_browser:
        _open_in_browser(html_path)

    if suite.suite_status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("build")
def cmd_build():
    """Compiles and packages release artifacts and env loaders into vX.Y.Z/x86_64/."""
    engine = BuildEngine()
    res = engine.run()
    console.print(f"[bold green]✔[/bold green] {res.summary}")
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("line")
def cmd_line(report: bool = typer.Option(False, "--report", "-r", help="Save line report")):
    """Analyzes code/comment/blank line distribution and verifies 500/1000 lines threshold."""
    engine = LineCountEngine()
    res = engine.run()
    extra = res.extra
    print_line_distribution_chart(
        extra.get("code", 0), extra.get("comment", 0), extra.get("blank", 0), extra.get("total", 0)
    )
    if report:
        _save_single_report("line_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("lint")
def cmd_lint(report: bool = typer.Option(False, "--report", "-r", help="Save lint report")):
    """Runs Ruff, AST syntax check, and G++/Clang-Format style linting."""
    engine = LintEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("lint_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("test")
def cmd_test(report: bool = typer.Option(False, "--report", "-r", help="Save test report")):
    """Runs unit tests, measures branch/function coverage, and calculates TEM 5.0 score."""
    engine = TestEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("test_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("type")
def cmd_type(report: bool = typer.Option(False, "--report", "-r", help="Save type report")):
    """Runs static type checking (Mypy & strict C++ compiler flags)."""
    engine = TypeCheckEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("type_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("complexity")
def cmd_complexity(
    report: bool = typer.Option(False, "--report", "-r", help="Save complexity report"),
):
    """Analyzes Cyclomatic Complexity and maximum block nesting depth per function."""
    engine = ComplexityEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("complexity_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("sanitize")
def cmd_sanitize(report: bool = typer.Option(False, "--report", "-r", help="Save sanitize report")):
    """Runs AddressSanitizer/UBSan for C++ and resource leak checks for Python."""
    engine = SanitizeEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("sanitize_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("dead")
def cmd_dead(report: bool = typer.Option(False, "--report", "-r", help="Save dead code report")):
    """Detects unused functions, unreachable statements, and orphaned symbols."""
    engine = DeadCodeEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("dead_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("dup")
def cmd_dup(report: bool = typer.Option(False, "--report", "-r", help="Save duplication report")):
    """Detects copy-pasted duplicate code blocks and calculates codebase duplication rate."""
    engine = DuplicateEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("dup_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("exception")
def cmd_exception(
    report: bool = typer.Option(False, "--report", "-r", help="Save exception safety report"),
):
    """Detects exception swallowing (except: pass), lost tracebacks, and destructor throws."""
    engine = ExceptionSafetyEngine()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("exception_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("cov")
def cmd_cov(report: bool = typer.Option(False, "--report", "-r", help="Save Coverity report")):
    """Runs Coverity static defect analysis interface / local rule scanner."""
    engine = CoverityInterface()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'yellow'}]{res.summary}[/]")
    if report:
        _save_single_report("cov_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("sam")
def cmd_sam(report: bool = typer.Option(False, "--report", "-r", help="Save SAM report")):
    """Runs SAM security analysis module interface / local scanner with 100-point score."""
    engine = SAMInterface()
    res = engine.run()
    console.print(f"[{'green' if res.status == EngineStatus.PASS else 'red'}]{res.summary}[/]")
    if report:
        _save_single_report("sam_report.json", res)
    if res.status == EngineStatus.FAIL:
        raise typer.Exit(code=1)


@app.command("doctor")
def cmd_doctor(
    brief: bool = typer.Option(
        False, "--brief", help="Concise single-screen brief for closed-network survey"
    ),
    as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Diagnoses toolchains, Python candidates, compilers, and shared paths."""
    data = collect_diagnostics()
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


def _save_single_report(filename: str, res) -> None:
    data = {
        "engine": res.engine_name,
        "status": res.status.value,
        "summary": res.summary,
        "score": res.score,
        "duration": res.duration,
        "targets": [
            {
                "file_path": t.file_path,
                "start_line": t.start_line,
                "end_line": t.end_line,
                "target_name": t.target_name,
                "status": t.status.value,
                "message": t.message,
            }
            for t in res.targets
        ],
    }
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    console.print(f"[dim]Report saved to: {filename}[/dim]")


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
