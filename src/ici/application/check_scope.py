"""Record the actual selection and completion used by baseline comparisons."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

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
                "externals": [(item.path, item.files, item.present) for item in stock.externals],
                "invocation": None
                if task is None
                else {
                    "argv": [arg.replace(str(root), "${workspace}") for arg in task.argv],
                    "cwd": task.cwd.replace(str(root), "${workspace}"),
                    "provider": task.provider,
                    "inputs": [_workspace_reference(item, root) for item in task.input_refs],
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
                        inputs_stable and observed is not None and observed.evidence_is_complete
                    ),
                    analysis_unit_id=units[0].id if len(units) == 1 else None,
                    variant=(task.variant if task is not None else None)
                    or (units[0].variant if len(units) == 1 else None),
                )
            )
    return tuple(result)


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
