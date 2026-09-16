"""The test-evidence half of the plan gates — split so ``next_common`` stays small.

Everything here answers one of two questions at plan time: *which interpreter
runs the Python suite*, and *which built artifacts carry a C++ suite's
verdict*. Nothing here executes — a missing interpreter, suite or
instrumentation note becomes a blocked marker naming what would fix it, never
a quiet substitute or an empty pass (#216, #219).

The leaf helpers ``locate_tool`` and ``component_targets`` live here too:
the module is a leaf of the cli package's import graph — ``next_common``
imports them back with their private names — because the two gates and these
planners share exactly this vocabulary and nothing else.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ici.adapters.providers.binarycompat import BinaryCompatProvider
from ici.adapters.providers.coverage import CoverageProvider
from ici.adapters.providers.cpptest import CtestProvider, QtestProvider
from ici.adapters.providers.gcov import GcovProvider
from ici.adapters.providers.pycompat import CompileallProvider, PythonVersionProvider
from ici.adapters.providers.pytest import PytestProvider
from ici.adapters.providers.sanitize import SanitizeProvider
from ici.application.plan import PlannedCheck
from ici.config.composition import EffectiveComponent
from ici.domain.workspace import AnalysisUnit, BuildUnit, Component
from ici.engines._python_compatibility import PythonMetadataError
from ici.languages.compat import declared_python_floor
from ici.workspace.instrumentation import ctest_binaries, is_elf, sanitizer_marked
from ici.workspace.test_suites import TestSuite, suites_for_build

__all__ = [
    "component_targets",
    "cpp_gcno",
    "cpp_suites",
    "expand_cpp_binary_compat",
    "expand_cpp_sanitizer",
    "expand_cpp_tests",
    "expand_python_compat_runtime",
    "locate_tool",
    "plan_coverage",
    "plan_cpp_coverage",
    "plan_test",
    "python_interpreter",
    "test_targets",
]

#: Where a bundle keeps the analyzers it shipped, relative to its root.
BUNDLED_TOOLS = Path("tools") / "python-static"


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def locate_tool(tool: str) -> str | None:
    """Where a tool is, asked once, here.

    **Running from a bundle, it is the bundle's copy or nothing.** #204 item 7:
    an analyzer taken from PATH makes the result depend on what else is
    installed on the machine, which is the property an offline release exists
    to remove. Falling back to PATH here would mean a bundle missing its ruff
    quietly linted with whatever the host had, and the report would not say so.

    Running from a source checkout there is no bundle to prefer, so PATH is the
    honest answer and the developer gets the tool they installed.

    The bundle is found through ``ICI_BUNDLE_ROOT``, which its launcher exports.
    A symlink to the interpreter inside ``tools/python-static/`` is deliberately
    acceptable — python-static is *the* bundled Python, not a vendored copy, so
    inside a real bundle it would have found ``mypy`` there anyway.
    """

    root = os.environ.get("ICI_BUNDLE_ROOT")
    if root:
        shipped = Path(root) / BUNDLED_TOOLS / tool
        return str(shipped) if _runnable(shipped) else None
    return shutil.which(tool)


def component_targets(component_root: Path, root: Path, files: tuple[str, ...]) -> tuple[str, ...]:
    """The unit's real files as tool targets, spelled relative to its root.

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
    return tuple(dict.fromkeys(targets))


# --- python: which interpreter answers for the suite ----------------------


def python_interpreter(effective: EffectiveComponent | None, component_root: Path) -> str | None:
    """The interpreter a component's tests run under, or None.

    The declared ``[python] executable`` wins; a ``.venv`` inside the
    component root is the discoverable project environment. ici's own
    interpreter is never a candidate — a test run under the verifier's
    runtime measures the wrong packages (#216).
    """

    if effective is not None and effective.python_executable is not None:
        return effective.python_executable.value
    venv_python = component_root / ".venv" / "bin" / "python"
    if venv_python.is_file():
        return str(venv_python)
    return None


