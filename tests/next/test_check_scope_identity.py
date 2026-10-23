"""Completed evidence stays tied to its analysis conditions across checkouts."""

from dataclasses import replace
from pathlib import Path

import pytest

from ici.adapters.providers.base import ProviderPlan
from ici.application.baseline import compare, load_baseline
from ici.application.check_scope import records
from ici.application.plan import Plan, PlannedCheck
from ici.application.verify import Verification
from ici.domain.enums import GateVerdict, TaskKind, TaskState
from ici.domain.finding import Finding, SourceSpan
from ici.domain.observation import Observation
from ici.domain.result import FINGERPRINT_VERSION, GateOutcome
from ici.domain.serialization import dumps, loads, run_result_to_dict
from ici.domain.tasks import TaskSpec
from ici.domain.workspace import AnalysisUnit, BuildUnit, Component, Workspace
from ici.languages.checks import CheckDefinition
from ici.workspace.inventory import take


def _record(
    root,
    *,
    variant="debug",
    runtime=None,
    build_id="build",
    reference=None,
    executable="stub",
    external_directory=False,
):
    root.mkdir(exist_ok=True)
    (root / "main.cpp").write_text("int main() { return 0; }\n")
    if external_directory:
        (root / "assets").mkdir(exist_ok=True)
        (root / "assets/data.txt").write_text("data\n")
    workspace = Workspace(
        id="example",
        name="example",
        components=(
            Component(
                id="app",
                root=".",
                languages=("cpp",),
                sources=("main.cpp",),
                build_ids=(build_id,),
                external=("assets",) if external_directory else (),
            ),
        ),
        builds=(BuildUnit(id=build_id, system="cmake", variant=variant, directory="build"),),
        analysis_units=(
            AnalysisUnit(
                id="app.cpp",
                component_id="app",
                language="cpp",
                variant=variant,
                runtime=runtime,
                build_id=build_id,
            ),
        ),
    )
    task = TaskSpec(
        id="app.cpp.binary_compat",
        kind=TaskKind.ANALYZE,
        provider="stub",
        argv=(executable, str(root / "build/app")),
        cwd=str(root),
        input_refs=(reference or str(root / "build/app"),),
        env_overlay=(("WORK_DIR", str(root / "work")),),
        analysis_unit_ids=("app.cpp",),
    )
    plan = Plan(
        checks=(
            PlannedCheck(
                check=CheckDefinition(
                    id="cpp.binary_compat",
                    title="Binary compatibility",
                    language="cpp",
                    tool="stub",
                ),
                task=ProviderPlan(task=task),
            ),
        )
    )
    verification = Verification(
        gate=GateOutcome(selected=GateVerdict.PASS),
        observations=(Observation(task_id=task.id, provider="stub", state=TaskState.SUCCEEDED),),
        findings=(),
    )
    return records(
        {"app": plan}, verification, workspace, take(workspace, root=root), root, inputs_stable=True
    )[0]


def _delta(tmp_path, old, new):
    fixture = Path(__file__).parents[1] / "fixtures/ici-next/run-success.json"
    result = loads(fixture.read_text())
    finding = Finding(
        fingerprint="old-problem",
        rule_id="stub.rule",
        message="problem",
        severity="high",
        confidence="high",
        provider="stub",
        primary_location=SourceSpan(path="main.cpp", start_line=1),
        component_id="app",
        analysis_unit_id=old.analysis_unit_id,
        variant=old.variant,
        task_id=old.task_id,
    )
    result = replace(
        result,
        schema_version=2,
        checks=(old,),
        findings=(finding,),
        identity=replace(result.identity, fingerprint_version=FINGERPRINT_VERSION),
    )
    path = tmp_path / "baseline.json"
    path.write_text(dumps(run_result_to_dict(result)))
    return compare(
        (),
        load_baseline(path),
        policy_digest=result.identity.policy_digest,
        toolchain_digest=result.identity.toolchain_digest,
        checks=(new,),
    )


@pytest.mark.parametrize(
    "changed", [{"variant": "release"}, {"runtime": "/toolchain/other"}, {"build_id": "other"}]
)
def test_changed_analysis_conditions_cannot_resolve_a_previous_finding(tmp_path, changed):
    old = _record(tmp_path / "project")
    new = _record(tmp_path / "project", **changed)
    delta = _delta(tmp_path, old, new)
    assert delta.resolved == ()
    assert delta.carried == ("old-problem",)
    assert old.variant == "debug"
    assert new.variant == changed.get("variant", "debug")


def test_relocated_checkout_retains_completed_scope_for_absolute_inputs(tmp_path):
    old = _record(tmp_path / "left")
    new = _record(tmp_path / "right")
    assert old.scope_digest == new.scope_digest
    delta = _delta(tmp_path, old, new)
    assert delta.resolved == ("old-problem",)
    assert delta.carried == ()


def test_external_paths_with_a_workspace_name_prefix_remain_distinct(tmp_path):
    old = _record(tmp_path / "left", reference=str(tmp_path / "left-other/app"))
    new = _record(tmp_path / "right", reference=str(tmp_path / "right-other/app"))
    delta = _delta(tmp_path, old, new)
    assert delta.resolved == ()
    assert delta.carried == ("old-problem",)


def test_workspace_external_directory_members_relocate_with_the_checkout(tmp_path):
    old = _record(tmp_path / "left", external_directory=True)
    new = _record(tmp_path / "right", external_directory=True)
    delta = _delta(tmp_path, old, new)
    assert delta.resolved == ("old-problem",)
    assert delta.carried == ()


def test_external_argv_prefixes_cannot_resolve_a_different_invocation(tmp_path):
    old = _record(tmp_path / "left", executable=str(tmp_path / "left-other/tool"))
    new = _record(tmp_path / "right", executable=str(tmp_path / "right-other/tool"))
    delta = _delta(tmp_path, old, new)
    assert delta.resolved == ()
    assert delta.carried == ("old-problem",)
