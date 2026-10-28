"""Record the actual selection and completion used by baseline comparisons."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

from ici.adapters.providers.identity import invocation_identity
from ici.application.plan import Plan
from ici.application.planning import SOURCE_SUFFIXES, unit_files
from ici.application.verify import Verification
from ici.domain.check_record import CheckRecord
from ici.domain.enums import TaskState
from ici.domain.tasks import TaskSpec
from ici.domain.workspace import Workspace
from ici.workspace.inventory import SourceInventory


def records(
    plans: Mapping[str, Plan],
    verification: Verification,
    scope: Workspace,
    stock: SourceInventory,
    root: Path,
    *,
    inputs_stable: bool,
) -> tuple[CheckRecord, ...]:
    """Keep failed and blocked checks too; selection alone is never evidence."""

    observations = {item.task_id: item for item in verification.observations}
    result = []
    for component_id, plan in plans.items():
        external_paths = set(scope.component(component_id).external)
        for planned in plan.checks:
            units = tuple(
                unit
                for unit in scope.analysis_units
                if unit.component_id == component_id
                and (
                    unit.language == planned.check.language
                    or planned.check.language not in SOURCE_SUFFIXES
                )
            )
            files = sorted(
                {
                    path
                    for unit in units
                    for path in unit_files(stock, unit, SOURCE_SUFFIXES.get(unit.language, ("",)))
                }
            )
            task = planned.task.task if planned.task is not None else None
            cwd, argv = (
                invocation_identity(task.argv, Path(task.cwd), root)
                if task is not None
                else (None, ())
            )
            context = {
                "files": files,
                "units": [
                    (
                        unit.id,
                        unit.variant,
                        _workspace_reference(unit.runtime, root) if unit.runtime else None,
                        unit.build_id,
                    )
                    for unit in sorted(units, key=lambda item: item.id)
                ],
                "externals": sorted(
                    {
                        (
                            _workspace_reference(item.path, root),
                            tuple(sorted(_workspace_reference(path, root) for path in item.files)),
                            item.present,
                        )
                        for item in stock.externals
                        if item.path in external_paths
                    }
                ),
                "builds": _linked_builds(scope, component_id, root),
                "invocation": None
                if task is None
                else {
                    "argv": argv,
                    "cwd": cwd,
                    "provider": task.provider,
                    "inputs": [_workspace_reference(item, root) for item in task.input_refs],
                    "configuration": _configuration_digests(task),
                    "environment": _environment(task, root),
                    "units": task.analysis_unit_ids,
                },
            }
            digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
            observed = observations.get(planned.task_id)
            result.append(
                CheckRecord(
                    task_id=planned.task_id,
                    check_id=planned.check.id,
                    component_id=component_id,
                    language=planned.check.language,
                    scope_digest=f"sha256:{digest}",
                    state=observed.state if observed is not None else TaskState.BLOCKED,
                    evidence_complete=bool(
                        inputs_stable
                        and observed is not None
                        and observed.evidence_is_complete
                        # Cargo target and tsconfig selection are tool-owned. Until
                        # their complete native file closure is recorded, a missing
                        # finding cannot prove that an older one was resolved.
                        and (task is None or task.provider not in {"clippy", "tsc"})
                    ),
                    analysis_unit_id=units[0].id if len(units) == 1 else None,
                    variant=(task.variant if task is not None else None)
                    or (units[0].variant if len(units) == 1 else None),
                )
            )
    return tuple(result)


def _configuration_digests(task: TaskSpec) -> tuple[tuple[str, str], ...]:
    """Changed native configuration cannot masquerade as a resolved finding."""
    found = []
    for ref in task.input_refs:
        path = Path(task.cwd) / ref
        if path.name not in {"ruff.toml", ".ruff.toml", "pyproject.toml", "mypy.ini", "setup.cfg"}:
            continue
        try:
            found.append((ref, hashlib.sha256(path.read_bytes()).hexdigest()))
        except OSError:
            found.append((ref, "unreadable"))
    return tuple(sorted(found))


def _linked_builds(
    scope: Workspace, component_id: str, root: Path
) -> tuple[tuple[object, ...], ...]:
    component = scope.component(component_id)
    selected = set(component.build_ids) | set(scope.build_needs(component_id))
    return tuple(
        (
            build.id,
            build.system,
            build.variant,
            build.directory,
            build.definition,
            invocation_identity(build.prepare_argv, root / build.directory, root),
            build.artifacts,
        )
        for build in sorted(scope.builds, key=lambda item: item.id)
        if build.id in selected
    )


def _workspace_reference(value: str, root: Path) -> str:
    """Relocate contained paths without aliasing similarly prefixed externals."""

    path = Path(os.path.normpath(value))
    if not path.is_absolute():
        return value
    try:
        relative = path.relative_to(root)
    except ValueError:
        return value
    return "${workspace}" if relative == Path(".") else "${workspace}/" + relative.as_posix()


def _environment(task: TaskSpec, root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, _environment_value(task.provider, key, value, root))
        for key, value in task.env_overlay
        if task.provider != "integration" or key != "ICI_OUT_NOT_BEFORE"
    )


def _environment_value(provider: str, key: str, value: str, root: Path) -> str:
    if provider != "integration" or key != "ICI_OUTPUT_ASSERTS":
        return _workspace_reference(value, root)
    try:
        outputs = json.loads(value)
    except ValueError:
        return value
    if not isinstance(outputs, list):
        return value
    normalized = [
        {**item, "path": _workspace_reference(item["path"], root)}
        if isinstance(item, dict) and isinstance(item.get("path"), str)
        else item
        for item in outputs
    ]
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))