def plan_test(
    planned: PlannedCheck,
    interpreter: str | None,
    no_interpreter: str,
    effective: EffectiveComponent | None,
    component_root: Path,
    root: Path,
    unit: AnalysisUnit | None,
    coverage_data: str | None,
) -> PlannedCheck:
    """Plan the pytest run — coverage-wrapped when the coverage check rides it."""

    if interpreter is None:
        return PlannedCheck(check=planned.check, task_id=planned.task_id, blocked=no_interpreter)
    targets = test_targets(effective, component_root, root)
    if effective is not None and effective.test_paths and not targets:
        # A declared test scope that matches nothing is not a suite that
        # passed quietly — it is a scope the run cannot find.
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="declared test_paths matched no files",
        )
    task = PytestProvider().plan(
        interpreter,
        targets=targets,
        cwd=str(component_root),
        task_id=planned.task_id,
        coverage_data=coverage_data,
        analysis_unit_id=unit.id if unit is not None else "",
        input_refs=targets,
    )
    return PlannedCheck(check=planned.check, task_id=planned.task_id, task=task)


def test_targets(
    effective: EffectiveComponent | None, component_root: Path, root: Path
) -> tuple[str, ...]:
    """Expand declared ``test_paths`` globs into component-relative argv.

    pytest cannot expand a workspace glob itself — handed ``tests/**/*.py``
    literally it errors rather than collecting. The declared patterns are
    expanded here so the argv names real paths the suite owns; nothing
    declared means the component's whole root is pytest's scope.
    """

    if effective is None or not effective.test_paths:
        return ()
    expanded: list[str] = []
    for pattern in effective.test_paths:
        relative = component_targets(component_root, root, (pattern,))
        for item in relative:
            for match in sorted(component_root.glob(item)):
                expanded.append(match.relative_to(component_root).as_posix())
    return tuple(dict.fromkeys(expanded))


def plan_coverage(
    planned: PlannedCheck,
    interpreter: str | None,
    no_interpreter: str,
    test_selected: bool,
    data_file: str,
    report_path: str,
    component_root: Path,
    unit: AnalysisUnit | None,
    sources: tuple[str, ...] = (),
) -> PlannedCheck:
    """Plan the coverage read — it shares the test run, never repeats it."""

    if not test_selected:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="python.coverage reads the test run's data — enable python.test",
        )
    if interpreter is None:
        return PlannedCheck(check=planned.check, task_id=planned.task_id, blocked=no_interpreter)
    task = CoverageProvider().plan(
        interpreter,
        data_file=data_file,
        report_path=report_path,
        cwd=str(component_root),
        task_id=planned.task_id,
        analysis_unit_id=unit.id if unit is not None else "",
        sources=sources,
    )
    return PlannedCheck(check=planned.check, task_id=planned.task_id, task=task)


# --- c++: which built artifacts carry the suite's verdict -----------------


def cpp_suites(
    root: Path, component: Component, builds: tuple[BuildUnit, ...]
) -> tuple[TestSuite, ...]:
    """The suites the component's linked builds declare — built or not."""

    return tuple(
        suite
        for build in builds
        if build.id in component.build_ids
        for suite in suites_for_build(root, build)
    )


def cpp_gcno(root: Path, component: Component, builds: tuple[BuildUnit, ...]) -> tuple[str, ...]:
    """Instrumentation notes under the linked build dirs — proof the build
    was compiled with coverage, and what the test run's ``.gcda`` join to."""

    found: list[str] = []
    for build in builds:
        if build.id not in component.build_ids:
            continue
        build_dir = root / build.directory
        if build_dir.is_dir():
            found.extend(str(path) for path in sorted(build_dir.rglob("*.gcno")))
    return tuple(found)


def expand_cpp_tests(
    planned: PlannedCheck,
    suites: tuple[TestSuite, ...],
    component: Component,
    component_root: Path,
    root: Path,
    cpp_unit: AnalysisUnit | None,
) -> list[PlannedCheck]:
    """One task per declared suite — a missing binary is blocked, not empty."""

    if not component.build_ids:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no build unit linked — declare [builds.<id>] and "
                "attach it to the component",
            )
        ]
    if not suites:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no declared test suite — cmake add_test or a "
                "testlib/testcase .pro is what declares one",
            )
        ]
    expanded: list[PlannedCheck] = []
    for suite in suites:
        task_id = f"{planned.task_id}.{suite.id}"
        build_dir = root / suite.build_dir
        if suite.kind == "ctest":
            executable = locate_tool("ctest")
            if executable is None:
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked="ctest is not available",
                    )
                )
                continue
            if not (build_dir / "CTestTestfile.cmake").is_file():
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked="test suite not built — no CTestTestfile.cmake under "
                        f"{suite.build_dir}",
                    )
                )
                continue
            plan = CtestProvider().plan(
                executable,
                build_dir=str(build_dir),
                cwd=str(component_root),
                task_id=task_id,
                analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
            )
            expanded.append(PlannedCheck(check=planned.check, task_id=task_id, task=plan))
        else:  # qtest
            binary = build_dir / suite.binary
            if not binary.is_file():
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked=f"test binary not built — {suite.binary} is absent "
                        f"under {suite.build_dir}",
                    )
                )
                continue
            plan = QtestProvider().plan(
                binary=str(binary),
                cwd=str(component_root),
                task_id=task_id,
                analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
            )
            expanded.append(PlannedCheck(check=planned.check, task_id=task_id, task=plan))
    return expanded


