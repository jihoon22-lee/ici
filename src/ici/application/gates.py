"""The plan gates — which checks may run, and which become blocked markers.

A gate reads the plan the selection produced and either attaches the real
task to each check or replaces it with a blocked marker naming the remedy.
Nothing here starts a process: tool and interpreter questions go through
``application.tooling``'s probe-free mode, and a check that cannot run is
blocked with the reason rather than silently dropped (#206, #212-#220).
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from ici.adapters.providers.compiler import (
    CompilerDiagnosticsProvider,
    compiler_family,
    transform_argv,
)
from ici.adapters.providers.mypy import MypyProvider
from ici.adapters.providers.ty import TyProvider
from ici.application.plan import Plan, PlannedCheck
from ici.application.testing import (
    component_targets,
    cpp_gcno,
    cpp_suites,
    expand_cpp_binary_compat,
    expand_cpp_sanitizer,
    expand_cpp_tests,
    expand_python_compat_runtime,
    plan_coverage,
    plan_cpp_coverage,
    plan_test,
)
from ici.application.tooling import live_resolver, locate_tool, resolve_python, resolve_tool
from ici.config.composition import EffectiveComponent
from ici.domain.workspace import AnalysisUnit, BuildUnit, Component
from ici.toolchain.resolution import ResolvedTool, Role, Unresolved
from ici.workspace import compile_units

__all__ = ["gate_cpp", "gate_python"]


def gate_cpp(
    plan: Plan,
    component: Component,
    files: tuple[str, ...],
    builds: tuple[BuildUnit, ...],
    root: Path,
    inputs: compile_units.CompileInputs | None,
    cpp_unit: AnalysisUnit | None,
) -> Plan:
    """Gate the compile-input checks, and expand per-TU work where it exists.

    Three checks read the same evidence, loaded once:

    - ``cpp.compile`` is blocked when no database declares where the C++
      scope's compile invocations live — the remedy names the declared build
      or the config key that would declare one (#212).
    - ``cpp.tidy`` is blocked on the same condition — ``-p`` replays the
      database, so without one there is nothing to point it at (#213/#214).
    - ``cpp.diagnostics`` expands into one task per covered translation
      unit, each carrying that TU's own recorded invocation transformed to
      ``-fsyntax-only`` (#214). A TU whose driver is not a gcc/clang-family
      compiler becomes a blocked marker, not a silent skip.
    - ``cpp.test`` expands into one task per suite the linked builds
      declare — ctest through the generated ``CTestTestfile``, QTest as the
      built binary; declared-but-unbuilt suites are blocked markers (#217).
    - ``cpp.coverage`` reads the ``.gcno``/``.gcda`` the instrumented build
      and that shared test run left — never rebuilding or re-running (#217).
    - ``cpp.sanitize``/``cpp.tsan`` run the suite binaries a matching
      ``variant`` build produced, gated on the instrumentation markers the
      runtime leaves in each binary — unbuilt or uninstrumented is blocked,
      never a vacuous pass (#220).
    - ``cpp.binary-compat`` expands into one readelf task per ELF artifact
      the linked builds' contract names — no contract means nothing to
      inspect, which is blocked rather than a pass (#220).
    """

    gated = {
        "cpp.compile",
        "cpp.tidy",
        "cpp.diagnostics",
        "cpp.test",
        "cpp.coverage",
        "cpp.artifact",
        "cpp.sanitize",
        "cpp.tsan",
        "cpp.binary-compat",
    }
    if not any(planned.check.id in gated for planned in plan.checks):
        return plan
    if inputs is None:
        inputs = compile_units.load_compile_inputs(root, component, files, builds)
    remedy = f" — {inputs.prepare[0]}" if inputs.prepare else ""
    missing = f"no compilation database for the component's C++ scope{remedy}"
    component_root = root / component.root
    test_selected = any(planned.check.id == "cpp.test" for planned in plan.checks)
    suites = cpp_suites(root, component, builds)
    gcno_files = cpp_gcno(root, component, builds)

    checks: list[PlannedCheck] = []
    for planned in plan.checks:
        if planned.blocked:
            checks.append(planned)
            continue
        if planned.check.id == "cpp.compile":
            if inputs.database_path:
                checks.append(planned)
            else:
                checks.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=planned.task_id,
                        blocked=missing,
                    )
                )
        elif planned.check.id == "cpp.tidy":
            if inputs.database_path:
                checks.append(planned)
            else:
                checks.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=planned.task_id,
                        blocked=f"{missing} — clang-tidy replays it via -p",
                    )
                )
        elif planned.check.id == "cpp.diagnostics":
            checks.extend(expand_diagnostics(planned, inputs, root, missing, cpp_unit))
        elif planned.check.id == "cpp.test":
            checks.extend(
                expand_cpp_tests(planned, suites, component, component_root, root, cpp_unit)
            )
        elif planned.check.id == "cpp.coverage":
            checks.append(
                plan_cpp_coverage(
                    planned,
                    test_selected,
                    gcno_files,
                    component,
                    root,
                    cpp_unit,
                )
            )
        elif planned.check.id == "cpp.artifact":
            checks.append(gate_artifact(planned, component, builds))
        elif planned.check.id == "cpp.binary-compat":
            checks.extend(expand_cpp_binary_compat(planned, component, root, builds, cpp_unit))
        elif planned.check.id in ("cpp.sanitize", "cpp.tsan"):
            checks.extend(
                expand_cpp_sanitizer(
                    planned,
                    "sanitize" if planned.check.id == "cpp.sanitize" else "thread-sanitize",
                    suites,
                    component,
                    component_root,
                    root,
                    builds,
                    cpp_unit,
                )
            )
        else:
            checks.append(planned)
    return Plan(checks=tuple(checks))


def gate_artifact(
    planned: PlannedCheck, component: Component, builds: tuple[BuildUnit, ...]
) -> PlannedCheck:
    """The artifact check applies only where a contract was declared.

    A component with no linked build — or builds that declare no
    ``artifacts`` — has no contract to verify, so the check is blocked and
    says why rather than reporting a vacuous pass (#220: 선언 없는 산출물
    검증을 성공으로 표시하지 않는다).
    """

    if not component.build_ids:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="no build unit linked — artifact declarations live on [builds.<id>]",
        )
    linked = [build for build in builds if build.id in component.build_ids]
    if not any(build.artifacts for build in linked):
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="no artifact contract — declare [builds.<id>] artifacts = [...]",
        )
    return planned


def expand_diagnostics(
    planned: PlannedCheck,
    inputs: compile_units.CompileInputs,
    root: Path,
    missing: str,
    cpp_unit: AnalysisUnit | None,
) -> list[PlannedCheck]:
    """One planned task per covered TU — each argv is the build's own."""

    if not inputs.database_path:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked=missing,
            )
        ]
    provider = CompilerDiagnosticsProvider()
    resolver = live_resolver(probe=False)  # planning starts no processes (#206)
    expanded: list[PlannedCheck] = []
    for index, unit in enumerate(inputs.units):
        task_id = f"{planned.task_id}.{task_slug(unit.source)}-{index}"
        stripped = unit.argv[len(unit.launchers) :] or unit.argv
        blocked = diagnostics_blocker(stripped, unit, root, resolver=resolver)
        if blocked is not None:
            expanded.append(PlannedCheck(check=planned.check, task_id=task_id, blocked=blocked))
            continue
        executable, token = stripped[0], source_token(stripped, unit, root)
        transformed = transform_argv(stripped, token)
        assert transformed is not None  # the blocker check already refused it
        if not Path(executable).is_absolute():
            resolved_driver = resolve_tool(executable, role=Role.COMPILER, resolver=resolver)
            if isinstance(resolved_driver, ResolvedTool):
                executable = resolved_driver.launch_path
        cwd = Path(root) / unit.directory
        expanded.append(
            PlannedCheck(
                check=planned.check,
                task_id=task_id,
                task=provider.plan(
                    executable,
                    transformed.argv,
                    cwd=str(cwd),
                    task_id=task_id,
                    analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
                    input_refs=(
                        os.path.relpath(root / unit.source, cwd),
                        os.path.relpath(root / inputs.database_path, cwd),
                    ),
                ),
            )
        )
    if not expanded:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="the database covers no translation units in this scope",
            )
        ]
    return expanded


def diagnostics_blocker(
    argv: tuple[str, ...],
    unit: compile_units.TranslationUnit,
    root: Path,
    *,
    resolver,
) -> str | None:
    """Why this TU's replay cannot be planned, or None when it can."""

    if not argv:
        return "the database recorded an empty invocation"
    driver = argv[0]
    if not compiler_family(driver):
        return (
            f"unsupported compiler '{PurePosixPath(driver).name}' — "
            "only gcc/clang-family invocations are replayed"
        )
    if Path(driver).is_absolute():
        if not Path(driver).is_file():
            return f"compiler '{driver}' does not exist"
    else:
        resolved = resolve_tool(driver, role=Role.COMPILER, resolver=resolver)
        if isinstance(resolved, Unresolved):
            return f"compiler '{driver}' is not usable: {resolved.detail}"
    cwd = root / unit.directory
    if not cwd.is_dir():
        return f"the recorded working directory {unit.directory} is gone — the capture is stale"
    return None


def interpreter_blocker(resolved: ResolvedTool | Unresolved) -> str:
    """Why no interpreter can run the suite, spelled for the gate line.

    The resolution carries the cause — missing file, timed-out probe, too-old
    version — where the old path had one fixed sentence for all of them.
    """

    if isinstance(resolved, ResolvedTool):
        return "no project interpreter"  # never shown — the interpreter exists
    parts = [resolved.detail]
    if resolved.considered:
        parts.append(f"looked at {', '.join(resolved.considered)}")
    key = resolved.origin.config_key
    if key:
        parts.append(f"set {key} to change it")
    else:
        parts.append("declare [python] executable or provide a .venv inside the component")
    return "no project interpreter — " + "; ".join(parts)


def source_token(argv: tuple[str, ...], unit: compile_units.TranslationUnit, root: Path) -> str:
    """The argv entry that names this TU, spelled as the database spelled it.

    Positional non-flag arguments are resolved against the recorded working
    directory; the first that lands on the unit's source wins. With no match
    the workspace-relative path is used — a wrong guess here would compile a
    different file than the build did, so the fallback stays literal.
    """

    target = (root / unit.source).resolve()
    for arg in reversed(argv[1:]):
        if arg.startswith("-"):
            continue
        try:
            if (root / unit.directory / arg).resolve() == target:
                return arg
        except OSError:
            continue
    try:
        return str((root / unit.source).relative_to(root / unit.directory))
    except ValueError:
        return str(root / unit.source)


def task_slug(source: str) -> str:
    """A source path made identifier-safe for a task id."""

    slug = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-.")
    return slug or "tu"


TYPE_CHECKERS: dict[str, MypyProvider | TyProvider] = {
    "mypy": MypyProvider(),
    "ty": TyProvider(),
}


def gate_python(
    plan: Plan,
    component_id: str,
    effective: EffectiveComponent | None,
    component_root: Path,
    root: Path,
    files: tuple[str, ...],
    unit: AnalysisUnit | None,
) -> Plan:
    """Resolve interpreter/tools and attach real tasks to the gated checks.

    ``python.type``'s checker is the component's own ``type_provider`` —
    mypy by default, ty only when named (#215). ``python.test`` and
    ``python.coverage`` run under the project's own interpreter; when both
    are selected the pytest task is wrapped in ``coverage run`` so one
    execution feeds both checks (#216 item 4).
    """

    gated = {"python.type", "python.test", "python.coverage", "python.compat-runtime"}
    if not any(planned.check.id in gated for planned in plan.checks):
        return plan
    decided = effective.python_type_provider if effective is not None else None
    provider_name = decided.value if decided is not None else "mypy"
    provider = TYPE_CHECKERS.get(provider_name)
    resolved_python = resolve_python(effective, component_root, root, probe=False)
    interpreter = resolved_python.launch_path if resolved_python.usable else None
    no_interpreter = interpreter_blocker(resolved_python)
    coverage_selected = any(
        planned.check.id == "python.coverage" and not planned.blocked for planned in plan.checks
    )
    test_selected = any(planned.check.id == "python.test" for planned in plan.checks)
    coverage_dir = root / ".ici" / "cache" / "coverage"
    data_file = str(coverage_dir / f"{component_id}.data")
    report_path = str(coverage_dir / f"{component_id}.json")

    checks: list[PlannedCheck] = []
    for planned in plan.checks:
        if planned.check.id not in gated or planned.blocked:
            checks.append(planned)
            continue
        if planned.check.id == "python.type":
            checks.append(
                plan_type_check(
                    planned,
                    provider,
                    provider_name,
                    component_root,
                    root,
                    files,
                    unit,
                )
            )
        elif planned.check.id == "python.test":
            checks.append(
                plan_test(
                    planned,
                    interpreter,
                    no_interpreter,
                    effective,
                    component_root,
                    root,
                    unit,
                    coverage_data=data_file if coverage_selected else None,
                )
            )
        elif planned.check.id == "python.compat-runtime":
            checks.extend(
                expand_python_compat_runtime(
                    planned,
                    interpreter,
                    no_interpreter,
                    files,
                    component_root,
                    root,
                    unit,
                )
            )
        else:  # python.coverage
            checks.append(
                plan_coverage(
                    planned,
                    interpreter,
                    no_interpreter,
                    test_selected,
                    data_file,
                    report_path,
                    component_root,
                    unit,
                    sources=component_targets(component_root, root, files),
                )
            )
    return Plan(checks=tuple(checks))


def plan_type_check(
    planned: PlannedCheck,
    provider: MypyProvider | TyProvider | None,
    provider_name: str,
    component_root: Path,
    root: Path,
    files: tuple[str, ...],
    unit: AnalysisUnit | None,
) -> PlannedCheck:
    """Attach the chosen checker's task — never a substitute for it."""

    if provider is None:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked=f"unknown type_provider {provider_name!r}",
        )
    executable = locate_tool(provider_name)
    if executable is None:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked=f"{provider_name} is not available",
        )
    targets = component_targets(component_root, root, files)
    # input_refs are read against the task's cwd — the component root — so
    # they spell paths the way the argv does, not the workspace-relative way
    # the inventory does.
    task = (
        # mypy writes ``.mypy_cache`` where it runs unless pointed elsewhere —
        # under .ici, where a verification tool's state belongs. ty has no
        # cache flag to forward.
        provider.plan(
            executable,
            targets=targets,
            cwd=str(component_root),
            cache_dir=str(root / ".ici" / "cache" / "mypy"),
            task_id=planned.task_id,
            analysis_unit_id=unit.id if unit is not None else "",
            input_refs=targets,
        )
        if isinstance(provider, MypyProvider)
        else provider.plan(
            executable,
            targets=targets,
            cwd=str(component_root),
            task_id=planned.task_id,
            analysis_unit_id=unit.id if unit is not None else "",
            input_refs=targets,
        )
    )
    return PlannedCheck(check=planned.check, task_id=planned.task_id, task=task)
