"""The check-to-task bridge: which checks a component owns, and their tasks.

``plans`` turns a scoped workspace into one :class:`Plan` per component —
the registry decides which checks apply, the gates attach real tasks or
blocked markers, and internal checks register their in-process analyses.
Planning never starts a process (#206): tool questions are existence-only
here, and ``doctor`` owns the bounded probes.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from ici.adapters.providers.base import ProviderPlan
from ici.adapters.providers.ruff import RuffProvider, RuffRequest
from ici.adapters.providers.tidy import ClangTidyProvider
from ici.application.gates import gate_cpp, gate_python
from ici.application.graph import TaskGraph, build_graph
from ici.application.integration import gate_integration
from ici.application.internal_checks import internal_analysis
from ici.application.plan import NothingSelected, Plan, PlannedCheck
from ici.application.request import RunRequest, wanted
from ici.application.selection import Planner, select_effective
from ici.application.testing import component_targets
from ici.application.tooling import locate_tool
from ici.application.verify import Analysis
from ici.config.composition import EffectiveConfig
from ici.domain.enums import Profile
from ici.domain.workspace import AnalysisUnit, Component, Workspace
from ici.languages.checks import CheckDefinition
from ici.languages.integration import INTEGRATION_CASES_CHECK
from ici.languages.registry import builtin as builtin_registry
from ici.workspace import compile_units, inventory
from ici.workspace.inventory import SourceInventory, SourceRole

__all__ = [
    "SOURCE_SUFFIXES",
    "compile_limitations",
    "component_root",
    "describe",
    "drift_summary",
    "graph_of",
    "plans",
    "unit_files",
    "units_of",
]


def plans(
    scope: Workspace,
    config: EffectiveConfig,
    stock: SourceInventory,
    root: Path,
    request: RunRequest,
    profile: Profile,
    mutate: bool = False,
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
    result: list[Plan] = []
    by_component: dict[str, Plan] = {}
    analyses: dict[str, Analysis] = {}
    limitations: list[str] = []
    for component in scope.components:
        units = {unit.language: unit for unit in units_of(scope, component)}
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
        comp_root = component_root(root, component)
        # Declared cases opt a component into the contract check; the check
        # is a domain check, not a language, so nothing else surfaces it.
        if effective.integrations and wanted(request, INTEGRATION_CASES_CHECK):
            available = (*available, INTEGRATION_CASES_CHECK)
        files_by_language = {
            language: unit_files(stock, units[language], SOURCE_SUFFIXES[language])
            for language in component.languages
            if language in units and language in SOURCE_SUFFIXES
        }
        compile_inputs = (
            compile_units.load_compile_inputs(
                root, component, files_by_language.get("cpp", ()), scope.builds
            )
            if "cpp" in component.languages
            else None
        )
        work = planner(
            comp_root,
            root,
            component.id,
            units.get("python"),
            files_by_language.get("python", ()),
            tool_configs(comp_root, root),
            units.get("cpp"),
            compile_inputs,
        )

        def in_scope(
            check: CheckDefinition,
            found: dict[str, tuple[str, ...]] = files_by_language,
        ) -> bool:
            if check.language == "integration":
                return True  # its scope is the declared cases, not source files
            return bool(found.get(check.language))

        try:
            plan = select_effective(
                effective,
                locate_tool,
                work,
                available=available,
                task_prefix=component.id,
                profile=profile,
                in_scope=in_scope,
            )
        except NothingSelected as error:
            limitations.append(str(error))
            continue
        plan = gate_cpp(
            plan,
            component,
            files_by_language.get("cpp", ()),
            scope.builds,
            root,
            compile_inputs,
            units.get("cpp"),
        )
        plan = gate_python(
            plan,
            component.id,
            effective,
            comp_root,
            root,
            files_by_language.get("python", ()),
            units.get("python"),
            mutate=mutate,
        )
        plan = gate_integration(plan, component, effective, comp_root, root, scope.builds)
        result.append(plan)
        by_component[component.id] = plan
        metric_cache: dict = {}
        for planned in plan.checks:
            if planned.is_internal:
                analyses[planned.task_id] = internal_analysis(
                    planned,
                    component,
                    files_by_language.get(planned.check.language, ()),
                    comp_root,
                    root,
                    scope.builds,
                    metric_cache,
                )
    if not result:
        raise NothingSelected(
            "no component selected a check; a run that checks nothing cannot pass"
        )
    return result, analyses, limitations, by_component


def planner(
    component_root_: Path,
    root: Path,
    component_id: str,
    unit: AnalysisUnit | None,
    files: tuple[str, ...],
    config_files: tuple[str, ...],
    cpp_unit: AnalysisUnit | None = None,
    compile_inputs: compile_units.CompileInputs | None = None,
) -> Planner:
    """Bind one component's context into the check-to-task callback."""

    def work(check: CheckDefinition, executable: str, task_id: str) -> ProviderPlan:
        if check.id == "python.format":
            assert unit is not None
            return RuffProvider().plan(
                RuffRequest(
                    executable=executable,
                    project_root=component_root_,
                    targets=component_targets(component_root_, root, files),
                    task_id=task_id,
                    component_id=component_id,
                    analysis_unit_id=unit.id,
                    config_files=config_files,
                    mode="format",
                )
            )
        if check.id == "cpp.tidy":
            assert cpp_unit is not None
            database_dir = (
                str(root / str(PurePosixPath(compile_inputs.database_path).parent))
                if compile_inputs is not None and compile_inputs.database_path
                else str(root)
            )
            sources = (
                tuple(str(root / u.source) for u in compile_inputs.units)
                if compile_inputs is not None
                else ()
            )
            return ClangTidyProvider().plan(
                executable,
                database_dir=database_dir,
                sources=sources,
                task_id=task_id,
                cwd=str(root),
                analysis_unit_id=cpp_unit.id,
                input_refs=(
                    (
                        compile_inputs.database_path,
                        *(u.source for u in compile_inputs.units),
                    )
                    if compile_inputs is not None and compile_inputs.database_path
                    else ()
                ),
            )
        assert unit is not None
        return RuffProvider().plan(
            RuffRequest(
                executable=executable,
                project_root=component_root_,
                targets=component_targets(component_root_, root, files),
                task_id=task_id,
                component_id=component_id,
                analysis_unit_id=unit.id,
                config_files=config_files,
            )
        )

    return work


