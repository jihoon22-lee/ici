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
from ici.domain.enums import GateVerdict, TaskKind
from ici.domain.finding import Finding, SourceSpan
from ici.domain.tasks import TaskSpec
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


@pytest.mark.parametrize("owners", [("a", "b"), ("a", "a")])
def test_shared_execution_preserves_consumer_component_and_variant(owners):
    finding = Finding(
        fingerprint="native",
        rule_id="stub.rule",
        message="problem",
        severity="high",
        confidence="high",
        provider="stub",
        primary_location=SourceSpan(path="main.py", start_line=1),
        component_id="a",
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
                    check=check, task=ProviderPlan(task=replace(task, id=f"{c}.lint", variant=c))
                ),
            )
        )
        for c in ("a", "b")
    )
    result = verify(
        plans,
        providers={"stub": Provider()},
        analyses={},
        runner=runner,
        task_components=dict(zip(("a.lint", "b.lint"), owners, strict=True)),
    )
    assert len(calls) == 1
    assert result.gate.selected is GateVerdict.FAIL
    assert {f.component_id for f in result.findings} == set(owners)
    assert {f.variant for f in result.findings} == {"a", "b"}
    assert len({f.fingerprint for f in result.findings}) == 2
