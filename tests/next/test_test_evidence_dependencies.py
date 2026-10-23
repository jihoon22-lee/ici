"""Language and suite boundaries survive test-evidence graph wiring."""

import json

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.gates import gate_cpp
from ici.application.graph import build_graph
from ici.application.plan import Plan, PlannedCheck
from ici.domain.workspace import BuildUnit, Component
from ici.languages.cpp.checks import CPP_CHECKS, CPP_COVERAGE_CHECK, CPP_TEST_CHECK
from ici.languages.python.checks import PYTHON_CHECKS


def _config(root, languages, enabled, build=""):
    root.joinpath("ici.toml").write_text(
        'schema_version=1\n[workspace]\nname="example"\n'
        '[[components]]\nid="app"\nroot="."\n'
        f"languages={json.dumps(languages)}\n{build}\n"
        + "".join(
            f'\n[checks."{c.id}"]\nenabled={str(c.id in enabled).lower()}\nrequired=true\n'
            for c in (*PYTHON_CHECKS, *CPP_CHECKS)
        )
    )


def test_hybrid_tests_form_a_plan_and_report_missing_inputs(tmp_path, monkeypatch):
    _config(tmp_path, ["python", "cpp"], {"python.test", "cpp.test"})
    (tmp_path / "main.py").write_text("x=1\n")
    (tmp_path / "main.cpp").write_text("int main() { return 0; }\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    planned = runner.invoke(app, ["next", "plan"])
    assert planned.exit_code == 0, planned.output
    outcome = runner.invoke(app, ["next", "verify", "--json"])
    assert outcome.exit_code == 3, outcome.output
    data = json.loads(outcome.stdout)
    assert set(data["execution"]["blocked_task_ids"]) == {"app.python.test", "app.cpp.test"}


def _qtest_project(root):
    (root / "project.pro").write_text("TEMPLATE=subdirs\nSUBDIRS=one two\n")
    (root / "main.cpp").write_text("int main() { return 0; }\n")
    build = root / "build"
    build.mkdir()
    for name in ("one", "two"):
        (root / name).mkdir()
        (root / name / f"{name}.pro").write_text("QT += testlib\nCONFIG += testcase\n")
        binary = build / name
        binary.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$0" >> calls.txt\n'
            'echo "PASS   : Test::ok()"\n'
            'echo "Totals: 1 passed, 0 failed, 0 skipped, 0 blacklisted"\n'
        )
        binary.chmod(0o755)
    return BuildUnit(
        id="release", system="qmake", variant="release", directory="build", definition="project.pro"
    )


def test_multiple_qtest_suites_run_once_each(tmp_path, monkeypatch):
    _qtest_project(tmp_path)
    _config(tmp_path, ["cpp"], {"cpp.test"}, 'build="release"')
    with (tmp_path / "ici.toml").open("a") as stream:
        stream.write(
            '\n[builds.release]\nsystem="qmake"\nproject="project.pro"\ndirectory="build"\nvariant="release"\n'
        )
    monkeypatch.chdir(tmp_path)
    outcome = CliRunner().invoke(app, ["next", "verify", "--json"])
    assert outcome.exit_code == 0, outcome.output
    assert len((tmp_path / "calls.txt").read_text().splitlines()) == 2
    data = json.loads(outcome.stdout)
    assert sum(m["value"] for m in data["metrics"] if m["name"] == "qtest.cases") == 2


@pytest.mark.parametrize("missing", [False, True])
def test_coverage_waits_for_every_declared_suite(tmp_path, monkeypatch, missing):
    build = _qtest_project(tmp_path)
    (tmp_path / "build/one.gcno").touch()
    if missing:
        (tmp_path / "build/two").unlink()
    monkeypatch.setattr("ici.application.testing.locate_tool", lambda tool: "/tools/" + tool)
    component = Component(id="app", root=".", languages=("cpp",), build_ids=("release",))
    plan = gate_cpp(
        Plan(checks=(PlannedCheck(CPP_TEST_CHECK), PlannedCheck(CPP_COVERAGE_CHECK))),
        component,
        ("main.cpp",),
        (build,),
        tmp_path,
        None,
        None,
    )
    graph = build_graph(plan.checks)
    if missing:
        assert any(item.check.check.id == "cpp.coverage" for item in graph.blocked)
    else:
        unit = graph.unit_of("cpp.coverage")
        assert unit is not None
        assert set(unit.depends_on) == {"cpp.test.release-one", "cpp.test.release-two"}
