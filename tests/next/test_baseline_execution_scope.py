"""A baseline can resolve only findings whose checks actually ran again."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.baseline import compare, load_baseline
from ici.domain.check_record import CheckRecord
from ici.domain.enums import BaselineState, TaskState
from ici.domain.result import FINGERPRINT_VERSION
from ici.domain.serialization import SchemaError, dumps, loads, run_result_to_dict
from ici.languages.cpp.checks import CPP_CHECKS
from ici.languages.python.checks import PYTHON_CHECKS


def test_language_filter_carries_unexecuted_findings_and_diff_agrees(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.py").write_text("eval(input())\n")
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")
    (tmp_path / "ici.toml").write_text(
        'schema_version=1\n[workspace]\nname="example"\n'
        '[[components]]\nid="app"\nroot="."\nlanguages=["python","cpp"]\n'
        + "".join(
            f'\n[checks."{c.id}"]\nenabled={str(c.id in {"python.security", "cpp.line"}).lower()}\nrequired=true\n'
            for c in (*PYTHON_CHECKS, *CPP_CHECKS)
        )
    )
    runner = CliRunner()
    first = runner.invoke(app, ["next", "verify", "--result", "baseline.json", "--json"])
    assert first.exit_code == 1, first.output
    partial = runner.invoke(
        app, ["next", "verify", "--cpp", "--baseline", "baseline.json", "--json"]
    )
    assert partial.exit_code == 0, partial.output
    delta = json.loads(partial.stdout)["baseline"]
    assert delta["resolved"] == []
    assert len(delta["carried"]) == 1
    diff = runner.invoke(app, ["next", "diff", "baseline.json"])
    assert diff.exit_code == 0
    assert "0 resolved, 1 carried" in diff.output
    (tmp_path / "main.py").write_text("value=1\n")
    complete = runner.invoke(app, ["next", "verify", "--baseline", "baseline.json", "--json"])
    assert complete.exit_code == 0, complete.output
    assert len(json.loads(complete.stdout)["baseline"]["resolved"]) == 1


def test_internal_analysis_retains_its_configured_build_variant(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "main.py"
    source.write_text("eval(input())\n")
    config = tmp_path / "ici.toml"
    text = (
        'schema_version=1\n[workspace]\nname="example"\n'
        '[[components]]\nid="app"\nroot="."\nlanguages=["python"]\nbuild="out"\n'
        '[builds.out]\nsystem="cmake"\ndirectory="build"\nvariant="debug"\n'
        + "".join(
            f'\n[checks."{c.id}"]\nenabled={str(c.id == "python.security").lower()}\nrequired=true\n'
            for c in PYTHON_CHECKS
        )
    )
    config.write_text(text)
    runner = CliRunner()
    before = runner.invoke(app, ["next", "verify", "--result", "before.json", "--json"])
    assert before.exit_code == 1, before.output
    original = json.loads(before.stdout)
    source.write_text("value=1\n")
    config.write_text(text.replace('variant="debug"', 'variant="release"'))
    changed = runner.invoke(app, ["next", "verify", "--baseline", "before.json", "--json"])
    assert changed.exit_code == 0, changed.output
    current = json.loads(changed.stdout)
    assert original["identity"]["policy_digest"] == current["identity"]["policy_digest"]
    assert current["baseline"]["resolved"] == []
    assert len(current["baseline"]["carried"]) == 1
    assert current["checks"][0]["variant"] == "release"
    config.write_text(text)
    same = runner.invoke(app, ["next", "verify", "--baseline", "before.json", "--json"])
    assert same.exit_code == 0, same.output
    assert len(json.loads(same.stdout)["baseline"]["resolved"]) == 1


@pytest.mark.parametrize(("relocated", "minimum"), [(False, 1), (True, 1), (False, 2)])
def test_integration_scope_excludes_freshness_time_but_keeps_assertions(
    tmp_path, monkeypatch, relocated, minimum
):
    def project(root, source, size):
        root.mkdir(exist_ok=True)
        (root / "probe.py").write_text(source)
        (root / "ici.toml").write_text(
            'schema_version=1\n[workspace]\nname="example"\n'
            '[[components]]\nid="app"\nroot="."\nlanguages=["python"]\n'
            f"[components.python]\nexecutable={json.dumps(sys.executable)}\n"
            '[[components.integrations]]\nname="write"\n'
            'argv=["{python:declared}","probe.py"]\nrequired=true\n'
            '[[components.integrations.output_artifacts]]\npath="out.txt"\n'
            f"min_size={size}\n"
            "[checks.integration]\nenabled=true\nrequired=true\n"
            + "".join(f'\n[checks."{c.id}"]\nenabled=false\n' for c in PYTHON_CHECKS)
        )

    left = tmp_path / "left"
    project(left, "pass\n", 1)
    baseline = tmp_path / "before.json"
    runner = CliRunner()
    monkeypatch.chdir(left)
    first = runner.invoke(
        app, ["next", "verify", "--profile", "deep", "--result", str(baseline), "--json"]
    )
    assert first.exit_code == 1, first.output
    original = json.loads(first.stdout)
    right = tmp_path / "right" if relocated else left
    project(right, "from pathlib import Path\nPath('out.txt').write_text('done')\n", minimum)
    monkeypatch.chdir(right)
    second = runner.invoke(
        app, ["next", "verify", "--profile", "deep", "--baseline", str(baseline), "--json"]
    )
    assert second.exit_code == 0, second.output
    current = json.loads(second.stdout)
    if minimum == 1:
        assert original["checks"][0]["scope_digest"] == current["checks"][0]["scope_digest"]
        assert len(current["baseline"]["resolved"]) == 1
    else:
        assert original["checks"][0]["scope_digest"] != current["checks"][0]["scope_digest"]
        assert current["baseline"]["resolved"] == []


@pytest.mark.parametrize(
    "reduction", ["omitted", "failed", "blocked", "cancelled", "partial", "inputs", "variant"]
)
def test_absence_without_matching_complete_evidence_is_carried(tmp_path, reduction):
    result = _result()
    before = tmp_path / "old.json"
    before.write_text(dumps(run_result_to_dict(result)))
    check = result.checks[0]
    if reduction in {"failed", "blocked", "cancelled"}:
        check = replace(check, state=TaskState(reduction.upper()), evidence_complete=False)
    elif reduction == "partial":
        check = replace(check, evidence_complete=False)
    elif reduction == "inputs":
        check = replace(check, scope_digest="sha256:" + "f" * 64)
    elif reduction == "variant":
        check = replace(check, variant="different")
    current = replace(result, findings=(), checks=() if reduction == "omitted" else (check,))
    # Exercise saved results, not just an in-memory completion map.
    current = loads(dumps(run_result_to_dict(current)))
    delta = compare(
        (),
        load_baseline(before),
        policy_digest=result.identity.policy_digest,
        toolchain_digest=result.identity.toolchain_digest,
        checks=current.checks,
    )
    assert delta.state is BaselineState.COMPARABLE
    assert delta.resolved == ()
    assert delta.carried == (result.findings[0].fingerprint,)


def test_matching_completed_check_resolves_and_old_unknown_scope_refuses(tmp_path):
    result = _result()
    before = tmp_path / "old.json"
    before.write_text(dumps(run_result_to_dict(result)))
    kwargs = dict(
        policy_digest=result.identity.policy_digest,
        toolchain_digest=result.identity.toolchain_digest,
        checks=result.checks,
    )
    delta = compare((), load_baseline(before), **kwargs)
    assert delta.resolved == (result.findings[0].fingerprint,)
    # Even a v1 result with a matching fingerprint version lacks evidence provenance.
    old = replace(result, schema_version=1, checks=None)
    before.write_text(dumps(run_result_to_dict(old)))
    assert loads(before.read_text()).schema_version == 1
    delta = compare((), load_baseline(before), **kwargs)
    assert delta.state is BaselineState.INCOMPATIBLE
    assert "scope is unknown" in delta.reason


def test_v2_requires_explicit_scope_and_rejects_false_completion():
    result = _result()
    payload = run_result_to_dict(result)
    del payload["checks"]
    with pytest.raises(SchemaError, match="require checks"):
        loads(dumps(payload))
    payload = run_result_to_dict(result)
    payload["checks"][0]["state"] = "FAILED"
    with pytest.raises(SchemaError, match="only a succeeded check"):
        loads(dumps(payload))
    payload = run_result_to_dict(result)
    del payload["checks"][0]["variant"]
    with pytest.raises(SchemaError, match="requires explicit"):
        loads(dumps(payload))


def test_v2_serialization_matches_published_contract():
    from test_next_schema_contract import (
        RUN_SCHEMA,
        TestFixturesSatisfyThePublishedSchema,
        load,
    )

    result = _result()
    payload = run_result_to_dict(result)
    TestFixturesSatisfyThePublishedSchema().check(load(RUN_SCHEMA), payload, "v2 result")
    assert loads(dumps(payload)) == result


def _result():
    fixture = Path(__file__).parents[1] / "fixtures/ici-next/run-success.json"
    result = loads(fixture.read_text())
    from ici.domain.finding import Finding, SourceSpan

    check = CheckRecord(
        task_id="app.python.security",
        check_id="python.security",
        component_id="app",
        language="python",
        scope_digest="sha256:" + "a" * 64,
        state=TaskState.SUCCEEDED,
        evidence_complete=True,
    )
    finding = Finding(
        fingerprint="security-eval",
        rule_id="python.security.eval",
        message="eval",
        severity="info",
        confidence="high",
        provider="ici",
        primary_location=SourceSpan(path="main.py", start_line=1),
        component_id="app",
        task_id=check.task_id,
    )
    return replace(
        result,
        schema_version=2,
        checks=(check,),
        findings=(finding,),
        identity=replace(result.identity, fingerprint_version=FINGERPRINT_VERSION),
    )
