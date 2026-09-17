"""Declared suppressions, from ``[[suppressions]]`` to the gate (#221).

A suppression is a named relaxation: it marks a finding with the reason and
the file that declared it, and the mark — not the finding — is what keeps it
out of the gate. The tests below pin the three ways this goes wrong: a
suppression that selects nothing, a selector that matches more than the
author meant, and a suppressed finding being dropped instead of reported.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.suppressions import apply_suppressions
from ici.application.verify import verify
from ici.config.composition import EffectiveSuppression, compose
from ici.config.errors import NextConfigError
from ici.config.scaffold import propose, write
from ici.config.schema import read_root
from ici.domain.enums import EvidenceLevel, GateVerdict
from ici.domain.finding import Finding, SourceSpan

runner = CliRunner()

_ROOT = """schema_version = 1
[workspace]
name = "p"
[[components]]
id = "app"
root = "."
languages = ["python"]
"""


def _suppression(**selectors: str) -> EffectiveSuppression:
    return EffectiveSuppression(
        fingerprint=selectors.get("fingerprint", ""),
        rule=selectors.get("rule", ""),
        path=selectors.get("path", ""),
        component=selectors.get("component", ""),
        reason=selectors.get("reason", "tracked elsewhere"),
        declared_in=selectors.get("declared_in", "ici.toml"),
    )


def _finding(
    rule: str = "ruff.F401",
    path: str = "src/app.py",
    component: str | None = "app",
    fingerprint: str = "sha256:one",
) -> Finding:
    return Finding(
        fingerprint=fingerprint,
        rule_id=rule,
        message="unused import",
        severity="warning",
        confidence="high",
        provider="ruff",
        primary_location=SourceSpan(path=path, start_line=1, start_column=1),
        component_id=component,
        evidence=EvidenceLevel.MEASURED,
    )


# --- the schema ------------------------------------------------------------


def _read(suppressions: str):
    return read_root(_ROOT + suppressions, path="ici.toml")


def test_a_declared_suppression_is_read() -> None:
    document = _read('[[suppressions]]\nrule = "ruff.F401"\npath = "src/**"\nreason = "PROJ-9"\n')

    (item,) = document.suppressions
    assert item.rule.value == "ruff.F401"
    assert item.path.value == "src/**"
    assert item.reason.value == "PROJ-9"


def test_a_suppression_without_a_reason_is_refused() -> None:
    with pytest.raises(NextConfigError, match="reason"):
        _read('[[suppressions]]\nrule = "ruff.F401"\n')


def test_a_suppression_that_selects_nothing_is_refused() -> None:
    # With no selector the entry would suppress every finding — a gate the
    # file never visibly declared.
    with pytest.raises(NextConfigError, match="selects nothing"):
        _read('[[suppressions]]\nreason = "everything is fine"\n')


@pytest.mark.parametrize("path", ["../escape/**", "/abs/**"])
def test_a_suppression_path_must_stay_in_the_workspace(path: str) -> None:
    with pytest.raises(NextConfigError, match="inside the workspace"):
        _read(f'[[suppressions]]\npath = "{path}"\nreason = "x"\n')


def test_an_unknown_suppression_key_is_refused() -> None:
    with pytest.raises(NextConfigError, match="unknown"):
        _read('[[suppressions]]\nrule = "ruff.F401"\nreason = "x"\nseverity = "low"\n')


# --- composition -----------------------------------------------------------


def test_suppressions_reach_the_effective_config() -> None:
    document = _read('[[suppressions]]\nrule = "ruff"\npath = "./legacy/**"\nreason = "x"\n')
    config = compose(document)

    (item,) = config.suppressions
    assert item.rule == "ruff"
    assert item.path == "legacy/**", "the glob kept its ./ prefix"
    assert item.reason == "x"
    assert item.declared_in == "ici.toml"


def test_suppressions_are_part_of_the_policy_digest() -> None:
    plain = compose(read_root(_ROOT, path="ici.toml"))
    suppressed = compose(_read('[[suppressions]]\nrule = "ruff"\nreason = "x"\n'))

    assert plain.policy_digest != suppressed.policy_digest


def test_the_reason_text_does_not_change_the_digest() -> None:
    one = compose(_read('[[suppressions]]\nrule = "ruff"\nreason = "PROJ-1"\n'))
    two = compose(_read('[[suppressions]]\nrule = "ruff"\nreason = "PROJ-2"\n'))

    assert one.policy_digest == two.policy_digest


# --- matching --------------------------------------------------------------


def test_each_selector_matches_on_its_own() -> None:
    finding = _finding()

    for item in (
        _suppression(fingerprint="sha256:one"),
        _suppression(rule="ruff.F401"),
        _suppression(path="src/**"),
        _suppression(component="app"),
    ):
        (marked,) = apply_suppressions((finding,), (item,))
        assert marked.suppression.suppressed


def test_selectors_are_conjunctive() -> None:
    finding = _finding()
    item = _suppression(rule="ruff.F401", path="tests/**")

    (marked,) = apply_suppressions((finding,), (item,))

    assert not marked.suppression.suppressed


def test_a_rule_selector_covers_its_descendants_but_not_its_siblings() -> None:
    item = _suppression(rule="ruff")

    child = apply_suppressions((_finding(rule="ruff.F401"),), (item,))[0]
    sibling = apply_suppressions((_finding(rule="mypy.x"),), (item,))[0]

    assert child.suppression.suppressed
    assert not sibling.suppression.suppressed


def test_a_path_selector_anchors_at_the_root_when_it_has_a_separator() -> None:
    item = _suppression(path="legacy/**")

    inside = apply_suppressions((_finding(path="legacy/old/a.py"),), (item,))[0]
    nested = apply_suppressions((_finding(path="src/legacy/a.py"),), (item,))[0]

    assert inside.suppression.suppressed
    assert not nested.suppression.suppressed


def test_a_path_selector_without_a_separator_matches_the_basename() -> None:
    item = _suppression(path="*.py")

    assert apply_suppressions((_finding(path="a/b/c.py"),), (item,))[0].suppression.suppressed
    assert not apply_suppressions((_finding(path="a/b/c.txt"),), (item,))[0].suppression.suppressed


def test_a_suppressed_finding_keeps_its_reason_and_origin() -> None:
    item = _suppression(rule="ruff", reason="PROJ-9", declared_in="ici.toml")

    (marked,) = apply_suppressions((_finding(),), (item,))

    assert marked.suppression.reason == "PROJ-9"
    assert marked.suppression.origin == "ici.toml"
    assert marked.suppression.kind == "config"


def test_the_first_matching_declaration_wins() -> None:
    first = _suppression(rule="ruff", reason="first")
    second = _suppression(rule="ruff.F401", reason="second")

    (marked,) = apply_suppressions((_finding(),), (first, second))

    assert marked.suppression.reason == "first"


def test_an_unmatched_finding_is_untouched() -> None:
    finding = _finding()

    (kept,) = apply_suppressions((finding,), (_suppression(rule="mypy"),))

    assert kept is finding


# --- the gate --------------------------------------------------------------


def test_a_suppressed_finding_cannot_fail_the_gate() -> None:
    from ici.adapters.providers.base import ParsedOutput
    from ici.application.plan import Plan, PlannedCheck
    from test_application_verify import LINT, _outcome, _Provider, _task

    plan = Plan(checks=(PlannedCheck(check=LINT, task=_task()),))
    provider = _Provider(ParsedOutput(findings=(_finding(component=None),)))

    result = verify(
        plan,
        providers={"ruff": provider},
        analyses={},
        runner=lambda _: _outcome(1),
        suppressions=(_suppression(rule="ruff"),),
    )

    assert result.gate.selected is GateVerdict.PASS
    assert result.findings[0].suppression.suppressed, "the finding was dropped"


def test_a_suppression_cannot_hide_an_unfinished_check() -> None:
    # SPEC-04 §4: a required check that did not finish is not a finding, so no
    # selector can reach it.
    from ici.application.plan import Plan, PlannedCheck
    from test_application_verify import LINT

    plan = Plan(checks=(PlannedCheck(check=LINT, blocked="ruff was not found"),))

    result = verify(
        plan,
        providers={},
        analyses={},
        suppressions=(_suppression(rule="ruff"),),
    )

    assert result.gate.selected is GateVerdict.INCOMPLETE
    assert result.exit_code == 3


# --- end to end ------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")
    write(propose(root), root / "ici.toml")
    with (root / "ici.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            '[checks."python.test"]\nenabled = false\n'
            '[checks."python.coverage"]\nenabled = false\n'
            '[checks."python.type"]\nenabled = false\n'
            '[checks."python.compat-runtime"]\nenabled = false\n'
            '[[suppressions]]\nrule = "ruff"\nreason = "accepted debt"\n'
        )
    monkeypatch.chdir(root)
    return root


def test_a_declared_suppression_reaches_the_saved_result(project: Path) -> None:
    import shutil

    ruff = shutil.which("ruff") or Path(".venv/bin/ruff").resolve()
    if not Path(str(ruff)).exists():
        pytest.skip("ruff is not available")

    result = runner.invoke(app, ["next", "verify"])

    assert result.exit_code == 0, result.output
    stored = json.loads((project / ".ici" / "next" / "result.json").read_text())
    findings = [item for item in stored["findings"] if item["rule_id"].startswith("ruff.")]
    assert findings, "the seeded defect produced nothing"
    assert all(
        item["suppression"]["suppressed"] and item["suppression"]["reason"] == "accepted debt"
        for item in findings
    ), findings
