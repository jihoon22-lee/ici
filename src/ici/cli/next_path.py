"""``ici next`` — the new path, reachable only by asking for it.

The command set is SPEC-01 section 5's: ``init`` writes a root ``ici.toml``,
``doctor`` probes tool availability, ``plan`` says what a run would do,
``verify`` runs it and saves the result, ``report`` renders a saved result.
The request→scope→plan plumbing these stand on lives in
:mod:`ici.cli.next_common` — this module is the command surface and nothing
else, because nothing here may be wired into the stable commands.

Machine output and human output never share a stream: ``--json`` puts the
result document on stdout and every diagnostic on stderr, and ``--events``
writes the ``ici.next.event`` JSONL stream to its own file. The exit codes
are SPEC-04 section 3's — a configuration that cannot be read is not a
failing verification, so it is 2, not 1.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import typer

from ici import __version__
from ici.adapters.providers.base import Provider
from ici.adapters.providers.binarycompat import BinaryCompatProvider
from ici.adapters.providers.compiler import CompilerDiagnosticsProvider
from ici.adapters.providers.coverage import CoverageProvider
from ici.adapters.providers.cpptest import CtestProvider, QtestProvider
from ici.adapters.providers.gcov import GcovProvider
from ici.adapters.providers.integration import IntegrationCaseProvider
from ici.adapters.providers.mypy import MypyProvider
from ici.adapters.providers.pycompat import CompileallProvider, PythonVersionProvider
from ici.adapters.providers.pytest import PytestProvider
from ici.adapters.providers.ruff import RuffProvider
from ici.adapters.providers.sanitize import SanitizeProvider
from ici.adapters.providers.tidy import ClangTidyProvider
from ici.adapters.providers.ty import TyProvider
from ici.application.baseline import BaselineError, compare, load_baseline
from ici.application.graph import WorkUnit
from ici.application.identity import task_identity
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.application.publish import PublishError
from ici.application.publish import publish as publish_result
from ici.application.report import assemble, digest_of
from ici.application.request import (
    RunRequest,
    apply_require_full,
    requested_languages,
    uncovered_scope,
    wanted,
)
from ici.application.verify import verify as run_verification
from ici.cli.next_common import (
    _BASELINE_ARG,
    _BASELINE_OPTION,
    _COMPONENT_OPTION,
    _CONFIG_OPTION,
    _CPP_OPTION,
    _EVENTS_OPTION,
    _FORCE_OPTION,
    _JSON_OPTION,
    _LOCAL_CONFIG_OPTION,
    _NO_CACHE_OPTION,
    _PAGE_OPTION,
    _PREVIEW_OPTION,
    _PROFILE_OPTION,
    _PYTHON_OPTION,
    _REQUIRE_FULL_OPTION,
    _RESULT_OPTION,
    _SARIF_OPTION,
    _SOURCE_SUFFIXES,
    EXIT_CONFIG,
    _announce,
    _compile_limitations,
    _describe,
    _drift_summary,
    _graph_of,
    _plans,
    _profile,
    _request,
    _scope,
    _unit_files,
    _units_of,
    _version_of,
    _workspace,
    next_app,
)
from ici.cli.next_events import EventSink
from ici.cli.next_testing import BUNDLED_TOOLS, locate_tool
from ici.config.composition import EffectiveCheck
from ici.domain.enums import (
    BaselineState,
    GateVerdict,
    Profile,
    PublicationState,
    ScopeKind,
)
from ici.domain.events import EventType
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.workspace import AnalysisUnit, Component, Workspace
from ici.execution.cache import ObservationCache
from ici.execution.cancellation import Cancellation, signal_cancels
from ici.execution.manifest import digest_of as file_digest
from ici.execution.process import run_task
from ici.languages.checks import CheckDefinition
from ici.languages.registry import builtin as builtin_registry
from ici.reporting.offline_html import render
from ici.reporting.sarif import SarifBoundsError
from ici.reporting.sarif import document as sarif_document
from ici.workspace import compile_units, inventory
from ici.workspace.inventory import SourceInventory
from ici.workspace.vcs import status as vcs_status

__all__ = ["next_app"]


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
            checks_out.append(_doctor_check(item, check, settings, units, stock, resolved, echo))
        entry: dict[str, object] = {
            "id": item.id,
            "languages": list(item.languages),
            "checks": checks_out,
        }
        if "cpp" in item.languages:
            entry["compile_inputs"] = _doctor_compile(root, model, item, units, stock, echo)
        components_out.append(entry)
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


def _doctor_check(
    item: Component,
    check: CheckDefinition,
    settings: dict[str, EffectiveCheck],
    units: dict[str, AnalysisUnit],
    stock: SourceInventory,
    resolved: Profile,
    echo,
) -> dict[str, object]:
    """One check's doctor verdict: omitted, blocked with guidance, or selected."""

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
            resolve=(f"declare sources on component '{item.id}' or add {check.language} files"),
            recheck="ici next doctor",
        )
        _blocked(echo, check.id, line)
    elif check.tool:
        located = locate_tool(check.tool)
        if located is None:
            line.update(
                status="blocked",
                reason=f"{check.tool} is not available",
                resolve=(
                    f"install {check.tool}, or ship it under the bundle's {BUNDLED_TOOLS} directory"
                ),
                recheck="ici next doctor",
            )
            _blocked(echo, check.id, line)
        else:
            origin = "bundle" if os.environ.get("ICI_BUNDLE_ROOT") else "PATH"
            version = _version_of(located)
            line.update(status="selected", tool=located, version=version, origin=origin)
            echo(f"  {check.id}: selected — {check.tool} at {located} ({origin}, {version})")
    else:
        line.update(status="selected", tool="ici")
        echo(f"  {check.id}: selected — runs in-process (ici)")
    return line


