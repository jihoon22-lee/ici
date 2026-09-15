"""The shared plumbing every ``ici next`` command stands on.

Request → scope → plan plumbing lives here so the command modules stay
small enough to read: workspace discovery, selection, inventory, the
check-to-task bridge, the compile-input gate, tool location, the event
sink and the option spellings every command shares.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import typer

from ici.adapters.providers.base import ProviderPlan
from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.application.graph import TaskGraph, build_graph
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.application.request import (
    RunRequest,
    profile_for,
    requested_languages,
    scope_of,
    wanted,
)
from ici.application.selection import Planner, select_effective
from ici.application.verify import Analysis
from ici.config.composition import EffectiveConfig
from ici.config.discovery import discover, load
from ici.config.errors import NextConfigError
from ici.domain.enums import Profile, TaskState
from ici.domain.events import EventType, RunEvent
from ici.domain.eventstream import events_to_jsonl
from ici.domain.observation import Measurement, Observation
from ici.domain.workspace import AnalysisUnit, BuildUnit, Component, Workspace
from ici.languages.checks import CheckDefinition
from ici.languages.python.lines import LineRequest
from ici.languages.python.lines import count as count_lines
from ici.languages.registry import builtin as builtin_registry
from ici.workspace import build as build_workspace
from ici.workspace import compile_units, inventory
from ici.workspace.inventory import SourceInventory, SourceRole

__all__ = ["next_app"]

#: SPEC-04 section 3. Named rather than spelled out at each raise, because the
#: one that matters is the difference between 2 and everything else.
EXIT_CONFIG = 2

DEFAULT_RESULT = Path(".ici") / "next" / "result.json"
DEFAULT_PAGE = Path(".ici") / "next" / "result.html"

_COMPONENT_OPTION = typer.Option(None, "--component", help="Which component to act on; repeatable")
_PYTHON_OPTION = typer.Option(False, "--python", help="Select Python checks only")
_CPP_OPTION = typer.Option(False, "--cpp", help="Select C++ checks only")
_PROFILE_OPTION = typer.Option(
    None, "--profile", help="Cost profile for this run (does not rewrite the root's)"
)
_REQUIRE_FULL_OPTION = typer.Option(
    False,
    "--require-full",
    help="Fail unless the run covers the workspace's full required scope",
)
_RESULT_OPTION = typer.Option(DEFAULT_RESULT, "--result", "--output", help="The saved result")
_PAGE_OPTION = typer.Option(DEFAULT_PAGE, "--out", help="Where to write the page")
_NO_CACHE_OPTION = typer.Option(False, "--no-cache", help="Run without reusing stored observations")
_JSON_OPTION = typer.Option(
    False, "--json", help="Machine output on stdout; diagnostics stay on stderr"
)
_EVENTS_OPTION = typer.Option(None, "--events", help="Write the event stream here")
_CONFIG_OPTION = typer.Option(None, "--config", help="Read this config file")
_LOCAL_CONFIG_OPTION = typer.Option(
    None, "--local-config", help="Personal overlay, path-allowlisted"
)
_PREVIEW_OPTION = typer.Option(False, "--preview", help="Print the file instead of writing it")
_FORCE_OPTION = typer.Option(False, "--force", help="Overwrite an existing ici.toml")

next_app = typer.Typer(
    name="next",
    help="The ici-next path (in development). Does not replace any stable command.",
    add_completion=False,
)


def _workspace(
    cwd: Path, config_path: Path | None, local_path: Path | None
) -> tuple[Path, EffectiveConfig, Workspace]:
    """Read the configuration and the workspace model, or stop with exit 2.

    The run's root is the directory holding the discovered root file, not the
    directory the command was typed in — #210's promise that a subdirectory
    cwd does not quietly re-scope the run. A configuration that cannot be read
    — or whose model cannot be built — is not a failing verification: nothing
    ran. Every problem is printed, not just the first — the reader is going to
    edit the file either way, and one round trip per mistake is the thing
    ``NextConfigError`` exists to avoid.
    """

    try:
        found = discover(cwd, explicit=config_path)
        config = load(cwd, explicit=found.path, local=local_path)
        model = build_workspace(config)
    except NextConfigError as error:
        for problem in error.problems:
            typer.echo(f"config: {problem}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    except (OSError, ValueError) as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    return found.directory.resolve(), config, model


def _request(
    components: list[str] | None,
    python: bool,
    cpp: bool,
    profile: Profile | None,
    require_full: bool = False,
) -> RunRequest:
    """The CLI flags as one request. Both language flags are a union."""

    languages = tuple(language for language, asked in (("python", python), ("cpp", cpp)) if asked)
    return RunRequest(
        components=tuple(components or ()),
        languages=languages,
        profile=profile,
        require_full=require_full,
    )


def _profile(config: EffectiveConfig, request: RunRequest) -> Profile:
    """Resolve the run's cost profile, refusing a name the domain does not know."""

    try:
        return profile_for(config.profile.value if config.profile is not None else None, request)
    except ValueError as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error