def expand_python_compat_runtime(
    planned: PlannedCheck,
    interpreter: str | None,
    no_interpreter: str,
    files: tuple[str, ...],
    component_root: Path,
    root: Path,
    unit: AnalysisUnit | None,
) -> list[PlannedCheck]:
    """Two measured answers from the declared interpreter (#220 item 5).

    ``-VV`` proves which runtime the project declared; ``compileall`` proves
    that runtime accepts the component's sources. Neither is the static
    scan's job, and a component with no interpreter gets blocked, not
    silently judged by the AST tables alone.
    """

    if interpreter is None:
        return [PlannedCheck(check=planned.check, task_id=planned.task_id, blocked=no_interpreter)]
    targets = tuple(
        item for item in component_targets(component_root, root, files) if item.endswith(".py")
    )
    if not targets:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no Python sources in the component's scope",
            )
        ]
    try:
        floor = declared_python_floor(component_root, root)
    except PythonMetadataError as error:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked=f"python metadata unreadable: {error}",
            )
        ]
    pycache = root / ".ici" / "cache" / "pycache" / "compat"
    pycache.mkdir(parents=True, exist_ok=True)
    version = PythonVersionProvider().plan(
        interpreter,
        cwd=str(component_root),
        task_id=f"{planned.task_id}.version",
        requires_python=floor,
        analysis_unit_id=unit.id if unit is not None else "",
    )
    compileall = CompileallProvider().plan(
        interpreter,
        files=targets,
        cwd=str(component_root),
        task_id=f"{planned.task_id}.compileall",
        analysis_unit_id=unit.id if unit is not None else "",
        pycache_prefix=str(pycache),
    )
    return [
        PlannedCheck(check=planned.check, task_id=version.task.id, task=version),
        PlannedCheck(check=planned.check, task_id=compileall.task.id, task=compileall),
    ]


def expand_cpp_binary_compat(
    planned: PlannedCheck,
    component: Component,
    root: Path,
    builds: tuple[BuildUnit, ...],
    cpp_unit: AnalysisUnit | None,
) -> list[PlannedCheck]:
    """One readelf task per ELF artifact the linked builds declare (#220).

    The targets come from the artifact contract — the same globs
    ``cpp.artifact`` verifies — so a component without a contract has
    nothing to inspect and is blocked, not vacuously passed.
    """

    if not component.build_ids:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no build unit linked — artifact declarations live on [builds.<id>]",
            )
        ]
    linked = [build for build in builds if build.id in component.build_ids]
    if not any(build.artifacts for build in linked):
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no artifact contract — declare [builds.<id>] artifacts = [...] "
                "so the binaries to inspect are named",
            )
        ]
    executable = locate_tool("readelf")
    if executable is None:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="readelf is not available",
            )
        ]
    provider = BinaryCompatProvider(project_root=root)
    expanded: list[PlannedCheck] = []
    inspected = 0
    for build in linked:
        build_dir = root / build.directory
        for pattern in build.artifacts:
            for match in sorted(build_dir.glob(pattern)):
                if not match.is_file() or is_elf(match) is not True:
                    continue
                inspected += 1
                plan = provider.plan(
                    executable,
                    binary=match,
                    task_id=f"{planned.task_id}.{build.id}-{inspected}",
                    analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
                )
                expanded.append(PlannedCheck(check=planned.check, task_id=plan.task.id, task=plan))
    if not expanded:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="the artifact contract matched no ELF binaries — "
                "nothing for binary compatibility to inspect",
            )
        ]
    return expanded


