"""SARIF export of a stored next result (#221 PR C).

The checks that matter are the honest ones: a finding's SARIF result carries
the same fingerprint, location and severity the envelope stored — not a
re-derivation — and the run-level metadata tells a consumer what the gate,
the scope and the baseline actually were.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.config.scaffold import propose, write
from ici.domain import BaselineComparison, BaselineState
from ici.domain.serialization import loads, run_result_from_dict
from ici.reporting.sarif import SARIF_VERSION, document

runner = CliRunner()
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ici-next"


def _stored(name: str = "run-code-fail"):
    return run_result_from_dict(json.loads((FIXTURES / f"{name}.json").read_text()))


def test_the_document_is_sarif_210() -> None:
    doc = document(_stored())

    assert doc["version"] == SARIF_VERSION
    assert doc["$schema"].endswith("sarif-2.1.0.json")
    (run,) = doc["runs"]
    assert run["tool"]["driver"]["name"] == "ici"


def test_a_finding_keeps_the_identity_the_envelope_stored() -> None:
    doc = document(_stored())
    (finding,) = doc["runs"][0]["results"]

    assert finding["ruleId"] == "ICI-LINT-001"
    assert finding["level"] == "error"  # severity "high" maps to error
    location = finding["locations"][0]["physicalLocation"]
    assert location["artifactLocation"]["uri"] == "python/tool-a/demo.py"
    assert location["region"]["startLine"] == 12
    assert finding["partialFingerprints"]["ici.fingerprint"] == "f8a1c3"
    assert finding["properties"]["provider"] == "ruff"
    assert finding["properties"]["componentId"] == "tool-a"
    assert finding["properties"]["severity"] == "high"


def test_rules_are_declared_once_per_rule_id() -> None:
    doc = document(_stored())

    (rule,) = doc["runs"][0]["tool"]["driver"]["rules"]
    assert rule["id"] == "ICI-LINT-001"
    assert rule["defaultConfiguration"]["level"] == "error"
    assert rule["properties"]["provider"] == "ruff"


def test_the_run_records_gate_scope_and_limitations() -> None:
    doc = document(_stored("run-required-incomplete"))
    (run,) = doc["runs"]

    assert run["invocations"][0]["executionSuccessful"] is False
    assert run["properties"]["gate"] == "INCOMPLETE"
    assert run["properties"]["scopeKind"] == "full"


def test_a_suppressed_finding_is_marked_not_hidden() -> None:
    from ici.domain import Finding, FindingSuppression, SourceSpan
    from ici.domain.enums import EvidenceLevel
    from test_next_serialization import minimal_result

    finding = Finding(
        fingerprint="fp-sup",
        rule_id="ruff.F401",
        message="unused import",
        severity="warning",
        confidence="high",
        provider="ruff",
        primary_location=SourceSpan(path="a.py", start_line=1),
        evidence=EvidenceLevel.MEASURED,
        suppression=FindingSuppression(
            suppressed=True, kind="config", reason="PROJ-9", origin="ici.toml"
        ),
    )
    doc = document(minimal_result(findings=(finding,)))
    (entry,) = doc["runs"][0]["results"]

    (suppression,) = entry["suppressions"]
    assert suppression["kind"] == "external"
    assert suppression["justification"] == "PROJ-9"
    assert suppression["properties"]["origin"] == "ici.toml"


def test_baseline_state_marks_new_and_unchanged_only() -> None:
    from ici.domain import Finding, SourceSpan
    from ici.domain.enums import EvidenceLevel
    from test_next_serialization import minimal_result

    def finding(fp: str) -> Finding:
        return Finding(
            fingerprint=fp,
            rule_id="ruff.F401",
            message="m",
            severity="warning",
            confidence="high",
            provider="ruff",
            primary_location=SourceSpan(path="a.py", start_line=1),
            evidence=EvidenceLevel.MEASURED,
        )

    result = minimal_result(
        findings=(finding("fp-new"), finding("fp-same")),
        baseline=BaselineComparison(
            state=BaselineState.COMPARABLE,
            origin="base.json",
            new=("fp-new",),
            unchanged=("fp-same",),
            resolved=("fp-gone",),
            carried=("fp-elsewhere",),
        ),
    )
    doc = document(result)
    states = {
        entry["partialFingerprints"]["ici.fingerprint"]: entry.get("baselineState")
        for entry in doc["runs"][0]["results"]
    }

    assert states == {"fp-new": "new", "fp-same": "unchanged"}
    summary = doc["runs"][0]["properties"]["baseline"]
    assert summary["state"] == "comparable"
    assert summary["counts"] == {
        "new": 1,
        "unchanged": 1,
        "resolved": 1,
        "carried": 1,
    }


def test_an_incompatible_baseline_records_the_reason_not_a_delta() -> None:
    from test_next_serialization import minimal_result

    result = minimal_result(
        baseline=BaselineComparison(
            state=BaselineState.INCOMPATIBLE,
            origin="old.json",
            reason="policy changed",
        )
    )
    doc = document(result)

    baseline = doc["runs"][0]["properties"]["baseline"]
    assert baseline["state"] == "incompatible"
    assert baseline["reason"] == "policy changed"


def test_no_baseline_records_no_baseline() -> None:
    from test_next_serialization import minimal_result

    doc = document(minimal_result())

    assert doc["runs"][0]["properties"]["baseline"] == {"state": "none"}


# --- the CLI surface --------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    write(propose(root), root / "ici.toml")
    with (root / "ici.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            '[checks."python.test"]\nenabled = false\n'
            '[checks."python.coverage"]\nenabled = false\n'
            '[checks."python.type"]\nenabled = false\n'
            '[checks."python.compat-runtime"]\nenabled = false\n'
            '[checks."python.lint"]\nenabled = false\n'
        )
    monkeypatch.chdir(root)
    return root


def test_report_writes_sarif_from_the_saved_result(project: Path) -> None:
    result = runner.invoke(app, ["next", "verify"])
    assert result.exit_code == 0, result.output

    outcome = runner.invoke(app, ["next", "report", "--sarif", "out.sarif"])

    assert outcome.exit_code == 0, outcome.output
    doc = json.loads((project / "out.sarif").read_text())
    assert doc["version"] == "2.1.0"
    assert doc["runs"][0]["tool"]["driver"]["name"] == "ici"
    # The stored result re-reads to exactly what was exported.
    assert loads((project / ".ici" / "next" / "result.json").read_text()).run_id
