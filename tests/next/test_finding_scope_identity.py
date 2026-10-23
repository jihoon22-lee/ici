"""Finding identity must retain every consumer's policy scope."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.adapters.providers.base import ParsedOutput, ProviderPlan
from ici.application.plan import Plan, PlannedCheck
from ici.application.verify import verify
from ici.config.composition import EffectiveSuppression
from ici.domain.enums import GateVerdict, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
from ici.execution.cache import ObservationCache
from ici.execution.process import Outcome, TaskOutcome
from ici.languages.checks import CheckDefinition
from ici.languages.python.checks import PYTHON_CHECKS


@pytest.mark.parametrize("shared_root", [False, True])
def test_suppression_never_crosses_component_ownership(tmp_path, monkeypatch, shared_root):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.chdir(tmp_path)
    parts = ['schema_version=1\n[workspace]\nname="example"\n']
    for name in ("a", "b"):
        folder = "shared" if shared_root else name
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / "main.py").write_text("value = missing\n")
        parts.append(f'[[components]]\nid="{name}"\nroot="{folder}"\nlanguages=["python"]\n')
    parts.extend(
        f'[checks."{c.id}"]\nenabled={str(c.id == "python.lint").lower()}\nrequired=true\n'
        for c in PYTHON_CHECKS
    )
    parts.append('[[suppressions]]\nrule="ruff.F821"\ncomponent="a"\nreason="a only"\n')
    (tmp_path / "ici.toml").write_text("\n".join(parts))
    runner = CliRunner()
    result = runner.invoke(app, ["next", "verify", "--json"])
    assert result.exit_code == 1, result.output
    findings = json.loads(result.stdout)["findings"]
    assert len(findings) == len({f["fingerprint"] for f in findings}) == 2
    by_component = {f["component_id"]: f for f in findings}
    assert by_component["a"]["suppression"]["suppressed"]
    assert "suppression" not in by_component["b"]
    assert by_component["b"]["task_id"] == "b.python.lint"
    alone = runner.invoke(app, ["next", "verify", "--json", "--component", "b"])
    assert (
        json.loads(alone.stdout)["findings"][0]["fingerprint"] == by_component["b"]["fingerprint"]
    )


@pytest.mark.parametrize(
    ("owners", "identity"),
    [
        (("a", "b"), "variant"),
        (("a", "a"), "variant"),
        (None, "variant"),
        (None, "unit"),
        (None, "units"),
    ],
)
def test_shared_and_cached_execution_preserves_each_consumer(tmp_path, owners, identity):
    finding = Finding(
        fingerprint="native",
        rule_id="stub.rule",
        message="problem",
        severity="high",
        confidence="high",
        provider="stub",
        primary_location=SourceSpan(path="main.py", start_line=1),
        component_id="a" if owners else None,
        analysis_unit_id="a.python" if identity == "units" else None,
        task_id="a.lint",
    )
    calls = []

    class Provider:
        name = "stub"

        def parse(self, outcome):
            return ParsedOutput(findings=(finding,))

    def runner(spec):
        calls.append(spec.name)
        return TaskOutcome(spec=spec, outcome=Outcome.FINISHED, exit_code=0)

    check = CheckDefinition(id="python.lint", title="Lint", language="python", tool="stub")
    task = TaskSpec(
        id="a.lint", kind=TaskKind.ANALYZE, provider="stub", argv=("stub",), cwd="/workspace"
    )
    plans = tuple(
        Plan(
            checks=(
                PlannedCheck(
                    check=check,
                    task=ProviderPlan(
                        task=replace(
                            task,
                            id=f"{c}.lint",
                            variant=c if identity == "variant" else None,
                            analysis_unit_ids=(f"{c}.python",)
                            if identity == "unit"
                            else (f"{c}.python", f"{c}.extra")
                            if identity == "units"
                            else (),
                        )
                    ),
                ),
            )
        )
        for c in ("a", "b")
    )
    kwargs = dict(
        providers={"stub": Provider()},
        analyses={},
        runner=runner,
        cache=ObservationCache(tmp_path / "cache"),
        identify=lambda unit: ("a" * 64, ""),
        task_components=dict(zip(("a.lint", "b.lint"), owners, strict=True)) if owners else None,
    )
    result = verify(plans, **kwargs)
    assert len(calls) == 1
    assert result.gate.selected is GateVerdict.FAIL
    assert {f.component_id for f in result.findings} == (set(owners) if owners else {None})
    assert {f.task_id for f in result.findings} == {"a.lint", "b.lint"}
    if identity == "variant":
        assert {f.variant for f in result.findings} == {"a", "b"}
    elif identity == "unit":
        assert {f.analysis_unit_id for f in result.findings} == {"a.python", "b.python"}
    assert len({f.fingerprint for f in result.findings}) == 2
    by_task = {f.task_id: f for f in result.findings}

    # The persisted native observation belongs to A; a partial B run must rebind it.
    warm = verify(plans[1], **kwargs)
    assert len(calls) == 1
    assert warm.executions[0].detail.startswith("cache hit")
    assert warm.findings == (by_task["b.lint"],)
    if identity == "units":
        assert warm.findings[0].analysis_unit_id is None
        planned = plans[1].checks[0]
        reordered = replace(
            planned.task.task,
            analysis_unit_ids=tuple(reversed(planned.task.task.analysis_unit_ids)),
        )
        reordered_plan = Plan(checks=(replace(planned, task=ProviderPlan(task=reordered)),))
        assert verify(reordered_plan, **kwargs).findings == warm.findings
    suppression = EffectiveSuppression(
        fingerprint=by_task["a.lint"].fingerprint,
        rule="",
        path="",
        component="",
        reason="accepted for A only",
        declared_in="ici.toml",
    )
    suppressed = verify(plans, suppressions=(suppression,), **kwargs)
    assert suppressed.gate.selected is GateVerdict.FAIL
    assert sum(f.suppression.suppressed for f in suppressed.findings) == 1