def _doctor_compile(
    root: Path,
    model: Workspace,
    item: Component,
    units: dict[str, AnalysisUnit],
    stock: SourceInventory,
    echo,
) -> dict[str, object]:
    """A cpp component's compilation-input picture for the doctor report."""

    cpp_unit = units.get("cpp")
    files = _unit_files(stock, cpp_unit, _SOURCE_SUFFIXES["cpp"]) if cpp_unit is not None else ()
    inputs = compile_units.load_compile_inputs(root, item, files, model.builds)
    if inputs.database_path:
        echo(
            f"  compile db: {inputs.database_path} "
            f"({inputs.origin}) — {len(inputs.covered)}/{len(inputs.expected)} TU(s)"
        )
        for source in inputs.missing:
            echo(f"    missing: {source}")
    else:
        echo("  compile db: none")
    for target in inputs.targets:
        marker = target.project or "unresolved"
        echo(f"    target: {target.name} → {marker}")
    if inputs.generated:
        echo(f"    generated inputs: {len(inputs.generated)}")
    for prepare in inputs.prepare:
        echo(f"    prepare: {prepare}")
    return {
        "database": inputs.database_path or None,
        "origin": inputs.origin or None,
        "expected": len(inputs.expected),
        "covered": list(inputs.covered),
        "missing": list(inputs.missing),
        "generated": list(inputs.generated),
        "targets": [
            {"name": t.name, "project": t.project, "depends": list(t.depends)}
            for t in inputs.targets
        ],
        "prepare": list(inputs.prepare),
    }


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
    compile_notes, compile_gaps = _compile_limitations(model, scope, stock, root)
    limitations = [*limitations, *compile_notes]
    gaps = uncovered_scope(model, request, scope) + tuple(compile_gaps)

    builds = _linked_builds(scope)
    if json_mode:
        _plan_json(
            root,
            scope,
            request,
            resolved,
            by_component,
            edges,
            shared,
            mutating,
            builds,
            gaps,
            limitations,
        )
        return
    _plan_text(scope, plans, by_component, edges, shared, mutating, builds, gaps, limitations)


def _linked_builds(scope: Workspace) -> list[dict[str, object]]:
    """The build units in scope, with the directory a prepare would write.

    ``prepare = "explicit"`` means nothing here runs — but a plan that cannot
    name where evidence comes from cannot be checked. Each entry answers the
    same provenance question the compile database answers (#213 item 6).
    """

    linked: dict[str, set[str]] = {}
    for item in scope.components:
        for build_id in item.build_ids:
            linked.setdefault(build_id, set()).add(item.id)
    return [
        {
            "id": build.id,
            "system": build.system,
            "variant": build.variant,
            "directory": build.directory,
            "definition": build.definition,
            "artifacts": list(build.artifacts),
            "linked_by": sorted(linked.get(build.id, ())),
        }
        for build in scope.builds
    ]


