"""Required AST analyses must retain partial evidence without claiming completion."""

from dataclasses import replace

import pytest

from ici.analysis._python_resources import ResourceAnalysisLimit
from ici.application.plan import Plan, PlannedCheck
from ici.application.verify import verify
from ici.domain.enums import GateVerdict, TaskState
from ici.languages.hygiene import HygieneRequest, measure_hygiene
from ici.languages.metrics import MetricRequest, measure
from ici.languages.python.checks import CheckDefinition


@pytest.mark.parametrize("kind", ["security", "resource", "exception", "complexity", "cognitive"])
@pytest.mark.parametrize("required", [True, False])
def test_unparsed_input_cannot_complete_a_required_analysis(tmp_path, kind, required):
    source = tmp_path / "bad.py"
    source.write_text("def broken(:\n")
    check = CheckDefinition(
        id=f"python.{kind}", title=kind, language="python", tool=None, required=required
    )
    if kind in {"complexity", "cognitive"}:
        observation = measure(MetricRequest(kind, "python", tmp_path, (source,), check.id))
    else:
        observation = measure_hygiene(HygieneRequest(kind, tmp_path, (source,), check.id))
    result = verify(
        Plan(checks=(PlannedCheck(check=check),)),
        providers={},
        analyses={check.id: lambda: observation},
    )
    assert observation.state is TaskState.FAILED
    assert not observation.evidence_is_complete
    assert result.gate.selected is (GateVerdict.INCOMPLETE if required else GateVerdict.PASS)
    assert result.exit_code == (3 if required else 0)


def test_partial_security_analysis_retains_a_confirmed_violation(tmp_path):
    good, bad = tmp_path / "good.py", tmp_path / "bad.py"
    good.write_text("eval(input())\n")
    bad.write_text("def broken(:\n")
    request = HygieneRequest("security", tmp_path, (good, bad), "python.security")
    observation = measure_hygiene(request)
    check = CheckDefinition(id=request.task_id, title="Security", language="python", tool=None)
    result = verify(
        Plan(checks=(PlannedCheck(check=check),)),
        providers={},
        analyses={check.id: lambda: observation},
    )
    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.gate.has_violations
    assert result.findings[0].primary_location.path == "good.py"
    assert observation.measurements[0].value == 1
    assert measure_hygiene(replace(request, files=(good,))).evidence_is_complete


def test_resource_budget_is_incomplete(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("x = 1\n")

    def limited(*args):
        raise ResourceAnalysisLimit("analysis budget exhausted")

    monkeypatch.setattr("ici.languages.hygiene.analyze_python_resources", limited)
    observation = measure_hygiene(
        HygieneRequest("resource", tmp_path, (source,), "python.resource")
    )
    assert observation.state is TaskState.FAILED
    assert "budget" in observation.limitations[0]


def test_missing_metric_input_is_incomplete_and_empty_function_scope_is_complete(tmp_path):
    source = tmp_path / "main.py"
    request = MetricRequest("complexity", "python", tmp_path, (source,), "python.complexity")
    assert not measure(request).evidence_is_complete
    source.write_text("x = 1\n")
    assert measure(request).evidence_is_complete
