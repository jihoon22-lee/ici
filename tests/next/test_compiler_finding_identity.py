"""Compile variants use workspace coordinates without losing real options."""

import json

from ici.adapters.providers.compiler import CompilerDiagnosticsProvider
from ici.application.gates import expand_diagnostics
from ici.application.plan import Plan, PlannedCheck
from ici.application.verify import verify
from ici.domain.workspace import AnalysisUnit, Component
from ici.execution.process import Outcome, TaskOutcome
from ici.languages.checks import CheckDefinition
from ici.workspace.compile_units import load_compile_inputs


def _run(root, *, external=False):
    root.mkdir()
    compiler = root / "tools/g++"
    compiler.parent.mkdir()
    compiler.write_text("#!/bin/sh\nexit 0\n")
    compiler.chmod(0o755)
    source = root / "main.cpp"
    source.write_text("int value() { return missing; }\n")
    include = root.with_name(root.name + "-other") / "include" if external else root / "include"
    include.mkdir(parents=True)
    rows = [
        {
            "directory": str(root),
            "file": str(source),
            "arguments": [
                str(compiler),
                f"-I{include}",
                f'-DPROJECT_ROOT="{root}"',
                f"-DCHOICE={choice}",
                "-c",
                str(source),
                "-o",
                str(root / f"{choice}.o"),
            ],
        }
        for choice in ("one", "two")
    ]
    (root / "compile_commands.json").write_text(json.dumps(rows))
    inputs = load_compile_inputs(
        root, Component(id="app", root=".", languages=("cpp",)), ("main.cpp",)
    )
    check = CheckDefinition(
        id="cpp.diagnostics", title="Compiler diagnostics", language="cpp", tool="compiler"
    )
    plans = expand_diagnostics(
        PlannedCheck(check=check, task_id="app.cpp.diagnostics"),
        inputs,
        root,
        "missing",
        AnalysisUnit(id="app.cpp", component_id="app", language="cpp"),
    )
    assert all(item.task is not None for item in plans)

    def runner(spec):
        return TaskOutcome(
            spec=spec,
            outcome=Outcome.FINISHED,
            exit_code=1,
            stderr=f"{source}:1:22: error: missing was not declared\n",
        )

    result = verify(
        Plan(checks=tuple(plans)),
        providers={"compiler": CompilerDiagnosticsProvider()},
        analyses={},
        runner=runner,
        task_components={item.task_id: "app" for item in plans},
    )
    assert len(result.findings) == 2
    return {finding.variant: finding for finding in result.findings}


def test_absolute_cmake_commands_keep_variant_and_finding_identity_after_move(tmp_path):
    before = _run(tmp_path / "left")
    after = _run(tmp_path / "right")
    assert before == after


def test_external_include_paths_with_workspace_prefixes_remain_distinct(tmp_path):
    before = _run(tmp_path / "left", external=True)
    after = _run(tmp_path / "right", external=True)
    assert set(before).isdisjoint(after)