def _build_line(build: dict[str, object]) -> str:
    linked = build["linked_by"]
    names = ", ".join(str(item) for item in linked) if isinstance(linked, list) else ""
    return (
        f"  {build['id']}: {build['system']} {build['variant']} "
        f"→ {build['directory']} (for {names or 'unlinked'})"
    )


def _plan_json(
    root: Path,
    scope: Workspace,
    request: RunRequest,
    resolved: Profile,
    by_component: dict[str, Plan],
    edges: dict[str, list[str]],
    shared: dict[str, list[str]],
    mutating: dict[str, list[str]],
    builds: list[dict[str, object]],
    gaps: tuple[str, ...],
    limitations: list[str],
) -> None:
    """The plan as a document — stdout carries this and nothing else."""

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
                        "checks": [_planned_dict(planned) for planned in plan.checks],
                    }
                    for component_id, plan in by_component.items()
                ],
                "graph": {"edges": edges, "shared": shared, "mutating": mutating},
                "builds": builds,
                "coverage_gaps": list(gaps),
                "limitations": list(limitations),
            }
        )
    )


def _planned_dict(planned: PlannedCheck) -> dict[str, object]:
    entry: dict[str, object] = {
        "task_id": planned.task_id,
        "check": planned.check.id,
        "kind": (
            "blocked" if planned.blocked else "internal" if planned.is_internal else "process"
        ),
    }
    if planned.task is not None:
        entry["argv"] = list(planned.task.task.argv)
        if planned.task.task.requires:
            entry["requires"] = list(planned.task.task.requires)
    if planned.blocked:
        entry["blocked"] = planned.blocked
    return entry


