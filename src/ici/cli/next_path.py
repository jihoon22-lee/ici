"""``ici next`` — the new path, reachable only by asking for it.

#206's last acceptance line is *"완성 경로가 기존 stable 기능을 자동 교체하거나
릴리스를 만들지 않는다"*, and that is why this is a separate namespace rather
than a flag on ``verify``. A flag can be defaulted on by accident; a
sub-command cannot be reached without typing it. Nothing here is wired into the
stable commands, and removing this module would leave them untouched.

The command set is SPEC-01 section 5's, and each one's side-effect budget is
the point of it:

- ``init``    writes a root ``ici.toml``. No install, no source, no build.
- ``doctor``  says what the scope's checks need and where they stand. It may
              ask a found tool for its version; it never builds or installs.
- ``plan``    says what a run would do — selection, dependency edges, shared
              executions, mutating steps — and runs nothing.
- ``verify``  runs it and saves the result.
- ``report``  renders a saved result, and analyses nothing.

Selection is a request, not a re-read of the model (#210): ``--component`` is
repeatable and intersects with the ``--python``/``--cpp`` union, ``--profile``
overrides the root's declared cost profile without erasing it, and
``--require-full`` demotes the final gate to INCOMPLETE when the request left
required coverage out. Wherever the commands are started from, the workspace
root is the directory holding the discovered root file — a subdirectory cwd
changes where you stand, not what the run covers, and the chosen root and
scope are printed before anything runs.

Machine output and human output never share a stream: ``--json`` puts the
result document on stdout and every diagnostic on stderr, and ``--events``
writes the ``ici.next.event`` JSONL stream to its own file.

The exit codes are SPEC-04 section 3's, and the one that is easy to get wrong
is 2: a configuration that cannot be read is not a failing verification. The
run never started, so reporting 1 would claim a verdict nobody reached.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import typer

from ici import __version__
from ici.adapters.providers.base import Provider, ProviderPlan
from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.application.graph import TaskGraph, WorkUnit, build_graph
from ici.application.identity import task_identity
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.application.report import assemble, digest_of
from ici.application.request import (
    RunRequest,
    apply_require_full,
    profile_for,
    requested_languages,
    scope_of,
    uncovered_scope,
    wanted,
)
from ici.application.selection import Planner, select_effective
from ici.application.verify import Analysis
from ici.application.verify import verify as run_verification
from ici.config.composition import EffectiveConfig
from ici.config.discovery import discover, load
from ici.config.errors import NextConfigError
from ici.domain.enums import GateVerdict, Profile, ScopeKind
from ici.domain.events import EventType, RunEvent
from ici.domain.eventstream import events_to_jsonl
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.workspace import AnalysisUnit, Component, Workspace
from ici.execution.cache import ObservationCache
from ici.execution.manifest import digest_of as file_digest
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

        try:
            plan = select_effective(
                effective,
                _locate,
                work,
                available=available,
                task_prefix=component.id,
                profile=profile,
                in_scope=lambda check, found=files_by_language: bool(found.get(check.language)),
            )
        except NothingSelected as error:
            limitations.append(str(error))
            continue
        plans.append(plan)
        by_component[component.id] = plan
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


@next_app.command("init")
def cmd_init(
    python: bool = _PYTHON_OPTION,
    cpp: bool = _CPP_OPTION,
    preview: bool = _PREVIEW_OPTION,
    force: bool = _FORCE_OPTION,
) -> None:
    """Write a root ``ici.toml`` for this directory. Installs nothing.

    Candidates are read off the tree — a directory holding sources of a known
    language becomes a component — and that is the whole of it: no tool is
    probed, nothing is built, and an existing ``ici.toml`` is not overwritten
    unless ``--force`` says so. ``--preview`` prints the file instead.
    """

    root = Path.cwd().resolve()
    target = root / "ici.toml"
    languages = tuple(
        language for language, asked in (("python", python), ("cpp", cpp)) if asked
    ) or tuple(_SOURCE_SUFFIXES)
    components = _candidate_components(root, languages)
    lines = ["schema_version = 1", "[workspace]", f'name = "{root.name}"', ""]
    for component_id, rel_root, found in components:
        lines += [
            "[[components]]",
            f'id = "{component_id}"',
            f'root = "{rel_root}"',
            f"languages = {json.dumps(sorted(found))}",
            "",
        ]
    document = "\n".join(lines)
    if preview:
        typer.echo(document, nl=False)
        return
    if target.exists() and not force:
        typer.echo(
            f"init: {target} already exists — pass --force to overwrite or --preview to inspect",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIG)
    target.write_text(document, encoding="utf-8")
    typer.echo(f"wrote {target} ({len(components)} component(s))")


def _candidate_components(
    root: Path, languages: tuple[str, ...]
) -> list[tuple[str, str, set[str]]]:
    """Directory candidates — one component per directory holding sources.

    A directory with both Python and C++ sources is one hybrid component, not
    two with colliding ids. The root itself becomes a component only when it
    holds sources directly *and* no subdirectory was a better claim — a root
    that spans ``.`` would swallow the components under it.
    """

    hidden = {".git", ".hg", ".svn", ".ici", ".venv", "venv", "__pycache__", "build", "dist"}
    found: list[tuple[str, str, set[str]]] = []
    for directory in sorted(
        item for item in root.iterdir() if item.is_dir() and item.name not in hidden
    ):
        held = {
            language
            for language in languages
            for suffix in _SOURCE_SUFFIXES.get(language, ())
            if any(
                path.suffix == suffix and not hidden & set(path.relative_to(root).parts)
                for path in directory.rglob("*")
                if path.is_file()
            )
        }
        if held:
            rel = directory.relative_to(root)
            found.append((directory.name, str(rel), held))
    if not found:
        held = {
            language
            for language in languages
            for suffix in _SOURCE_SUFFIXES.get(language, ())
            if any(path.suffix == suffix for path in root.iterdir() if path.is_file())
        }
        if held:
            found.append((root.name, ".", held))
    return found


@next_app.command("doctor")
def cmd_doctor(
    component: list[str] = _COMPONENT_OPTION,
    python: bool = _PYTHON_OPTION,
    cpp: bool = _CPP_OPTION,
    profile: Profile | None = _PROFILE_OPTION,
    json_mode: bool = _JSON_OPTION,
    config_path: Path | None = _CONFIG_OPTION,
    local_config: Path | None = _LOCAL_CONFIG_OPTION,
) -> None:
    """What the scope's checks need, and how each need stands.

    For every selected check this reports the tool it wants, where the tool
    was found (bundle or PATH) and its probed version, or the reason it was
    blocked. For every omitted check it reports who omitted it. Guidance is
    written as cause → affected scope → the config key that resolves it → the
    command that rechecks, because "install something" is not the only fix a
    missing tool has.
    """

    cwd = Path.cwd().resolve()
    root, config, model = _workspace(cwd, config_path, local_config)
    request = _request(component, python, cpp, profile)
    scope = _scope(model, request)
    resolved = _profile(config, request)
    _announce(root, scope, request, resolved, json_mode)

    registry = builtin_registry()
    stock = inventory.take(scope, root=root)
    echo = (lambda line: typer.echo(line, err=True)) if json_mode else typer.echo
    components_out: list[dict[str, object]] = []
    for item in scope.components:
        units = {unit.language: unit for unit in _units_of(scope, item)}
        available = tuple(
            check for check in registry.checks_for(item.languages) if wanted(request, check)
        )
        effective = config.component(item.id)
        checks_out: list[dict[str, object]] = []
        echo(f"{item.id} ({', '.join(item.languages)}):")
        settings = {check.id: check for check in effective.checks} if effective else {}
        for check in available:
            files = (
                _unit_files(stock, units[check.language], _SOURCE_SUFFIXES[check.language])
                if check.language in units and check.language in _SOURCE_SUFFIXES
                else ()
            )
            decided = settings.get(check.id)
            line: dict[str, object] = {"check": check.id, "inputs": len(files)}
            if decided is not None and not decided.enabled.value:
                reason = f"disabled by {decided.enabled.origin} ({decided.enabled.layer.value})"
                line.update(status="omitted", reason=reason)
                echo(f"  {check.id}: omitted — {reason}")
            elif resolved not in check.profiles:
                reason = f"not in profile '{resolved.value}'"
                line.update(status="omitted", reason=reason)
                echo(f"  {check.id}: omitted — {reason}")
            elif not files:
                line.update(
                    status="blocked",
                    reason=f"no sources in scope for {check.language}",
                    resolve=(
                        f"declare sources on component '{item.id}' or add {check.language} files"
                    ),
                    recheck="ici next doctor",
                )
                _blocked(echo, check.id, line)
            elif check.tool:
                located = _locate(check.tool)
                if located is None:
                    line.update(
                        status="blocked",
                        reason=f"{check.tool} is not available",
                        resolve=(
                            f"install {check.tool}, or ship it under the bundle's "
                            f"{BUNDLED_TOOLS} directory"
                        ),
                        recheck="ici next doctor",
                    )
                    _blocked(echo, check.id, line)
                else:
                    origin = "bundle" if os.environ.get("ICI_BUNDLE_ROOT") else "PATH"
                    version = _version_of(located)
                    line.update(status="selected", tool=located, version=version, origin=origin)
                    echo(
                        f"  {check.id}: selected — {check.tool} at {located} ({origin}, {version})"
                    )
            else:
                line.update(status="selected", tool="ici")
                echo(f"  {check.id}: selected — runs in-process (ici)")
            checks_out.append(line)
        components_out.append(
            {"id": item.id, "languages": list(item.languages), "checks": checks_out}
        )
    if json_mode:
        typer.echo(
            dumps(
                {
                    "schema_id": "ici.next.doctor",
                    "schema_version": 1,
                    "root": str(root),
                    "profile": resolved.value,
                    "components": components_out,
                }
            )
        )


def _blocked(echo, check_id: str, line: dict[str, object]) -> None:
    """The cause → fix → recheck shape #210 item 7 asks guidance to take."""

    echo(f"  {check_id}: blocked — {line['reason']}")
    echo(f"    resolve: {line['resolve']}")
    echo(f"    recheck: {line['recheck']}")


