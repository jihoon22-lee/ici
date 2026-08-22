"""Main Typer CLI Application Entrypoint for ici."""

import json
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from ici import __version__
from ici.config import ConfigError, load_config
from ici.core.models import EngineResult, EngineStatus, exit_code_for_status
from ici.doctor import collect_diagnostics, render_doctor_brief, render_doctor_table
from ici.engines.build import BuildEngine
from ici.engines.build_definition import BuildDefinitionEngine
from ici.engines.cmake_lint import CMakeLintEngine
from ici.engines.compile_db import CompileDbEngine
from ici.engines.complexity import ComplexityEngine
from ici.engines.dead import DeadCodeEngine
from ici.engines.dup import DuplicateEngine
from ici.engines.exception import ExceptionSafetyEngine
from ici.engines.file_hygiene import FileHygieneEngine
from ici.engines.line import LineCountEngine
from ici.engines.lint import LintEngine
from ici.engines.pyproject_lint import PyProjectLintEngine
from ici.engines.python_compat import PythonCompatEngine
from ici.engines.sanitize import SanitizeEngine
from ici.engines.static_hygiene import StaticHygieneEngine
from ici.engines.test import TestEngine
from ici.engines.toolchain import ToolchainEngine
from ici.engines.type_check import TypeCheckEngine
from ici.engines.verify import VerifyOrchestrator
from ici.reporters.console import print_line_distribution_chart
from ici.reporters.json_rep import save_engine_json_report

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
):
    """Runs all 9 verification engines and outputs unified quality gate dashboard."""
    root = Path.cwd().resolve()
    orchestrator = VerifyOrchestrator(root, _effective_config(ctx))
    json_path = "verify_report.json" if report else None
    html_path = html if html else ("verify_report.html" if open_browser else None)
    if publish and not html_path:
        html_path = "verify_report.html"

    suite = orchestrator.run_all(
        report_json=json_path,
        report_html=html_path,
        github_summary=github_summary,
        publish=publish,
    )

    if html_path and open_browser:
        _open_in_browser(html_path)

    _exit_for_safety_status(suite.suite_status)


@app.command("build")
def cmd_build(ctx: typer.Context):
    """Compiles and packages release artifacts and env loaders into vX.Y.Z/x86_64/."""
    engine = _create_engine(BuildEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    _exit_for_safety_status(res.status)


@app.command("line")
def cmd_line(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save line report"),
):
    """Analyzes code/comment/blank line distribution and verifies 500/1000 lines threshold."""
    engine = _create_engine(LineCountEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    extra = res.extra
    print_line_distribution_chart(
        extra.get("code", 0), extra.get("comment", 0), extra.get("blank", 0), extra.get("total", 0)
    )
    if report:
        _save_single_report("line_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("compile-db")
def cmd_compile_db(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save compile-db report"),
):
    """Validates compile_commands.json coverage and flag policy."""
    engine = _create_engine(CompileDbEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("compile_db_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("static-hygiene")
def cmd_static_hygiene(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save static-hygiene report"),
):
    """Detects missing header guards, include cycles, and dangerous patterns."""
    engine = _create_engine(StaticHygieneEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("static_hygiene_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("build-definition")
def cmd_build_definition(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save build-definition report"),
):
    """Configures and builds via the project's declared build system (shadow dir)."""
    engine = _create_engine(BuildDefinitionEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("build_definition_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("cmake-lint")
def cmd_cmake_lint(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save cmake-lint report"),
):
    """Validates CMakeLists.txt without executing cmake."""
    engine = _create_engine(CMakeLintEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("cmake_lint_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("pyproject-lint")
def cmd_pyproject_lint(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save pyproject-lint report"),
):
    """Validates pyproject.toml [project] metadata offline."""
    engine = _create_engine(PyProjectLintEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("pyproject_lint_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("toolchain")
def cmd_toolchain(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save toolchain report"),
):
    """Probes CI tools and enforces required-tool policy."""
    engine = _create_engine(ToolchainEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("toolchain_report.json", res)


@app.command("python-compat")
def cmd_python_compat(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save python-compat report"),
):
    """Byte-compiles sources under each configured target interpreter."""
    engine = _create_engine(PythonCompatEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("python_compat_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("file-hygiene")
def cmd_file_hygiene(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save file-hygiene report"),
):
    """Detects exec bits, CRLF/BOM, pycache artifacts, and broken shell syntax."""
    engine = _create_engine(FileHygieneEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("file_hygiene_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("lint")
def cmd_lint(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save lint report"),
):
    """Runs Ruff/AST Python checks and g++ syntax diagnostics for C/C++ sources."""
    engine = _create_engine(LintEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("lint_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("test")
def cmd_test(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save test report"),
):
    """Runs unit tests, measures branch/function coverage, and calculates TEM 5.0 score."""
    engine = _create_engine(TestEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("test_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("type")
def cmd_type(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save type report"),
):
    """Runs Mypy or the labeled AST fallback; C++ type checking is explicitly skipped."""
    engine = _create_engine(TypeCheckEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("type_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("complexity")
def cmd_complexity(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save complexity report"),
):
    """Analyzes Cyclomatic Complexity and maximum block nesting depth per function."""
    engine = _create_engine(ComplexityEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("complexity_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("sanitize")
def cmd_sanitize(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save sanitize report"),
):
    """Runs AddressSanitizer/UBSan for C++ and resource leak checks for Python."""
    engine = _create_engine(SanitizeEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("sanitize_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("dead")
def cmd_dead(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save dead code report"),
):
    """Detects unused functions, unreachable statements, and orphaned symbols."""
    engine = _create_engine(DeadCodeEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("dead_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("dup")
def cmd_dup(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save duplication report"),
):
    """Detects copy-pasted duplicate code blocks and calculates codebase duplication rate."""
    engine = _create_engine(DuplicateEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("dup_report.json", res)
    _exit_for_safety_status(res.status)


@app.command("exception")
def cmd_exception(
    ctx: typer.Context,
    report: bool = typer.Option(False, "--report", "-r", help="Save exception safety report"),
):
    """Detects exception swallowing (except: pass), lost tracebacks, and destructor throws."""
    engine = _create_engine(ExceptionSafetyEngine, _effective_config(ctx))
    res = engine.run()
    _print_engine_result(res)
    if report:
        _save_single_report("exception_report.json", res)
    _exit_for_safety_status(res.status)


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
    save_engine_json_report(res, Path(filename))
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
