"""Real provider parsers must not encode the first execution consumer's ID."""

from dataclasses import replace

import pytest

from ici.adapters.providers import builtin_providers
from ici.application.plan import Plan, PlannedCheck
from ici.application.verify import verify
from ici.config.composition import EffectiveSuppression
from ici.domain.enums import GateVerdict
from ici.execution.process import Outcome, TaskOutcome
from ici.languages.checks import CheckDefinition

CASES = ("ctest", "qtest", "sanitize-suite", "sanitize-report", "integration", "version", "compile")


def _case(kind, root):
    root.mkdir(exist_ok=True)
    providers = builtin_providers(root)
    kwargs = {"cwd": str(root), "task_id": "a.check", "analysis_unit_id": "a.unit"}
    stdout, stderr, code = "", "", 1
    if kind == "ctest":
        provider = providers["ctest"]
        planned = provider.plan("ctest", build_dir=str(root / "build"), **kwargs)
        stdout, code = "Test #1: sample ........***Failed 0.01 sec\n", 8
    elif kind == "qtest":
        provider = providers["qtest"]
        planned = provider.plan(binary=str(root / "build/suite"), **kwargs)
        stdout = "FAIL! : Sample::case() problem\nPASS : Sample::cleanupTestCase()\n"
    elif kind.startswith("sanitize"):
        provider = providers["sanitize"]
        planned = provider.plan(argv=(str(root / "build/suite"),), **kwargs)
        if kind == "sanitize-report":
            stderr = (
                "==41==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010\n"
                "READ of size 4 at 0x602000000010 thread T0\n"
                f"    #1 0x7f01 in Worker::read() {root / 'main.cpp'}:42:7\n"
                f"SUMMARY: AddressSanitizer: heap-use-after-free {root / 'main.cpp'}:42 in Worker::read()\n"
            )
    elif kind == "integration":
        provider = providers["integration"]
        planned = provider.plan(
            argv=("python", str(root / "case.py")),
            case_name="contract",
            case_source="ici.toml",
            case_required=True,
            expected_exit=0,
            stdout_contains=(),
            stderr_contains=(),
            stdout_not_contains=(),
            stderr_not_contains=(),
            outputs=(),
            env=(),
            requires=(),
            timeout_seconds=2,
            **kwargs,
        )
    elif kind == "version":
        provider = providers["python-compat-version"]
        planned = provider.plan(str(root / "venv/bin/python"), requires_python=">=3.10", **kwargs)
        stdout, code = "Python 3.9.0\n", 0
    else:
        provider = providers["python-compat-compileall"]
        planned = provider.plan(
            str(root / "venv/bin/python"),
            files=(str(root / "main.py"),),
            pycache_prefix=str(root / ".ici/pycache"),
            **kwargs,
        )
        stdout = (
            f"*** Error compiling '{root / 'main.py'}'...\n"
            f'  File "{root / "main.py"}", line 1\n'
            "SyntaxError: invalid syntax\n"
        )
    check = CheckDefinition(id="sample.check", title="Check", language="cpp", tool=provider.name)
    plans = tuple(
        Plan(
            checks=(
                PlannedCheck(
                    check=check,
                    task=replace(
                        planned,
                        task=replace(
                            planned.task, id=f"{owner}.check", analysis_unit_ids=(f"{owner}.unit",)
                        ),
                    ),
                ),
            )
        )
        for owner in ("a", "b")
    )
    calls = []

    def runner(spec):
        calls.append(spec.name)
        return TaskOutcome(
            spec=spec, outcome=Outcome.FINISHED, stdout=stdout, stderr=stderr, exit_code=code
        )

    options = dict(
        providers=providers,
        analyses={},
        runner=runner,
        task_components={"a.check": "a", "b.check": "b"},
    )
    return plans, options, calls


@pytest.mark.parametrize("kind", CASES)
def test_shared_and_standalone_provider_findings_have_the_same_identity(tmp_path, kind):
    plans, options, calls = _case(kind, tmp_path)
    together = verify(plans, **options)
    assert len(calls) == 1
    assert together.gate.selected is GateVerdict.FAIL
    assert len(together.findings) == 2
    by_owner = {f.component_id: f for f in together.findings}
    alone = verify(plans[1], **options)
    assert alone.findings == (by_owner["b"],)
    reversed_run = verify(tuple(reversed(plans)), **options)
    assert {f.component_id: f for f in reversed_run.findings} == by_owner
    suppression = EffectiveSuppression(
        fingerprint=alone.findings[0].fingerprint,
        rule="",
        path="",
        component="",
        reason="B only",
        declared_in="ici.toml",
    )
    suppressed = verify(plans, suppressions=(suppression,), **options)
    assert suppressed.gate.selected is GateVerdict.FAIL
    assert [f.component_id for f in suppressed.findings if f.suppression.suppressed] == ["b"]


@pytest.mark.parametrize("kind", CASES)
def test_provider_identity_survives_a_workspace_relocation(tmp_path, kind):
    plans, options, _ = _case(kind, tmp_path / "left")
    left = verify(plans[1], **options)
    plans, options, _ = _case(kind, tmp_path / "right")
    right = verify(plans[1], **options)
    assert left.findings == right.findings


@pytest.mark.parametrize("kind", CASES)
def test_distinct_provider_invocations_keep_their_own_findings(tmp_path, kind):
    plans, options, _ = _case(kind, tmp_path)
    first = plans[1].checks[0]
    second = replace(
        first.task.task,
        id="b.other",
        argv=(*first.task.task.argv, "--other-target"),
    )
    plan = Plan(
        checks=(first, replace(first, task_id=second.id, task=replace(first.task, task=second)))
    )
    options["task_components"]["b.other"] = "b"
    result = verify(plan, **options)
    assert len(result.findings) == 2