@next_app.command("plan")
def cmd_plan(
    component: list[str] = _COMPONENT_OPTION,
    python: bool = _PYTHON_OPTION,
    cpp: bool = _CPP_OPTION,
    profile: Profile | None = _PROFILE_OPTION,
    require_full: bool = _REQUIRE_FULL_OPTION,
    json_mode: bool = _JSON_OPTION,
    config_path: Path | None = _CONFIG_OPTION,
    local_config: Path | None = _LOCAL_CONFIG_OPTION,
) -> None:
    """Say what a run would do. Runs nothing, builds nothing, installs nothing.

    The output is the request answered, not the run started: the selected and
    blocked checks with their argv or their reason, the dependency edges the
    graph wired from declared inputs, the executions several checks will
    share, the mutating steps and the directories they hold, and the cost
    profile the plan was cut to.
    """

    cwd = Path.cwd().resolve()
    root, config, model = _workspace(cwd, config_path, local_config)
    request = _request(component, python, cpp, profile, require_full)
    scope = _scope(model, request)
    resolved = _profile(config, request)
    stock = inventory.take(scope, root=root)
    try:
        plans, _, limitations, by_component = _plans(scope, config, stock, root, request, resolved)
    except NothingSelected as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    _announce(root, scope, request, resolved, json_mode)

    graph = _graph_of(plans)
    edges = {unit.id: list(unit.depends_on) for unit in graph.units if unit.depends_on}
    shared = {unit.id: list(unit.consumers) for unit in graph.units if len(unit.consumers) > 1}
    mutating = {
        unit.id: sorted(unit.task.resource_keys)
        for unit in graph.units
        if unit.mutating and unit.task is not None
    }
    gaps = uncovered_scope(model, request, scope) if request.require_full else ()

    if json_mode:
        typer.echo(
            dumps(
                {
                    "schema_id": "ici.next.plan",
                    "schema_version": 1,
                    "root": str(root),
                    "scope": {
                        "components": [item.id for item in scope.components],
                        "languages": list(requested_languages(request, scope)),
                        "profile": resolved.value,
                        "require_full": request.require_full,
                    },
                    "plans": [
                        {
                            "component": component_id,
                            "checks": [
                                {
                                    "task_id": planned.task_id,
                                    "check": planned.check.id,
                                    "kind": (
                                        "blocked"
                                        if planned.blocked
                                        else "internal"
                                        if planned.is_internal
                                        else "process"
                                    ),
                                    **(
                                        {"argv": list(planned.task.task.argv)}
                                        if planned.task is not None
                                        else {}
                                    ),
                                    **({"blocked": planned.blocked} if planned.blocked else {}),
                                }
                                for planned in plan.checks
                            ],
                        }
                        for component_id, plan in by_component.items()
                    ],
                    "graph": {"edges": edges, "shared": shared, "mutating": mutating},
                    "coverage_gaps": list(gaps),
                    "limitations": list(limitations),
                }
            )
        )
        return

    total = sum(len(plan.checks) for plan in plans)
    typer.echo(f"{scope.id}: {total} check(s)")
    for component_id, plan in by_component.items():
        typer.echo(f"{component_id}:")
        for planned in plan.checks:
            typer.echo(_describe(planned))
    if edges:
        typer.echo("dependencies:")
        for unit_id, deps in edges.items():
            typer.echo(f"  {unit_id} needs {', '.join(deps)}")
    if shared:
        typer.echo("shared:")
        for unit_id, consumers in shared.items():
            typer.echo(f"  {unit_id} runs once for {', '.join(consumers)}")
    if mutating:
        typer.echo("mutating:")
        for unit_id, keys in mutating.items():
            typer.echo(f"  {unit_id} writes {', '.join(keys) or '(undeclared)'}")
    for gap in gaps:
        typer.echo(f"  coverage gap: {gap}")
    for limitation in limitations:
        typer.echo(f"  note: {limitation}")


