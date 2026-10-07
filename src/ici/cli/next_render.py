"""``ici next`` output shaping — the presentation half of the commands.

Machine output and human output live here so the command module holds only
the request→scope→plan→run flow. Nothing in this module executes a task or
reads the filesystem beyond what its arguments carry; it turns plans and
stored results into stdout text or a document.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ici.application.plan import Plan, PlannedCheck
from ici.application.planning import describe
from ici.application.request import RunRequest, requested_languages
from ici.domain.enums import BaselineState, Profile
from ici.domain.serialization import dumps
from ici.domain.workspace import Workspace


def linked_builds(scope: Workspace) -> list[dict[str, object]]:
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


def plan_json(
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
        if planned.task.task.work_dirs:
            entry["work_dirs"] = list(planned.task.task.work_dirs)
    if planned.blocked:
        entry["blocked"] = planned.blocked
    return entry


def plan_text(
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
            typer.echo(describe(planned))
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


def verify_text(stored, verification, limitations: list[str], target: Path, exit_code: int) -> None:
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