def _scope(model: Workspace, request: RunRequest) -> Workspace:
    """The whole workspace, or the components the run was narrowed to."""

    try:
        return scope_of(model, request)
    except ValueError as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error


def _announce(
    root: Path, scope: Workspace, request: RunRequest, profile: Profile, json_mode: bool
) -> None:
    """Say which root and scope the run covers before anything happens.

    Under ``--json`` this is a diagnostic and goes to stderr — stdout belongs
    to the machine document alone.
    """

    echo = (lambda line: typer.echo(line, err=True)) if json_mode else typer.echo
    components = ",".join(item.id for item in scope.components)
    languages = ",".join(requested_languages(request, scope)) or "all"
    echo(f"root: {root}")
    echo(
        f"scope: components={components} languages={languages} "
        f"profile={profile.value}" + (" require-full" if request.require_full else "")
    )


def _compile_limitations(
    model: Workspace, scope: Workspace, stock: SourceInventory, root: Path
) -> tuple[list[str], list[str]]:
    """What the compile database can and cannot vouch for, per C++ component.

    #211: a database covering half a component's translation units is a
    partial input, and the run says which sources have no invocation rather
    than letting the gap pass as full C++ coverage. No database at all names
    the declared build unit that would produce one — or the config key that
    would declare it — instead of silently linting nothing.

    #212: the second return is the require-full reading of the same facts —
    missing generated inputs, partial coverage and a qmake SUBDIRS tree that
    never reaches the component are coverage *gaps*, not just notes.
    """

    limitations: list[str] = []
    gaps: list[str] = []
    for component in scope.components:
        if "cpp" not in component.languages:
            continue
        units = {unit.language: unit for unit in _units_of(scope, component)}
        cpp_unit = units.get("cpp")
        files = (
            _unit_files(stock, cpp_unit, _SOURCE_SUFFIXES["cpp"]) if cpp_unit is not None else ()
        )
        inputs = compile_units.load_compile_inputs(root, component, files, model.builds)
        if not inputs.database_path:
            hint = f" ({inputs.prepare[0]})" if inputs.prepare else ""
            limitations.append(f"{component.id}: no compilation database for its C++ scope{hint}")
            gaps.append(f"{component.id}: no compilation database captured its C++ scope")
            continue
        if inputs.missing:
            limitations.append(
                f"{component.id}: compile database covers "
                f"{len(inputs.covered)}/{len(inputs.expected)} translation units; "
                f"missing: {', '.join(inputs.missing)}"
            )
            gaps.append(
                f"{component.id}: {len(inputs.missing)} translation unit(s) have no "
                "compile invocation"
            )
        for diagnostic in inputs.diagnostics:
            if diagnostic.level == "error":
                limitations.append(f"{component.id}: {diagnostic.message}")
            if diagnostic.code in ("generated-input-missing", "qmake-target-missing"):
                gaps.append(f"{component.id}: {diagnostic.message}")
    return limitations, gaps


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
    request: RunRequest,
    profile: Profile,
) -> tuple[list[Plan], dict[str, Analysis], list[str], dict[str, Plan]]:
    """One plan per component, plus the internal analyses they register.

    The registry decides which checks a component's languages own (#208) —
    a Python component never sees a C++ tool requirement, and a C++ unit
    gets the pack's own checks rather than a Python-shaped empty answer.
    The request's language filter narrows which of those checks are asked
    for (#210): filtering the *checks* is what keeps the model's component
    a hybrid one even when only its Python half runs. Task ids carry the
    component — ``app.python.lint`` — so two components running one check
    stay two facts all the way into the stored result. A component nothing
    applies to is a limitation, not a vanished check.
    """

    registry = builtin_registry()
    plans: list[Plan] = []
    by_component: dict[str, Plan] = {}
    analyses: dict[str, Analysis] = {}
    limitations: list[str] = []
    for component in scope.components:
        units = {unit.language: unit for unit in _units_of(scope, component)}
        available = tuple(
            check for check in registry.checks_for(component.languages) if wanted(request, check)
        )
        if not available:
            if request.languages:
                detail = f"no checks in the requested languages ({', '.join(request.languages)})"
            else:
                detail = f"no checks apply to its languages ({', '.join(component.languages)})"
            limitations.append(f"{component.id}: {detail}")
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
            _tool_configs(component_root, root),
        )

        def in_scope(
            check: CheckDefinition,
            found: dict[str, tuple[str, ...]] = files_by_language,
        ) -> bool:
            return bool(found.get(check.language))

        try:
            plan = select_effective(
                effective,
                _locate,
                work,
                available=available,
                task_prefix=component.id,
                profile=profile,
                in_scope=in_scope,
            )
        except NothingSelected as error:
            limitations.append(str(error))
            continue
        plan = _gate_cpp_compile(
            plan, component, files_by_language.get("cpp", ()), scope.builds, root
        )
        plans.append(plan)
        by_component[component.id] = plan
        for planned in plan.checks:
            if planned.is_internal:
                analyses[planned.task_id] = _internal_analysis(
                    planned,
                    component,
                    files_by_language.get(planned.check.language, ()),
                    component_root,
                    root,
                    scope.builds,
                )
    if not plans:
        raise NothingSelected(
            "no component selected a check; a run that checks nothing cannot pass"
        )
    return plans, analyses, limitations, by_component


