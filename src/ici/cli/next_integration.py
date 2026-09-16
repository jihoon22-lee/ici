"""Turning declared integration cases into per-case tasks (#220).

A case only names what the workspace already declared: ``{python:NAME}``
resolves against the component's declared interpreter or its named
``python_targets``, and ``{artifact:BUILD/PATH}`` resolves only against a
path a linked build's ``artifacts`` contract already claims. A placeholder
that resolves to nothing is a blocked task with the reason named — never a
dropped case and never a guess at what was meant.

Resolution happens at plan time on purpose: ``ici next plan`` must show the
argv a run *would* execute, so a case whose artifact the build never left is
shown as blocked before anything runs rather than failing mid-run.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ici.adapters.providers.integration import IntegrationCaseProvider
from ici.application.plan import Plan, PlannedCheck
from ici.cli.next_testing import python_interpreter
from ici.config.composition import EffectiveComponent, EffectiveIntegrationCase
from ici.domain.workspace import BuildUnit, Component

__all__ = ["gate_integration"]

_PLACEHOLDER = re.compile(r"^\{(python|artifact):([^{}]+)\}$")
_SLUG_BAD = re.compile(r"[^a-z0-9._-]+")


def gate_integration(
    plan: Plan,
    component: Component,
    effective: EffectiveComponent,
    component_root: Path,
    root: Path,
    builds: tuple[BuildUnit, ...],
) -> Plan:
    """Expand the selected integration check into one task per case."""

    checks: list[PlannedCheck] = []
    for planned in plan.checks:
        if planned.check.id != "integration" or planned.blocked:
            checks.append(planned)
            continue
        checks.extend(_expand(planned, component, effective, component_root, root, builds))
    return Plan(checks=tuple(checks))


def _expand(
    planned: PlannedCheck,
    component: Component,
    effective: EffectiveComponent,
    component_root: Path,
    root: Path,
    builds: tuple[BuildUnit, ...],
) -> list[PlannedCheck]:
    provider = IntegrationCaseProvider()
    expanded: list[PlannedCheck] = []
    used: set[str] = set()
    for case in effective.integrations:
        slug = _slug(case.name, used)
        task_id = f"{planned.task_id}.{slug}"
        # The case's own ``required`` refines the check's: an advisory case
        # records findings without failing the gate even when the check is
        # required, and a required case under an advisory check stays
        # advisory — relaxing is allowed, tightening is the check's call.
        check = replace(planned.check, required=planned.check.required and case.required)
        argv, blocked = _resolve_argv(case, component, effective, component_root, root, builds)
        if blocked:
            expanded.append(PlannedCheck(check=check, task_id=task_id, blocked=blocked))
            continue
        task = provider.plan(
            tuple(argv),
            cwd=str(component_root),
            task_id=task_id,
            case_name=case.name,
            case_source=case.declared_in,
            case_required=case.required,
            expected_exit=case.expected_exit,
            stdout_contains=case.stdout_contains,
            stderr_contains=case.stderr_contains,
            stdout_not_contains=case.stdout_not_contains,
            stderr_not_contains=case.stderr_not_contains,
            outputs=tuple(
                (str((root / output.path).resolve()), output.min_size)
                for output in case.output_artifacts
            ),
            env=case.env,
            requires=case.requires,
            timeout_seconds=case.timeout_seconds,
        )
        expanded.append(PlannedCheck(check=check, task_id=task_id, task=task))
    if not expanded:
        expanded.append(
            PlannedCheck(
                check=planned.check,
                task_id=planned.task_id,
                blocked="no integration cases declared",
            )
        )
    return expanded


def _resolve_argv(
    case: EffectiveIntegrationCase,
    component: Component,
    effective: EffectiveComponent,
    component_root: Path,
    root: Path,
    builds: tuple[BuildUnit, ...],
) -> tuple[list[str], str]:
    """Resolve each token; the first failure names the whole case blocked."""

    resolved: list[str] = []
    targets = dict(case.python_targets)
    for index, token in enumerate(case.argv):
        match = _PLACEHOLDER.fullmatch(token)
        if match is None:
            resolved.append(token)
            continue
        kind, identity = match.groups()
        if kind == "python":
            value, blocked = _python_target(identity, targets, effective, component_root, root)
        else:
            value, blocked = _artifact(identity, component, root, builds)
        if blocked:
            return [], f"argv[{index}]: {blocked}"
        resolved.append(value)
    return resolved, ""


def _python_target(
    name: str,
    targets: dict[str, str],
    effective: EffectiveComponent,
    component_root: Path,
    root: Path,
) -> tuple[str, str]:
    """``{python:NAME}`` — a declared interpreter, never ici's own."""

    if name == "declared":
        interpreter = python_interpreter(effective, component_root)
        if interpreter is None:
            return "", (
                "no declared Python interpreter — set [python] executable or "
                "provide a .venv the case can run under"
            )
        return interpreter, ""
    target = targets.get(name)
    if target is None:
        declared = ", ".join(sorted((*targets, "declared")))
        return "", f"unknown python target {name!r}; declared: {declared}"
    if "/" not in target:
        found = shutil.which(target)
        return (found, "") if found else ("", f"python target {name!r} ({target}) is not on PATH")
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return "", f"python target {name!r} is unavailable: {target}"
    if not resolved.is_file():
        return "", f"python target {name!r} is not a file: {target}"
    return str(resolved), ""


def _artifact(
    identity: str,
    component: Component,
    root: Path,
    builds: tuple[BuildUnit, ...],
) -> tuple[str, str]:
    """``{artifact:BUILD/PATH}`` — a file a linked build's contract claims.

    The build must be one the component is linked to or consumes via
    ``needs``, the path must be matched by at least one declared artifact
    glob, and the file must exist — a case may only invoke what the
    workspace's contract already says the build produces.
    """

    build_id, _, relative = identity.partition("/")
    if not build_id or not relative:
        return "", f"artifact placeholder {identity!r} must spell BUILD/PATH"
    allowed = set(component.build_ids) | (set(component.needs) & {b.id for b in builds})
    if build_id not in allowed:
        known = ", ".join(sorted(allowed)) or "none are linked"
        return "", f"build {build_id!r} is not linked to this component; linked: {known}"
    build = next(item for item in builds if item.id == build_id)
    if not build.artifacts:
        return "", (
            f"build {build_id!r} declares no artifacts — add artifacts = [...] "
            "to [builds.<id>] so the case names a contracted output"
        )
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts:
        return "", f"artifact path {relative!r} must stay inside the build directory"
    base = root / build.directory
    declared = any(
        candidate.is_file() and candidate == base / relative
        for pattern in build.artifacts
        for candidate in base.glob(pattern)
    )
    if not declared:
        return "", (
            f"{relative!r} is not covered by build {build_id!r}'s artifact contract "
            f"({', '.join(build.artifacts)})"
        )
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return "", f"artifact {relative!r} resolves outside the workspace"
    return str(candidate), ""


def _slug(name: str, used: set[str]) -> str:
    """The case name as a task-id suffix — unique per component."""

    slug = _SLUG_BAD.sub("-", name.lower()).strip("-.") or "case"
    candidate, counter = slug, 2
    while candidate in used:
        candidate = f"{slug}-{counter}"
        counter += 1
    used.add(candidate)
    return candidate