def _plan_text(
    scope: Workspace,
    plans: list[Plan],
    by_component: dict[str, Plan],
    edges: dict[str, list[str]],
    shared: dict[str, list[str]],
    mutating: dict[str, list[str]],
    builds: list[dict[str, object]],
    gaps: tuple[str, ...],
    limitations: list[str],
) -> None:
    """The plan as lines a person reads."""

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
    if builds:
        typer.echo("builds:")
        for build in builds:
            typer.echo(_build_line(build))
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
    baseline: Path | None = _BASELINE_OPTION,
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
        plans, analyses, limitations, by_component = _plans(
            scope, config, before, root, request, resolved
        )
    except NothingSelected as error:
        typer.echo(f"config: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    compile_notes, compile_gaps = _compile_limitations(model, scope, before, root)
    limitations += compile_notes

    # Read the baseline before the run, not after: a baseline that cannot be
    # read is a rejected request, and the user should hear that before paying
    # for the verification rather than after a result was already written.
    baseline_doc = None
    if baseline is not None:
        try:
            baseline_doc = load_baseline(baseline if baseline.is_absolute() else root / baseline)
        except BaselineError as error:
            typer.echo(f"config: {error}", err=True)
            raise typer.Exit(EXIT_CONFIG) from error

    run_id = uuid.uuid4().hex
    sink = (
        EventSink(root / events if not events.is_absolute() else events, run_id) if events else None
    )
    if sink is not None:
        sink.emit(EventType.RUN_STARTED)
        sink.emit(
            EventType.PLAN_READY,
            message=f"{sum(len(plan.checks) for plan in plans)} check(s) across {len(plans)} plan(s)",
        )

    cache = None if no_cache else ObservationCache(root / ".ici" / "cache" / "observations")
    providers: dict[str, Provider] = {
        "ruff": RuffProvider(),
        "compiler": CompilerDiagnosticsProvider(),
        "clang-tidy": ClangTidyProvider(),
        "mypy": MypyProvider(),
        "ty": TyProvider(),
        "pytest": PytestProvider(),
        "coverage": CoverageProvider(),
        "ctest": CtestProvider(),
        "qtest": QtestProvider(),
        "gcov": GcovProvider(),
        "sanitize": SanitizeProvider("sanitize", project_root=root),
        "thread-sanitize": SanitizeProvider("thread-sanitize", project_root=root),
        "python-compat-version": PythonVersionProvider(),
        "python-compat-compileall": CompileallProvider(),
        "integration": IntegrationCaseProvider(),
        "binary-compat": BinaryCompatProvider(
            project_root=root,
            build_roots=tuple((root / build.directory).resolve() for build in model.builds),
        ),
    }
    # SIGINT/SIGTERM land as a fact on the run, not an exception: unstarted
    # units read it and report CANCELLED, the running one is told through its
    # watchdog, and the partial result is still written — SPEC-04 exit 130.
    cancellation = Cancellation()
    with signal_cancels(cancellation):
        verification = run_verification(
            plans,
            providers=providers,
            analyses=analyses,
            runner=lambda spec: run_task(spec, cancellation),
            cache=cache,
            identify=_identifier(resolved, config.policy_digest),
            run_id=run_id,
            cancellation=cancellation,
            on_execution=sink.on_execution if sink is not None else None,
            on_started=sink.on_started if sink is not None else None,
            suppressions=config.suppressions,
            task_components={
                planned.task_id: component_id
                for component_id, plan in by_component.items()
                for planned in plan.checks
            },
        )

    gaps = (
        uncovered_scope(model, request, scope) + tuple(compile_gaps) if request.require_full else ()
    )
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

    if cancellation.requested:
        limitations = [*limitations, f"cancelled: {cancellation.reason or 'user request'}"]
    toolchain_digest = digest_of("|".join(sorted(_tools(plans))))
    comparison = (
        compare(
            verification.findings,
            baseline_doc,
            policy_digest=config.policy_digest,
            toolchain_digest=toolchain_digest,
            selected_components=tuple(item.id for item in scope.components),
        )
        if baseline_doc is not None
        else None
    )
    stored = assemble(
        verification,
        run_id=run_id,
        ici_version=__version__,
        source=snapshot,
        policy_digest=config.policy_digest,
        toolchain_digest=toolchain_digest,
        component_ids=tuple(item.id for item in scope.components),
        languages=requested_languages(request, scope),
        scope=scope_kind,
        required_components=model.required_component_ids,
        omitted_components=tuple(
            item.id for item in model.components if item.id not in {c.id for c in scope.components}
        ),
        limitations=tuple(limitations),
        cancelled=cancellation.requested,
        baseline=comparison,
    )
    target = root / result if not result.is_absolute() else result
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(run_result_to_dict(stored)), encoding="utf-8")

    if sink is not None:
        if cancellation.requested:
            sink.emit(
                EventType.DIAGNOSTIC,
                message=f"cancelled: {cancellation.reason or 'user request'}",
            )
        sink.emit(
            EventType.RUN_COMPLETED,
            message=f"{stored.gate.selected.value}: {len(stored.findings)} finding(s)",
        )
        # If we never reach this close — a crash between emit and here — the
        # flushed prefix is still on disk, which is what a consumer should
        # see of a run that died: events, then nothing.
        sink.close()

    exit_code = 130 if cancellation.requested else stored.gate.exit_code
    if json_mode:
        typer.echo(dumps(run_result_to_dict(stored)))
        raise typer.Exit(exit_code)
    _verify_text(stored, verification, limitations, target, exit_code)


def _verify_text(
    stored, verification, limitations: list[str], target: Path, exit_code: int
) -> None:
    """The human half of ``verify``'s output — the verdict and its reasons."""

    typer.echo(f"{stored.gate.selected.value}: {len(stored.findings)} finding(s)")
    for reason in stored.gate.reasons:
        typer.echo(f"  {reason}")
    if stored.baseline is not None:
        delta = stored.baseline
        if delta.state is BaselineState.COMPARABLE:
            typer.echo(
                f"  baseline: {len(delta.new)} new, {len(delta.unchanged)} unchanged, "
                f"{len(delta.resolved)} resolved, {len(delta.carried)} carried"
            )
        else:
            typer.echo(f"  baseline: incompatible — {delta.reason}")
    reused = [item for item in verification.executions if item.detail.startswith("cache hit")]
    if reused:
        typer.echo(f"  cache: reused {len(reused)} stored result(s)")
    for item in verification.executions:
        if item.detail and not item.detail.startswith(("cache hit", "no stored")):
            typer.echo(f"  note: {item.unit}: {item.detail}")
    for limitation in limitations:
        typer.echo(f"  note: {limitation}")
    typer.echo(f"saved {target}")
    raise typer.Exit(exit_code)


