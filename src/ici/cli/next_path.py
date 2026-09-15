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

Since WP09 PR C the commands run on the workspace model rather than a bare
component: ``load`` composes the configuration, ``workspace.build`` turns it
into components, units and build units, and ``verify``'s stored identity is the
real inventory — content digests, generated and external roles, VCS state —
not a hash of path names. ``--component`` narrows the run to a subset and the
result says so (``PARTIAL`` with the omitted components named); without it a
run covers the whole workspace.

The exit codes are SPEC-04 section 3's, and the one that is easy to get wrong
is 2: a configuration that cannot be read is not a failing verification. The
run never started, so reporting 1 would claim a verdict nobody reached.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath

import typer

from ici import __version__
from ici.adapters.providers.base import Provider, ProviderPlan
from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.application.plan import NothingSelected, Plan
from ici.application.report import assemble, digest_of
from ici.application.selection import Planner, select_effective
from ici.application.verify import Analysis
from ici.application.verify import verify as run_verification
from ici.config.composition import EffectiveConfig
from ici.config.discovery import load
from ici.config.errors import NextConfigError
from ici.domain.enums import GateVerdict, ScopeKind
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.workspace import AnalysisUnit, Component, Workspace
from ici.languages.checks import CheckDefinition
from ici.languages.python.lines import LineRequest
from ici.languages.python.lines import count as count_lines
from ici.languages.registry import builtin as builtin_registry
from ici.reporting.offline_html import render
from ici.workspace import build as build_workspace
from ici.workspace import inventory
from ici.workspace.inventory import SourceInventory, SourceRole
from ici.workspace.vcs import status as vcs_status

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


