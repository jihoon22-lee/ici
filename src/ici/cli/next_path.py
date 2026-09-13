"""``ici next`` — the new path, reachable only by asking for it.

#206's last acceptance line is *"완성 경로가 기존 stable 기능을 자동 교체하거나
릴리스를 만들지 않는다"*, and that is why this is a separate namespace rather
than a flag on ``verify``. A flag can be defaulted on by accident; a
sub-command cannot be reached without typing it. Nothing here is wired into the
stable commands, and removing this module would leave them untouched.

Three commands, and the split between them is the one #206 item 5 asks for:

- ``plan``   says what a run would do, and runs nothing.
- ``verify`` runs it and saves the result.
- ``report`` renders a saved result, and analyses nothing.

The exit codes are SPEC-04 section 3's, and the one that is easy to get wrong
is 2: a configuration that cannot be read is not a failing verification. The
run never started, so reporting 1 would claim a verdict nobody reached.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import typer

from ici import __version__
from ici.adapters.providers.base import Provider, ProviderPlan
from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.application.plan import NothingSelected, Plan
from ici.application.report import assemble, digest_of
from ici.application.selection import select_effective
from ici.application.verify import verify as run_verification
from ici.config.composition import EffectiveComponent
from ici.config.discovery import load
from ici.config.errors import NextConfigError
from ici.domain.enums import ScopeKind
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.workspace import SourceSnapshot
from ici.languages.python.checks import CheckDefinition
from ici.languages.python.lines import LineRequest
from ici.languages.python.lines import count as count_lines
from ici.reporting.offline_html import render

__all__ = ["next_app"]

#: SPEC-04 section 3. Named rather than spelled out at each raise, because the
#: one that matters is the difference between 2 and everything else.
EXIT_CONFIG = 2

DEFAULT_RESULT = Path(".ici") / "next" / "result.json"
DEFAULT_PAGE = Path(".ici") / "next" / "result.html"

_COMPONENT_OPTION = typer.Option(None, "--component", help="Which component to act on")
_RESULT_OPTION = typer.Option(DEFAULT_RESULT, "--result", help="The saved result")
_PAGE_OPTION = typer.Option(DEFAULT_PAGE, "--out", help="Where to write the page")

next_app = typer.Typer(
    name="next",
    help="The ici-next path (in development). Does not replace any stable command.",
    add_completion=False,
)


def _component(root: Path, component_id: str | None) -> tuple[EffectiveComponent, str]:
    """Read the configuration, or stop with exit 2.

    A configuration that cannot be read is not a failing verification: nothing
    ran. Every problem is printed, not just the first — the reader is going to
    edit the file either way, and one round trip per mistake is the thing
    ``NextConfigError`` exists to avoid.
    """

    try:
        config = load(root)
    except NextConfigError as error:
        for problem in error.problems:
            typer.echo(f"config: {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    except (OSError, ValueError) as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error

    if component_id is not None:
        chosen = config.component(component_id)
        if chosen is None:
            known = ", ".join(item.id for item in config.components) or "none"
            typer.echo(f"config: no component {component_id!r}; declared: {known}", err=True)
            raise typer.Exit(EXIT_CONFIG)
    elif len(config.components) == 1:
        chosen = config.components[0]
    else:
        names = ", ".join(item.id for item in config.components) or "none"
        typer.echo(f"config: name a component with --component; declared: {names}", err=True)
        raise typer.Exit(EXIT_CONFIG)
    return chosen, config.policy_digest


def _build_plan(component: EffectiveComponent, root: Path) -> Plan:
    component_root = Path(component.root.value)
    if not component_root.is_absolute():
        component_root = (root / component_root).resolve()

    def work(check: CheckDefinition, executable: str) -> ProviderPlan:
        return RuffProvider().plan(
            RuffRequest(
                executable=executable,
                project_root=component_root,
                targets=tuple(component.sources) or (".",),
                task_id=check.id,
                component_id=component.id,
            )
        )

    try:
        return select_effective(component, _locate, work)
    except NothingSelected as error:
        # 0개 선택은 설정 오류다. A run that checks nothing cannot pass.
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error


def _locate(tool: str) -> str | None:
    """Where a tool is, asked once, here.

    The bundle's own copy first: a lint result that depends on what else is
    installed on the machine is not reproducible (#204 item 7).
    """

    beside = Path(__file__).resolve().parent.parent.parent.parent / "bin" / tool
    if beside.is_file() and os.access(beside, os.X_OK):
        return str(beside)
    return shutil.which(tool)


@next_app.command("plan")
def cmd_plan(
    component: str = _COMPONENT_OPTION,
) -> None:
    """Say what a run would do. Runs nothing, builds nothing, installs nothing."""

    root = Path.cwd().resolve()
    chosen, _ = _component(root, component)
    plan = _build_plan(chosen, root)
    typer.echo(f"{chosen.id}: {len(plan.checks)} check(s)")
    typer.echo(str(plan))


@next_app.command("verify")
def cmd_verify(
    component: str = _COMPONENT_OPTION,
    result: Path = _RESULT_OPTION,
) -> None:
    """Run the selected checks and save the result."""

    root = Path.cwd().resolve()
    chosen, policy = _component(root, component)
    plan = _build_plan(chosen, root)
    component_root = Path(chosen.root.value)
    if not component_root.is_absolute():
        component_root = (root / component_root).resolve()
    sources = tuple(sorted(component_root.rglob("*.py")))

    providers: dict[str, Provider] = {"ruff": RuffProvider()}
    verification = run_verification(
        plan,
        providers=providers,
        analyses={
            "python.line": lambda: count_lines(
                LineRequest(project_root=component_root, files=sources, task_id="python.line")
            )
        }
        if sources
        else {},
    )

    stored = assemble(
        verification,
        run_id=uuid.uuid4().hex,
        ici_version=__version__,
        source=SourceSnapshot(digest=digest_of("|".join(str(p) for p in sources))),
        policy_digest=policy,
        toolchain_digest=digest_of("|".join(sorted(_tools(plan)))),
        component_ids=(chosen.id,),
        languages=tuple(chosen.languages.value),
        scope=ScopeKind.PARTIAL,
    )
    target = root / result if not result.is_absolute() else result
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(run_result_to_dict(stored)), encoding="utf-8")

    typer.echo(f"{stored.gate.selected.value}: {len(stored.findings)} finding(s)")
    for reason in stored.gate.reasons:
        typer.echo(f"  {reason}")
    typer.echo(f"saved {target}")
    raise typer.Exit(stored.gate.exit_code)


@next_app.command("report")
def cmd_report(
    result: Path = _RESULT_OPTION,
    page: Path = _PAGE_OPTION,
) -> None:
    """Render a saved result. Analyses nothing and runs no tool."""

    root = Path.cwd().resolve()
    source = root / result if not result.is_absolute() else result
    if not source.is_file():
        typer.echo(f"report: no result at {source}; run `ici next verify` first", err=True)
        raise typer.Exit(EXIT_CONFIG)
    try:
        stored = loads(source.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as error:
        typer.echo(f"report: {source} is not a result this version can read: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error

    target = root / page if not page.is_absolute() else page
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(stored), encoding="utf-8")
    typer.echo(f"wrote {target}")


def _tools(plan: Plan) -> set[str]:
    return {
        item.task.task.argv[0] for item in plan.checks if item.task is not None and item.will_run
    }