@next_app.command("report")
def cmd_report(
    result: Path = _RESULT_OPTION,
    page: Path = _PAGE_OPTION,
    sarif: Path | None = _SARIF_OPTION,
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

    if sarif is not None:
        target = root / sarif if not sarif.is_absolute() else sarif
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(dumps(sarif_document(stored)) + "\n", encoding="utf-8")
        except SarifBoundsError as error:
            typer.echo(f"report: {error}", err=True)
            raise typer.Exit(EXIT_CONFIG) from error
        typer.echo(f"wrote {target}")
        return

    target = root / page if not page.is_absolute() else page
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(stored), encoding="utf-8")
    typer.echo(f"wrote {target}")


@next_app.command("publish")
def cmd_publish(
    config_path: Path | None = _CONFIG_OPTION,
    local_path: Path | None = _LOCAL_CONFIG_OPTION,
    result: Path = _RESULT_OPTION,
    page: Path = _PAGE_OPTION,
) -> None:
    """Push the saved result's page to the configured backend (#223).

    Reads stored artifacts only — nothing is re-analysed, and the result
    file is not modified; the outcome lands in ``publish.json`` beside it,
    on its own axis, so a failed upload can be retried without re-running.
    Exit codes are publish's own: 0 published, 1 the upload/comment failed,
    2 the request could not be formed (bad inputs, no destination).
    """

    root, config, _ = _workspace(Path.cwd(), config_path, local_path)
    try:
        outcome = publish_result(
            root,
            config=config,
            result_path=root / result if not result.is_absolute() else result,
            page_path=root / page if not page.is_absolute() else page,
            env=os.environ,
        )
    except PublishError as error:
        typer.echo(f"publish: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error

    record = root / ".ici" / "next" / "publish.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(outcome.to_record(), indent=2) + "\n", encoding="utf-8")

    typer.echo(f"publish: {outcome.state.value.lower()} — {outcome.detail}")
    if outcome.comment_url:
        typer.echo(f"comment: {outcome.comment_url}")
    if outcome.viewer_url:
        typer.echo(f"page: {outcome.viewer_url}")
    if outcome.state is PublicationState.FAILED:
        raise typer.Exit(1)
    if outcome.state is PublicationState.NOT_CONFIGURED:
        # An explicit `next publish` with nowhere to go is an input error —
        # succeeding here would teach a CI job that silence is publishing.
        raise typer.Exit(EXIT_CONFIG)


@next_app.command("diff")
def cmd_diff(
    baseline: Path = _BASELINE_ARG,
    current: Path = _RESULT_OPTION,
) -> None:
    """Compare two stored results — the same machinery ``--baseline`` uses.

    ``baseline`` is the older saved result, ``current`` defaults to the last
    run's. The report separates what the newer run actually resolved from
    what it simply never looked at, so a narrower scope cannot read as fixed
    code (#221 item 7).
    """

    root = Path.cwd().resolve()
    old_path = baseline if baseline.is_absolute() else root / baseline
    new_path = current if current.is_absolute() else root / current
    try:
        old = load_baseline(old_path)
    except BaselineError as error:
        typer.echo(f"diff: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    try:
        new = loads(new_path.read_text(encoding="utf-8"))
    except OSError as error:
        typer.echo(f"diff: {new_path} cannot be read: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error
    except (ValueError, json.JSONDecodeError) as error:
        typer.echo(f"diff: {new_path} is not a result this version can read: {error}", err=True)
        raise typer.Exit(EXIT_CONFIG) from error

    delta = compare(
        new.findings,
        old,
        policy_digest=new.identity.policy_digest,
        toolchain_digest=new.identity.toolchain_digest,
        selected_components=new.scope.selected_components,
    )
    if delta.state is BaselineState.INCOMPATIBLE:
        typer.echo(f"incompatible: {delta.reason}")
        raise typer.Exit(EXIT_CONFIG)
    typer.echo(
        f"{len(delta.new)} new, {len(delta.unchanged)} unchanged, "
        f"{len(delta.resolved)} resolved, {len(delta.carried)} carried "
        f"(baseline {old_path.name} → {new_path.name})"
    )
    if delta.carried:
        typer.echo(
            "  carried findings were in components the newer run did not "
            "select — reduced scope, not fixed code"
        )
    old_gate = None
    try:
        old_result = loads(old_path.read_text(encoding="utf-8"))
        old_gate = old_result.gate.selected.value
    except (OSError, ValueError):
        pass
    if old_gate and old_gate != new.gate.selected.value:
        typer.echo(f"  gate: {old_gate} → {new.gate.selected.value}")


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