def expand_cpp_sanitizer(
    planned: PlannedCheck,
    variant: str,
    suites: tuple[TestSuite, ...],
    component: Component,
    component_root: Path,
    root: Path,
    builds: tuple[BuildUnit, ...],
    cpp_unit: AnalysisUnit | None,
) -> list[PlannedCheck]:
    """One task per suite the variant build produced — unbuilt is blocked.

    The variant declaration is the project's claim that a build was compiled
    with the instrumentation; the claim is checked against the markers the
    runtime leaves in each binary. A suite whose binaries carry no marker is
    not silently run anyway — it is blocked with the reason, because running
    an uninstrumented binary under sanitizer env vars proves nothing (#220).
    """

    if not component.build_ids:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no build unit linked — declare [builds.<id>] and "
                "attach it to the component",
            )
        ]
    variant_ids = {
        build.id for build in builds if build.id in component.build_ids and build.variant == variant
    }
    if not variant_ids:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked=f'no {variant} build — declare [builds.<id>] variant = "{variant}" '
                "and link it to the component",
            )
        ]
    variant_suites = tuple(suite for suite in suites if suite.build_id in variant_ids)
    if not variant_suites:
        return [
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked=f"no declared test suite under the {variant} build — a "
                "sanitizer check needs a suite to run",
            )
        ]
    provider = SanitizeProvider(variant, project_root=root)
    expanded: list[PlannedCheck] = []
    for suite in variant_suites:
        task_id = f"{planned.task_id}.{suite.id}"
        build_dir = root / suite.build_dir
        if suite.kind == "ctest":
            executable = locate_tool("ctest")
            if executable is None:
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked="ctest is not available",
                    )
                )
                continue
            if not (build_dir / "CTestTestfile.cmake").is_file():
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked="test suite not built — no CTestTestfile.cmake under "
                        f"{suite.build_dir}",
                    )
                )
                continue
            binaries = ctest_binaries(root, suite.build_dir)
            marked = [item for item in binaries if sanitizer_marked(item, variant)]
            if not binaries:
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked=f"the suite names no test executables under {suite.build_dir}",
                    )
                )
                continue
            if not marked:
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked=f"no {variant} instrumentation in the suite's "
                        f"binaries — the {suite.build_dir} build was not "
                        "compiled for it",
                    )
                )
                continue
            plan = provider.plan(
                argv=(executable, "--test-dir", str(build_dir), "--output-on-failure"),
                cwd=str(component_root),
                task_id=task_id,
                analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
                input_refs=tuple(str(item) for item in binaries),
            )
            expanded.append(PlannedCheck(check=planned.check, task_id=task_id, task=plan))
        else:  # qtest
            binary = build_dir / suite.binary
            if not binary.is_file():
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked=f"test binary not built — {suite.binary} is absent "
                        f"under {suite.build_dir}",
                    )
                )
                continue
            marked_binary = sanitizer_marked(binary, variant)
            if marked_binary is not True:
                expanded.append(
                    PlannedCheck(
                        check=planned.check,
                        task_id=task_id,
                        blocked=(
                            f"no {variant} instrumentation in {suite.binary} — the "
                            "build was not compiled for it"
                            if marked is False
                            else f"{variant} instrumentation of {suite.binary} "
                            "could not be verified — the binary is unreadable "
                            "or too large to scan"
                        ),
                    )
                )
                continue
            plan = provider.plan(
                argv=(str(binary),),
                cwd=str(component_root),
                task_id=task_id,
                analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
                input_refs=(str(binary),),
            )
            expanded.append(PlannedCheck(check=planned.check, task_id=task_id, task=plan))
    return expanded


def plan_cpp_coverage(
    planned: PlannedCheck,
    test_selected: bool,
    gcno_files: tuple[str, ...],
    component: Component,
    root: Path,
    cpp_unit: AnalysisUnit | None,
) -> PlannedCheck:
    """gcov reads what the shared instrumented run left — it never re-runs it."""

    if not test_selected:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="cpp.coverage reads the test run's data — enable cpp.test",
        )
    if not gcno_files:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="build is not instrumented — no .gcno under the linked build directories",
        )
    executable = locate_tool("gcov")
    if executable is None:
        return PlannedCheck(
            check=planned.check,
            task_id=planned.task_id,
            blocked="gcov is not available",
        )
    work_dir = root / ".ici" / "cache" / "gcov" / component.id
    # gcov writes reports where it runs — under .ici, not in the build tree.
    work_dir.mkdir(parents=True, exist_ok=True)
    plan = GcovProvider().plan(
        executable,
        gcno_files=gcno_files,
        work_dir=str(work_dir),
        task_id=planned.task_id,
        analysis_unit_id=cpp_unit.id if cpp_unit is not None else "",
    )
    return PlannedCheck(check=planned.check, task_id=planned.task_id, task=plan)
