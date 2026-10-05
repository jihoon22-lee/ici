"""Comparing a run against a stored baseline result (#221).

The property that matters is honesty about scope and identity: a baseline
taken under another policy or toolchain is not a delta, it is a different
measurement; and a finding in a component this run never selected is carried,
not resolved — a partial run must not be able to "fix" code it never looked
at.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ici.__main__ import app
from ici.application.baseline import BaselineError, compare, load_baseline
from ici.config.scaffold import propose, write
from ici.domain.enums import BaselineState, EvidenceLevel
from ici.domain.finding import Finding, SourceSpan
from ici.domain.result import FINGERPRINT_VERSION, SCHEMA_ID

runner = CliRunner()

_DIGEST = "sha256:" + "0" * 64
_OTHER = "sha256:" + "1" * 64


def _finding(fingerprint: str, component: str | None = "app") -> Finding:
    return Finding(
        fingerprint=fingerprint,
        rule_id="ruff.F401",
        message="unused import",
        severity="warning",
        confidence="high",
        provider="ruff",
        primary_location=SourceSpan(path="src/app.py", start_line=1, start_column=1),
        component_id=component,
        evidence=EvidenceLevel.MEASURED,
    )


def _document(
    tmp_path: Path,
    *,
    findings: list[dict[str, str | None]],
    policy: str = _DIGEST,
    toolchain: str = _DIGEST,
    fingerprint_version: str = FINGERPRINT_VERSION,
    selected: list[str] | None = None,
) -> Path:
    payload = {
        "schema_id": SCHEMA_ID,
        "identity": {
            "policy_digest": policy,
            "toolchain_digest": toolchain,
            "fingerprint_version": fingerprint_version,
        },
        "scope": {"selected_components": selected if selected is not None else ["app"]},
        "findings": findings,
    }
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- loading ---------------------------------------------------------------


def test_a_baseline_document_loads(tmp_path: Path) -> None:
    path = _document(tmp_path, findings=[{"fingerprint": "fp1", "component_id": "app"}])

    document = load_baseline(path)

    assert document.policy_digest == _DIGEST
    assert document.findings == (("fp1", "app"),)


def test_a_missing_file_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(BaselineError, match="cannot be read"):
        load_baseline(tmp_path / "absent.json")


def test_a_non_json_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(BaselineError, match="not JSON"):
        load_baseline(path)


def test_a_v3_result_is_not_a_baseline(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_id": "ici.finding.v3", "findings": []}), "utf-8")

    with pytest.raises(BaselineError, match=r"ici\.finding\.v3"):
        load_baseline(path)


def test_a_document_without_identity_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_id": SCHEMA_ID, "findings": []}), "utf-8")

    with pytest.raises(BaselineError, match="no identity"):
        load_baseline(path)


def test_a_finding_without_a_fingerprint_is_refused(tmp_path: Path) -> None:
    path = _document(tmp_path, findings=[{"fingerprint": "fp1", "component_id": "app"}])
    payload = json.loads(path.read_text())
    payload["findings"].append({"rule_id": "ruff.F401"})
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BaselineError, match="no fingerprint"):
        load_baseline(path)


# --- the delta -------------------------------------------------------------


def test_new_unchanged_and_resolved_are_separate(tmp_path: Path) -> None:
    baseline = load_baseline(
        _document(
            tmp_path,
            findings=[
                {"fingerprint": "fp-old", "component_id": "app"},
                {"fingerprint": "fp-same", "component_id": "app"},
            ],
        )
    )
    current = (_finding("fp-same"), _finding("fp-new"))

    delta = compare(
        current,
        baseline,
        policy_digest=_DIGEST,
        toolchain_digest=_DIGEST,
        selected_components=("app",),
    )

    assert delta.state is BaselineState.COMPARABLE
    assert delta.new == ("fp-new",)
    assert delta.unchanged == ("fp-same",)
    assert delta.resolved == ("fp-old",)
    assert delta.carried == ()


def test_a_finding_in_an_unselected_component_is_carried_not_resolved(
    tmp_path: Path,
) -> None:
    baseline = load_baseline(
        _document(
            tmp_path,
            findings=[
                {"fingerprint": "fp-app", "component_id": "app"},
                {"fingerprint": "fp-other", "component_id": "other"},
            ],
        )
    )

    delta = compare(
        (_finding("fp-app"),),
        baseline,
        policy_digest=_DIGEST,
        toolchain_digest=_DIGEST,
        selected_components=("app",),
    )

    assert delta.resolved == (), "a partial run 'fixed' a component it never ran"
    assert delta.carried == ("fp-other",)


def test_a_policy_change_makes_the_baseline_incompatible(tmp_path: Path) -> None:
    baseline = load_baseline(_document(tmp_path, findings=[], policy=_OTHER))

    delta = compare(
        (),
        baseline,
        policy_digest=_DIGEST,
        toolchain_digest=_DIGEST,
        selected_components=("app",),
    )

    assert delta.state is BaselineState.INCOMPATIBLE
    assert "policy" in delta.reason
    assert delta.new == delta.unchanged == delta.resolved == delta.carried == ()


def test_a_toolchain_change_makes_the_baseline_incompatible(tmp_path: Path) -> None:
    baseline = load_baseline(_document(tmp_path, findings=[], toolchain=_OTHER))

    delta = compare(
        (),
        baseline,
        policy_digest=_DIGEST,
        toolchain_digest=_DIGEST,
        selected_components=("app",),
    )

    assert delta.state is BaselineState.INCOMPATIBLE
    assert "toolchain" in delta.reason


def test_an_unversioned_baseline_cannot_be_compared(tmp_path: Path) -> None:
    # Written before the fingerprint algorithm was versioned — comparable
    # policy and toolchain are not enough to know the identities match.
    baseline = load_baseline(_document(tmp_path, findings=[], fingerprint_version=""))

    delta = compare(
        (),
        baseline,
        policy_digest=_DIGEST,
        toolchain_digest=_DIGEST,
        selected_components=("app",),
    )

    assert delta.state is BaselineState.INCOMPATIBLE
    assert "fingerprint" in delta.reason


# --- the CLI ---------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "ruff.toml").write_text('[lint]\nselect = ["F"]\n', encoding="utf-8")
    (root / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    write(propose(root), root / "ici.toml")
    with (root / "ici.toml").open("a", encoding="utf-8") as handle:
        handle.write(
            '[checks."python.test"]\nenabled = false\n'
            '[checks."python.coverage"]\nenabled = false\n'
            '[checks."python.type"]\nenabled = false\n'
            '[checks."python.compat-runtime"]\nenabled = false\n'
        )
    monkeypatch.chdir(root)
    return root


def test_a_run_compares_itself_against_a_stored_baseline(project: Path) -> None:
    import shutil

    ruff = shutil.which("ruff") or Path(".venv/bin/ruff").resolve()
    if not Path(str(ruff)).exists():
        pytest.skip("ruff is not available")

    first = runner.invoke(app, ["next", "verify", "--result", "baseline.json"])
    assert first.exit_code == 0, first.output

    (project / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")
    second = runner.invoke(app, ["next", "verify", "--baseline", "baseline.json", "--no-cache"])

    assert "baseline:" in second.output, second.output
    stored = json.loads((project / ".ici" / "next" / "result.json").read_text())
    assert stored["baseline"]["state"] == "comparable"
    assert stored["baseline"]["new"], "the seeded defect was not new against the baseline"


def test_an_unreadable_baseline_stops_the_run_before_it_starts(project: Path) -> None:
    result = runner.invoke(app, ["next", "verify", "--baseline", "absent.json"])

    assert result.exit_code == 2
    assert "cannot be read" in result.output


# --- next diff --------------------------------------------------------------


def test_diff_reports_the_delta_between_two_saved_results(project: Path) -> None:
    import shutil

    ruff = shutil.which("ruff") or Path(".venv/bin/ruff").resolve()
    if not Path(str(ruff)).exists():
        pytest.skip("ruff is not available")

    first = runner.invoke(app, ["next", "verify", "--result", "old.json"])
    assert first.exit_code == 0, first.output
    (project / "src" / "app.py").write_text("import os\nvalue = 1\n", encoding="utf-8")
    second = runner.invoke(app, ["next", "verify", "--no-cache"])
    assert second.exit_code == 1, second.output

    diff = runner.invoke(app, ["next", "diff", "old.json"])

    assert diff.exit_code == 0, diff.output
    assert "new" in diff.output and "resolved" in diff.output
    assert "PASS → FAIL" in diff.output


def test_diff_refuses_an_incompatible_baseline(project: Path) -> None:
    import shutil

    ruff = shutil.which("ruff") or Path(".venv/bin/ruff").resolve()
    if not Path(str(ruff)).exists():
        pytest.skip("ruff is not available")

    assert runner.invoke(app, ["next", "verify", "--result", "old.json"]).exit_code == 0
    # A policy change rewrites the digest — the comparison refuses by name.
    with (project / "ici.toml").open("a", encoding="utf-8") as handle:
        handle.write('[checks."python.hygiene"]\nenabled = false\n')
    assert runner.invoke(app, ["next", "verify", "--no-cache"]).exit_code == 0

    diff = runner.invoke(app, ["next", "diff", "old.json"])

    assert diff.exit_code == 2
    assert "incompatible" in diff.output