@next_app.command("verify")
def cmd_verify(
    component: list[str] = _COMPONENT_OPTION,
    python: bool = _PYTHON_OPTION,
    cpp: bool = _CPP_OPTION,
    profile: Profile | None = _PROFILE_OPTION,
    require_full: bool = _REQUIRE_FULL_OPTION,
    result: Path = _RESULT_OPTION,
    no_cache: bool = _NO_CACHE_OPTION,
    json_mode: bool = _JSON_OPTION,
    events: Path | None = _EVENTS_OPTION,
    config_path: Path | None = _CONFIG_OPTION,
    local_config: Path | None = _LOCAL_CONFIG_OPTION,
) -> None:
    """Run the selected checks and save the result."""

    cwd = Path.cwd().resolve()
    root, config, model = _workspace(cwd, config_path, local_config)
    request = _request(component, python, cpp, profile, require_full)
    scope = _scope(model, request)
    resolved = _profile(config, request)
    _announce(root, scope, request, resolved, json_mode)

    before = inventory.take(scope, root=root)
    state = vcs_status(root)
    snapshot = before.snapshot(
        commit=state.commit if state is not None else None,
        dirty=state is None or not state.clean,
    )
    try:
        plans, analyses, limitations, _ = _plans(scope, config, before, root, request, resolved)
    except NothingSelected as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error

    run_id = uuid.uuid4().hex
    sink = (
        _EventSink(root / events if not events.is_absolute() else events, run_id)
        if events
        else None
    )
    if sink is not None:
        sink.emit(EventType.RUN_STARTED)
        sink.emit(
            EventType.PLAN_READY,
            message=f"{sum(len(plan.checks) for plan in plans)} check(s) across {len(plans)} plan(s)",
        )

    cache = None if no_cache else ObservationCache(root / ".ici" / "cache" / "observations")
    providers: dict[str, Provider] = {"ruff": RuffProvider()}
    verification = run_verification(
        plans,
        providers=providers,
        analyses=analyses,
        cache=cache,
        identify=_identifier(resolved, config.policy_digest),
        run_id=run_id,
        on_execution=sink.on_execution if sink is not None else None,
    )

    gaps = uncovered_scope(model, request, scope) if request.require_full else ()
    verification = apply_require_full(verification, gaps)

    drift = inventory.diff(before, inventory.take(scope, root=root))
    if not drift.stable:
        limitations = [*limitations, f"inputs changed while running: {_drift_summary(drift)}"]

    # FULL is the domain's claim that the workspace's required scope was
    # covered *and* finished — a component subset, a language filter that
    # left declared languages out, or an incomplete run are all PARTIAL,
    # because the model refuses a FULL it cannot stand behind.
    covered = set(requested_languages(request, scope))
    declared = {language for item in model.components for language in item.languages}
    if config.scope_kind is ScopeKind.STANDALONE:
        scope_kind = ScopeKind.STANDALONE
    elif component or covered != declared or verification.gate.selected is GateVerdict.INCOMPLETE:
        scope_kind = ScopeKind.PARTIAL
    else:
        scope_kind = ScopeKind.FULL

    stored = assemble(
        verification,
        run_id=run_id,
        ici_version=__version__,
        source=snapshot,
        policy_digest=config.policy_digest,
        toolchain_digest=digest_of("|".join(sorted(_tools(plans)))),
        component_ids=tuple(item.id for item in scope.components),
        languages=requested_languages(request, scope),
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

    if sink is not None:
        sink.emit(
            EventType.RUN_COMPLETED,
            message=f"{stored.gate.selected.value}: {len(stored.findings)} finding(s)",
        )
        sink.write()

    if json_mode:
        typer.echo(dumps(run_result_to_dict(stored)))
        raise typer.Exit(stored.gate.exit_code)

    typer.echo(f"{stored.gate.selected.value}: {len(stored.findings)} finding(s)")
    for reason in stored.gate.reasons:
        typer.echo(f"  {reason}")
    reused = [item for item in verification.executions if item.detail.startswith("cache hit")]
    if reused:
        typer.echo(f"  cache: reused {len(reused)} stored result(s)")
    for item in verification.executions:
        if item.detail and not item.detail.startswith(("cache hit", "no stored")):
            typer.echo(f"  note: {item.unit}: {item.detail}")
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


def _identifier(profile: Profile, policy_digest: str):
    """Bind the run's policy digest into the per-unit identity callback.

    What a task's answer depends on: its declared inputs' content, the tool's
    own bytes, the policy it ran under, and the profile that cut the plan —
    the same source can answer differently when a cheaper profile asked less.
    A unit whose inputs cannot all be measured gets no key — it still runs,
    and the reason is recorded rather than a partial key standing in for a
    complete one.
    """

    def identify(unit: WorkUnit) -> tuple[str | None, str]:
        if unit.is_internal:
            return task_identity(
                unit,
                input_digests=None,
                tool_digest=None,
                policy_digest=f"{policy_digest}|{profile.value}",
            )
        task = unit.task
        cwd = Path(task.cwd)
        digests: list[tuple[str, str]] = []
        try:
            for ref in task.input_refs:
                target = (cwd / ref).resolve()
                if not target.is_file():
                    raise OSError(f"{ref} is not a file")
                digests.append((ref, file_digest(target)))
        except OSError:
            return task_identity(
                unit, input_digests=None, tool_digest=None, policy_digest=policy_digest
            )
        executable = Path(task.argv[0])
        if not executable.is_file():
            found = shutil.which(task.argv[0])
            executable = Path(found) if found else Path("")
        tool = file_digest(executable) if executable.is_file() else None
        return task_identity(
            unit,
            input_digests=tuple(digests),
            tool_digest=tool,
            policy_digest=f"{policy_digest}|{profile.value}",
        )

    return identify


def _tools(plans: list[Plan]) -> set[str]:
    return {
        item.task.task.argv[0]
        for plan in plans
        for item in plan.checks
        if item.task is not None and item.will_run
    }