def _workspace(root: Path) -> tuple[EffectiveConfig, Workspace]:
    """Read the configuration and the workspace model, or stop with exit 2.

    A configuration that cannot be read — or whose model cannot be built — is
    not a failing verification: nothing ran. Every problem is printed, not
    just the first — the reader is going to edit the file either way, and one
    round trip per mistake is the thing ``NextConfigError`` exists to avoid.
    """

    try:
        config = load(root)
        model = build_workspace(config)
    except NextConfigError as error:
        for problem in error.problems:
            typer.echo(f"config: {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    except (OSError, ValueError) as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    return config, model


def _scope(model: Workspace, component_id: str | None) -> Workspace:
    """The whole workspace, or the one component the run was narrowed to."""

    if component_id is None:
        return model
    try:
        return model.scoped((component_id,))
    except ValueError:
        known = ", ".join(item.id for item in model.components) or "none"
        typer.echo(f"config: no component {component_id!r}; declared: {known}", err=True)
        raise typer.Exit(EXIT_CONFIG) from None


def _unit_files(
    stock: SourceInventory, unit: AnalysisUnit, suffixes: tuple[str, ...]
) -> tuple[str, ...]:
    """The workspace-relative files a unit's checks may read.

    Vendor files count — they are checked-in inputs the check would have seen.
    Generated files do not: a build output is not a source the run read.
    """

    return tuple(
        item.path
        for item in stock.files
        if unit.id in item.units
        and item.role is not SourceRole.GENERATED
        and item.path.endswith(suffixes)
    )


def _component_root(root: Path, component: Component) -> Path:
    return (root / PurePosixPath(component.root)).resolve()


#: Which file suffixes count as a language's sources when a unit's declared
#: globs resolve through the inventory. A language with no entry has no
#: checks that could read it, so nothing asks for its files.
_SOURCE_SUFFIXES = {
    "python": (".py",),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"),
}


def _plans(
    scope: Workspace,
    config: EffectiveConfig,
    stock: SourceInventory,
    root: Path,
) -> tuple[list[Plan], dict[str, Analysis], list[str]]:
    """One plan per component, plus the internal analyses they register.

    The registry decides which checks a component's languages own (#208) —
    a Python component never sees a C++ tool requirement, and a C++ unit
    gets the pack's own checks rather than a Python-shaped empty answer.
    Task ids carry the component — ``app.python.lint`` — so two components
    running one check stay two facts all the way into the stored result.
    A component nothing applies to is a limitation, not a vanished check:
    its languages having no checks is something the run should say.
    """

    registry = builtin_registry()
    plans: list[Plan] = []
    analyses: dict[str, Analysis] = {}
    limitations: list[str] = []
    for component in scope.components:
        units = {unit.language: unit for unit in _units_of(scope, component)}
        available = registry.checks_for(component.languages)
        if not available:
            limitations.append(
                f"{component.id}: no checks apply to its languages "
                f"({', '.join(component.languages)})"
            )
            continue
        effective = config.component(component.id)
        assert effective is not None
        component_root = _component_root(root, component)
        files_by_language = {
            language: _unit_files(stock, units[language], _SOURCE_SUFFIXES[language])
            for language in component.languages
            if language in units and language in _SOURCE_SUFFIXES
        }
        work = _planner(
            component_root,
            root,
            component.id,
            units.get("python"),
            files_by_language.get("python", ()),
        )

        try:
            plan = select_effective(
                effective,
                _locate,
                work,
                available=available,
                task_prefix=component.id,
                in_scope=lambda check, found=files_by_language: bool(found.get(check.language)),
            )
        except NothingSelected as error:
            limitations.append(str(error))
            continue
        plans.append(plan)
        for planned in plan.checks:
            if planned.is_internal:
                analyses[planned.task_id] = _line_counter(
                    component_root,
                    root,
                    files_by_language[planned.check.language],
                    planned.task_id,
                )
    if not plans:
        raise NothingSelected(
            "no component selected a check; a run that checks nothing cannot pass"
        )
    return plans, analyses, limitations


def _planner(
    component_root: Path,
    root: Path,
    component_id: str,
    unit: AnalysisUnit | None,
    files: tuple[str, ...],
) -> Planner:
    """Bind one component's context into the check-to-task callback."""

    def work(check: CheckDefinition, executable: str, task_id: str) -> ProviderPlan:
        assert unit is not None
        return RuffProvider().plan(
            RuffRequest(
                executable=executable,
                project_root=component_root,
                targets=_targets(component_root, root, files),
                task_id=task_id,
                component_id=component_id,
                analysis_unit_id=unit.id,
            )
        )

    return work


def _targets(component_root: Path, root: Path, files: tuple[str, ...]) -> tuple[str, ...]:
    """The unit's real files as ruff targets, spelled relative to its root.

    The declared globs resolve through the inventory into actual files — a
    glob pattern is not a valid argv entry, and the old path's ``.`` linted
    more than the component claimed.
    """

    base = str(component_root)
    targets: list[str] = []
    for item in files:
        absolute = str((root / item).resolve())
        if absolute.startswith(base + os.sep):
            targets.append(absolute[len(base) + 1 :])
        else:
            targets.append(absolute)
    return tuple(targets)


def _line_counter(
    component_root: Path, root: Path, files: tuple[str, ...], task_id: str
) -> Analysis:
    resolved = tuple(root / item for item in files)
    return lambda: count_lines(
        LineRequest(project_root=component_root, files=resolved, task_id=task_id)
    )


def _units_of(scope: Workspace, component: Component) -> tuple[AnalysisUnit, ...]:
    return tuple(unit for unit in scope.analysis_units if unit.component_id == component.id)


def _drift_summary(drift: inventory.InventoryDiff) -> str:
    parts = [
        f"{len(items)} {name}"
        for name, items in (
            ("added", drift.added),
            ("removed", drift.removed),
            ("changed", drift.changed),
        )
        if items
    ]
    return ", ".join(parts) or "inputs changed"


#: Where a bundle keeps the analyzers it shipped, relative to its root.
BUNDLED_TOOLS = Path("tools") / "python-static"


def _locate(tool: str) -> str | None:
    """Where a tool is, asked once, here.

    **Running from a bundle, it is the bundle's copy or nothing.** #204 item 7:
    an analyzer taken from PATH makes the result depend on what else is
    installed on the machine, which is the property an offline release exists
    to remove. Falling back to PATH here would mean a bundle missing its ruff
    quietly linted with whatever the host had, and the report would not say so.

    Running from a source checkout there is no bundle to prefer, so PATH is the
    honest answer and the developer gets the tool they installed.

    The bundle is found through ``ICI_BUNDLE_ROOT``, which its launcher exports.
    The first version walked ``__file__`` upwards to guess, and guessed wrong:
    it looked in ``bin/`` while the build puts analyzers in
    ``tools/python-static/``, so inside a real bundle it would have found
    nothing and fallen through to the host.
    """

    root = os.environ.get("ICI_BUNDLE_ROOT")
    if root:
        shipped = Path(root) / BUNDLED_TOOLS / tool
        return str(shipped) if _runnable(shipped) else None
    return shutil.which(tool)


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


@next_app.command("plan")
def cmd_plan(
    component: str = _COMPONENT_OPTION,
) -> None:
    """Say what a run would do. Runs nothing, builds nothing, installs nothing."""

    root = Path.cwd().resolve()
    config, model = _workspace(root)
    scope = _scope(model, component)
    stock = inventory.take(scope, root=root)
    try:
        plans, _, limitations = _plans(scope, config, stock, root)
    except NothingSelected as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    typer.echo(f"{scope.id}: {sum(len(plan.checks) for plan in plans)} check(s)")
    for plan in plans:
        typer.echo(str(plan))
    for limitation in limitations:
        typer.echo(f"  note: {limitation}")


@next_app.command("verify")
def cmd_verify(
    component: str = _COMPONENT_OPTION,
    result: Path = _RESULT_OPTION,
) -> None:
    """Run the selected checks and save the result."""

    root = Path.cwd().resolve()
    config, model = _workspace(root)
    scope = _scope(model, component)
    before = inventory.take(scope, root=root)
    state = vcs_status(root)
    snapshot = before.snapshot(
        commit=state.commit if state is not None else None,
        dirty=state is None or not state.clean,
    )
    try:
        plans, analyses, limitations = _plans(scope, config, before, root)
    except NothingSelected as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error

    providers: dict[str, Provider] = {"ruff": RuffProvider()}
    verification = run_verification(plans, providers=providers, analyses=analyses)

    drift = inventory.diff(before, inventory.take(scope, root=root))
    if not drift.stable:
        limitations = [*limitations, f"inputs changed while running: {_drift_summary(drift)}"]

    # FULL is the domain's claim that the workspace's required scope was
    # covered *and* finished — an incomplete run over everything is still
    # PARTIAL, because the model refuses a FULL it cannot stand behind.
    if config.scope_kind is ScopeKind.STANDALONE:
        scope_kind = ScopeKind.STANDALONE
    elif component is not None or verification.gate.selected is GateVerdict.INCOMPLETE:
        scope_kind = ScopeKind.PARTIAL
    else:
        scope_kind = ScopeKind.FULL

    stored = assemble(
        verification,
        run_id=uuid.uuid4().hex,
        ici_version=__version__,
        source=snapshot,
        policy_digest=config.policy_digest,
        toolchain_digest=digest_of("|".join(sorted(_tools(plans)))),
        component_ids=tuple(item.id for item in scope.components),
        languages=tuple(
            sorted({language for item in scope.components for language in item.languages})
        ),
        scope=scope_kind,
        required_components=model.required_component_ids,
        omitted_components=tuple(
            item.id for item in model.components if item.id not in {c.id for c in scope.components}
        ),
        limitations=tuple(limitations),
    )
    target = root / result if not result.is_absolute() else result
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(run_result_to_dict(stored)), encoding="utf-8")

    typer.echo(f"{stored.gate.selected.value}: {len(stored.findings)} finding(s)")
    for reason in stored.gate.reasons:
        typer.echo(f"  {reason}")
    for limitation in limitations:
        typer.echo(f"  note: {limitation}")
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


def _tools(plans: list[Plan]) -> set[str]:
    return {
        item.task.task.argv[0]
        for plan in plans
        for item in plan.checks
        if item.task is not None and item.will_run
    }