def tool_configs(component_root_: Path, root: Path) -> tuple[str, ...]:
    """The tool configuration files a check under this root would read.

    Ruff reads ``ruff.toml``/``.ruff.toml``/``pyproject.toml`` from the
    project root upward — so the search walks the component root to the
    workspace root and stops there. What a check reads is part of what it
    measured, which is why these are declared inputs (#209) rather than
    discovered quietly at run time.
    """

    found: list[str] = []
    base = component_root_
    while True:
        for name in ("ruff.toml", ".ruff.toml", "pyproject.toml"):
            candidate = base / name
            if candidate.is_file():
                try:
                    found.append(str(candidate.relative_to(component_root_)))
                except ValueError:
                    found.append(str(candidate))
        if base == root or root not in base.parents:
            break
        base = base.parent
    return tuple(found)


def compile_limitations(
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
        units = {unit.language: unit for unit in units_of(scope, component)}
        cpp_unit = units.get("cpp")
        files = unit_files(stock, cpp_unit, SOURCE_SUFFIXES["cpp"]) if cpp_unit is not None else ()
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
            if diagnostic.code in (
                "generated-input-missing",
                "qmake-target-missing",
                "cmake-target-missing",
                "build-definition-missing",
            ):
                gaps.append(f"{component.id}: {diagnostic.message}")
    return limitations, gaps


def unit_files(
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


def component_root(root: Path, component: Component) -> Path:
    return (root / PurePosixPath(component.root)).resolve()


#: Which file suffixes count as a language's sources when a unit's declared
#: globs resolve through the inventory. A language with no entry has no
#: checks that could read it, so nothing asks for its files.
SOURCE_SUFFIXES = {
    "python": (".py",),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"),
}


def units_of(scope: Workspace, component: Component) -> tuple[AnalysisUnit, ...]:
    return tuple(unit for unit in scope.analysis_units if unit.component_id == component.id)


def drift_summary(drift: inventory.InventoryDiff) -> str:
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


def graph_of(plans_: list[Plan]) -> TaskGraph:
    return build_graph(tuple(item for plan in plans_ for item in plan.checks))


def describe(planned: PlannedCheck) -> str:
    """One plan line: what runs, or why it cannot."""

    if planned.blocked:
        return f"  {planned.task_id}: blocked — {planned.blocked}"
    if planned.is_internal:
        return f"  {planned.task_id}: {planned.check.title} (ici)"
    assert planned.task is not None
    marker = " [mutating]" if planned.task.task.mutating else ""
    needs = (
        f" [requires {', '.join(planned.task.task.requires)}]" if planned.task.task.requires else ""
    )
    return f"  {planned.task_id}: {' '.join(planned.task.task.argv)}{marker}{needs}"