def _planner(
    component_root: Path,
    root: Path,
    component_id: str,
    unit: AnalysisUnit | None,
    files: tuple[str, ...],
    config_files: tuple[str, ...],
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
                config_files=config_files,
            )
        )

    return work


def _tool_configs(component_root: Path, root: Path) -> tuple[str, ...]:
    """The tool configuration files a check under this root would read.

    Ruff reads ``ruff.toml``/``.ruff.toml``/``pyproject.toml`` from the
    project root upward — so the search walks the component root to the
    workspace root and stops there. What a check reads is part of what it
    measured, which is why these are declared inputs (#209) rather than
    discovered quietly at run time.
    """

    found: list[str] = []
    base = component_root
    while True:
        for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
            candidate = base / name
            if candidate.is_file():
                try:
                    found.append(str(candidate.relative_to(component_root)))
                except ValueError:
                    found.append(str(candidate))
        if base == root or root not in base.parents:
            break
        base = base.parent
    return tuple(found)


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


def _internal_analysis(
    planned: PlannedCheck,
    component: Component,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
    builds: tuple[BuildUnit, ...],
) -> Analysis:
    """The in-process work behind a planned internal check.

    ``cpp.compile`` reads the compile-database service — the check's answer is
    the coverage the database proves, not a pass it grants. Everything else is
    the line counter, which every language's ``*.line`` check shares.
    """

    if planned.check.id == "cpp.compile":
        return _compile_coverage(root, component, files, builds, planned.task_id)
    return _line_counter(component_root, root, files, planned.task_id)


def _gate_cpp_compile(
    plan: Plan,
    component: Component,
    files: tuple[str, ...],
    builds: tuple[BuildUnit, ...],
    root: Path,
) -> Plan:
    """Block ``cpp.compile`` when nothing declares where its input lives.

    A C++ component with no compilation database has no compilable evidence —
    the check is marked blocked with the remedy (the declared build that would
    produce one, or the config key that would declare it) rather than run and
    report an empty coverage as a pass (#212).
    """

    checks: list[PlannedCheck] = []
    changed = False
    for planned in plan.checks:
        if planned.check.id != "cpp.compile" or planned.blocked:
            checks.append(planned)
            continue
        inputs = compile_units.load_compile_inputs(root, component, files, builds)
        if inputs.database_path:
            checks.append(planned)
            continue
        remedy = f" — {inputs.prepare[0]}" if inputs.prepare else ""
        checks.append(
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked=f"no compilation database for the component's C++ scope{remedy}",
            )
        )
        changed = True
    return Plan(checks=tuple(checks)) if changed else plan


def _compile_coverage(
    root: Path,
    component: Component,
    files: tuple[str, ...],
    builds: tuple[BuildUnit, ...],
    task_id: str,
) -> Analysis:
    """The ``cpp.compile`` check's own read: coverage as measured fact."""

    def run() -> Observation:
        inputs = compile_units.load_compile_inputs(root, component, files, builds)
        limitations: list[str] = []
        if inputs.missing:
            shown = ", ".join(inputs.missing[:8])
            more = f" (+{len(inputs.missing) - 8} more)" if len(inputs.missing) > 8 else ""
            limitations.append(
                f"no compile invocation for {len(inputs.missing)}/{len(inputs.expected)} "
                f"translation units: {shown}{more}"
            )
        limitations += [item.message for item in inputs.diagnostics]
        if inputs.targets:
            resolved = sum(1 for target in inputs.targets if target.project)
            limitations = (
                [
                    *limitations,
                    f"qmake targets resolved: {resolved}/{len(inputs.targets)}",
                ]
                if resolved < len(inputs.targets)
                else limitations
            )
        # A capture that names fewer TUs than the scope holds — or generated
        # inputs that are gone — is incomplete evidence, not a failed check
        # and not a passing one: FAILED state here reads as INCOMPLETE at the
        # gate, which is what "partial coverage is not a full C++ pass" means.
        incomplete_capture = inputs.missing or any(
            item.code == "generated-input-missing" for item in inputs.diagnostics
        )
        return Observation(
            task_id=task_id,
            provider="ici.compile",
            state=TaskState.FAILED if incomplete_capture else TaskState.SUCCEEDED,
            measurements=(
                Measurement(
                    name="units.covered",
                    value=len(inputs.covered),
                    unit="translation units",
                    numerator=len(inputs.covered),
                    denominator=len(inputs.expected),
                ),
                Measurement(
                    name="units.generated",
                    value=len(inputs.generated),
                    unit="translation units",
                ),
            ),
            limitations=tuple(limitations),
        )

    return run


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


def _version_of(executable: str) -> str:
    """One bounded ``--version`` probe — doctor's only process it may spawn.

    #210 item 4 allows a limited version probe and forbids everything else:
    no build, no install, no sourcing. Five seconds and the first line are
    the budget.
    """

    try:
        probe = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "version unknown"
    output = (probe.stdout or probe.stderr).strip()
    return output.splitlines()[0] if output else "version unknown"


class _EventSink:
    """The ``ici.next.event`` stream for one run, written to its own file.

    ``seq`` is monotonic per run and guarded because task completions arrive
    from pool threads. Nothing but events goes to the file — SPEC-04 section
    6 keeps logs out of the stream so a consumer parses every line.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        self._path = path
        self._run_id = run_id
        self._lock = threading.Lock()
        self._seq = 0
        self._events: list[RunEvent] = []

    def emit(
        self,
        event_type: EventType,
        *,
        task_id: str | None = None,
        component_id: str | None = None,
        message: str = "",
    ) -> None:
        with self._lock:
            seq = self._seq
            self._seq += 1
        self._events.append(
            RunEvent(
                run_id=self._run_id,
                seq=seq,
                event_type=event_type,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                task_id=task_id,
                component_id=component_id,
                message=message,
            )
        )

    def on_execution(self, execution) -> None:
        detail = f" — {execution.detail}" if execution.detail else ""
        self.emit(
            EventType.TASK_COMPLETED,
            task_id=execution.unit,
            message=f"{execution.state.value}{detail}",
        )

    def write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(events_to_jsonl(self._events), encoding="utf-8")


def _graph_of(plans: list[Plan]) -> TaskGraph:
    return build_graph(tuple(item for plan in plans for item in plan.checks))


def _describe(planned: PlannedCheck) -> str:
    """One plan line: what runs, or why it cannot."""

    if planned.blocked:
        return f"  {planned.task_id}: blocked — {planned.blocked}"
    if planned.is_internal:
        return f"  {planned.task_id}: {planned.check.title} (ici)"
    assert planned.task is not None
    marker = " [mutating]" if planned.task.task.mutating else ""
    return f"  {planned.task_id}: {' '.join(planned.task.task.argv)}{marker}"
