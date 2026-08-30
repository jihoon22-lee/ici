import json
from copy import deepcopy
from pathlib import Path

import pytest

from ici.config import DEFAULT_CONFIG
from ici.core.baseline import (
    BASELINE_MAX_BYTES,
    BaselineError,
    build_analysis_metadata,
    compare_suite_to_baseline,
    load_baseline,
)
from ici.core.models import (
    AnalysisMetadata,
    AnalysisMode,
    BaselineComparison,
    DeltaState,
    EngineResult,
    EngineStatus,
    EvidenceState,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingDelta,
    FindingSeverity,
    FindingSuppression,
    SourceLocation,
    SuppressionKind,
    VerificationSuiteResult,
)
from ici.core.support import evaluate_support_matrix
from ici.reporters.json_rep import save_json_report, serialize_suite_result

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


def _metadata(
    *,
    producer: str = "0.6.0",
    fingerprint: str = "ici-fingerprint/v1",
    policy: str = _DIGEST_A,
    tools: str = _DIGEST_A,
) -> AnalysisMetadata:
    return AnalysisMetadata(producer, fingerprint, policy, tools)


def _finding(
    label: str,
    line: int,
    *,
    severity: FindingSeverity = FindingSeverity.MEDIUM,
    path: str = "src/service.py",
    rule_id: str = "ici.test.baseline",
    suppressed: bool = False,
    message: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        category=FindingCategory.CORRECTNESS,
        severity=severity,
        confidence=FindingConfidence.EXACT,
        fingerprint=_DIGEST_A,
        primary_location=SourceLocation(path=path, start_line=line, label=label),
        message=message or label or "region finding",
        suppression=FindingSuppression(
            suppressed=suppressed,
            kind=SuppressionKind.INLINE if suppressed else SuppressionKind.NONE,
            reason="accepted" if suppressed else "",
        ),
    )


def _suite(
    findings: list[Finding],
    *,
    engine_name: str = "lint",
    metadata: AnalysisMetadata | None = None,
) -> VerificationSuiteResult:
    result = EngineResult(
        engine_name=engine_name,
        status=EngineStatus.WARN,
        summary="findings",
        findings=findings,
    )
    return VerificationSuiteResult(
        suite_status=EngineStatus.WARN,
        results=[result],
        analysis_metadata=metadata,
    )


def _write_baseline(
    root: Path,
    findings: list[Finding],
    *,
    engine_name: str = "lint",
    metadata: AnalysisMetadata | None = None,
) -> Path:
    path = root / "baseline.json"
    save_json_report(
        _suite(findings, engine_name=engine_name, metadata=metadata),
        path,
        project_root=root,
    )
    return path


def _compare(
    root: Path,
    current: VerificationSuiteResult,
    baseline: Path,
    *,
    metadata: AnalysisMetadata | None = None,
    fail_on_new: bool = False,
):
    return compare_suite_to_baseline(
        current,
        baseline_path=baseline,
        project_root=root,
        current_metadata=metadata or _metadata(),
        fail_on_new=fail_on_new,
    )


def test_delta_classifies_full_inventory_and_independent_regressions(tmp_path):
    baseline = _write_baseline(
        tmp_path,
        [
            _finding("moved", 10),
            _finding("same", 30),
            _finding("resolved", 40),
        ],
        metadata=_metadata(),
    )
    current = _suite(
        [
            _finding("moved", 20, severity=FindingSeverity.HIGH),
            _finding("same", 30),
            _finding("new", 50, severity=FindingSeverity.LOW),
        ]
    )

    comparison = _compare(tmp_path, current, baseline, fail_on_new=True)

    assert comparison.count(DeltaState.NEW) == 1
    assert comparison.count(DeltaState.UNCHANGED) == 1
    assert comparison.count(DeltaState.MOVED) == 1
    assert comparison.count(DeltaState.RESOLVED) == 1
    assert comparison.regressed_count == 1
    assert comparison.gated_count == 2
    assert comparison.gate_failed
    moved = next(entry for entry in comparison.entries if entry.state == DeltaState.MOVED)
    assert moved.regressed
    assert moved.current_location is not None and moved.current_location.start_line == 20
    assert moved.baseline_location is not None and moved.baseline_location.start_line == 10


def test_duplicate_fingerprints_are_compared_as_a_deterministic_multiset(tmp_path):
    baseline = _write_baseline(
        tmp_path,
        [_finding("overload", 10), _finding("overload", 20)],
        metadata=_metadata(),
    )
    current = _suite([_finding("overload", 20), _finding("overload", 30)])

    first = _compare(tmp_path, current, baseline)
    second = _compare(tmp_path, current, baseline)

    assert [entry.state for entry in first.entries] == [
        DeltaState.MOVED,
        DeltaState.UNCHANGED,
    ]
    assert first.entries == second.entries


def test_engine_name_is_part_of_the_comparison_identity(tmp_path):
    baseline = _write_baseline(
        tmp_path, [_finding("shared", 10)], engine_name="lint", metadata=_metadata()
    )
    current = _suite([_finding("shared", 10)], engine_name="security")

    comparison = _compare(tmp_path, current, baseline)

    assert comparison.count(DeltaState.NEW) == 1
    assert comparison.count(DeltaState.RESOLVED) == 1


def test_info_and_suppressed_new_findings_stay_in_inventory_but_do_not_gate(tmp_path):
    baseline = _write_baseline(tmp_path, [], metadata=_metadata())
    current = _suite(
        [
            _finding("pass", 1, severity=FindingSeverity.INFO),
            _finding("accepted", 2, severity=FindingSeverity.HIGH, suppressed=True),
        ]
    )

    comparison = _compare(tmp_path, current, baseline, fail_on_new=True)

    assert comparison.count(DeltaState.NEW) == 2
    assert comparison.gated_count == 0
    assert not comparison.gate_failed


def test_removing_a_baseline_suppression_is_an_actionable_regression(tmp_path):
    baseline = _write_baseline(
        tmp_path,
        [_finding("accepted", 2, severity=FindingSeverity.HIGH, suppressed=True)],
        metadata=_metadata(),
    )
    current = _suite([_finding("accepted", 2, severity=FindingSeverity.HIGH)])

    comparison = _compare(tmp_path, current, baseline, fail_on_new=True)

    assert comparison.count(DeltaState.UNCHANGED) == 1
    assert comparison.regressed_count == 1
    assert comparison.gated_count == 1
    assert comparison.gate_failed


def test_metadata_mismatches_warn_without_invalidating_the_delta(tmp_path):
    baseline = _write_baseline(
        tmp_path,
        [_finding("same", 1)],
        metadata=_metadata(producer="0.5.0", fingerprint="ici-fingerprint/v0"),
    )
    current_metadata = _metadata(producer="0.6.0", policy=_DIGEST_B, tools=_DIGEST_B)

    comparison = _compare(
        tmp_path, _suite([_finding("same", 1)]), baseline, metadata=current_metadata
    )

    assert comparison.count(DeltaState.UNCHANGED) == 1
    assert len(comparison.warnings) == 4
    assert any("producer version" in warning for warning in comparison.warnings)
    assert any("fingerprint version" in warning for warning in comparison.warnings)
    assert any("analysis policy" in warning for warning in comparison.warnings)
    assert any("tool policy" in warning for warning in comparison.warnings)


def test_older_v3_without_metadata_is_accepted_with_an_explicit_warning(tmp_path):
    baseline = _write_baseline(tmp_path, [_finding("same", 1)])

    comparison = _compare(tmp_path, _suite([_finding("same", 1)]), baseline)

    assert comparison.count(DeltaState.UNCHANGED) == 1
    assert comparison.baseline_metadata is None
    assert comparison.warnings == [
        "baseline has no analysis_metadata; producer, fingerprint, policy, and tool "
        "compatibility could not be verified"
    ]


@pytest.mark.parametrize(
    "unsafe_path",
    ["../outside.py", "/tmp/outside.py", r"C:\outside\file.cpp"],
)
def test_baseline_rejects_unsafe_primary_and_related_locations(tmp_path, unsafe_path):
    baseline = _write_baseline(tmp_path, [_finding("same", 1)], metadata=_metadata())
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    finding = payload["results"][0]["findings"][0]
    finding["primary_location"]["path"] = unsafe_path
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BaselineError, match="unsafe"):
        load_baseline(baseline, tmp_path)

    finding["primary_location"]["path"] = "src/service.py"
    finding["related_locations"] = [dict(finding["primary_location"], path=unsafe_path)]
    baseline.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BaselineError, match="unsafe"):
        load_baseline(baseline, tmp_path)


@pytest.mark.parametrize("noncanonical_path", ["src/../service.py", r"src\service.py"])
def test_baseline_rejects_noncanonical_path_aliases(tmp_path, noncanonical_path):
    baseline = _write_baseline(tmp_path, [_finding("same", 1)], metadata=_metadata())
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["results"][0]["findings"][0]["primary_location"]["path"] = noncanonical_path
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BaselineError, match="canonical"):
        load_baseline(baseline, tmp_path)


def test_baseline_rejects_a_fingerprint_that_does_not_match_its_finding(tmp_path):
    baseline = _write_baseline(tmp_path, [_finding("same", 1)], metadata=_metadata())
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["results"][0]["findings"][0]["fingerprint"] = _DIGEST_B
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BaselineError, match="does not match"):
        load_baseline(baseline, tmp_path)


def test_baseline_file_must_stay_inside_project_even_through_a_symlink(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(BaselineError, match=r"outside project root|could not read baseline"):
        load_baseline(outside, project)

    link = project / "baseline.json"
    try:
        link.symlink_to(outside)
    except OSError as err:
        pytest.skip(f"symlinks unavailable: {err}")
    with pytest.raises(BaselineError, match=r"outside project root|could not read baseline"):
        load_baseline(link, project)


@pytest.mark.parametrize("schema", ["ici.result/v2", "ici.result/v4", None])
def test_baseline_requires_v3_schema(tmp_path, schema):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema_version": schema, "results": []}), encoding="utf-8")

    with pytest.raises(BaselineError, match="schema_version"):
        load_baseline(baseline, tmp_path)


def test_baseline_rejects_malformed_and_oversized_input(tmp_path, monkeypatch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{not json", encoding="utf-8")
    with pytest.raises(BaselineError, match="parse"):
        load_baseline(baseline, tmp_path)

    baseline.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("ici.core.baseline.BASELINE_MAX_BYTES", 1)
    with pytest.raises(BaselineError, match="exceeds"):
        load_baseline(baseline, tmp_path)
    assert BASELINE_MAX_BYTES >= 64 * 1024 * 1024


def test_analysis_metadata_is_deterministic_and_tracks_tool_policy(tmp_path):
    config = deepcopy(DEFAULT_CONFIG)
    matrix = evaluate_support_matrix(tmp_path, config, [])

    first = build_analysis_metadata(config, matrix)
    second = build_analysis_metadata(config, matrix)

    assert first == second
    assert first.policy_digest.startswith("sha256:")
    assert first.tool_policy_digest.startswith("sha256:")

    changed = deepcopy(config)
    changed["engines"]["lint"]["ruff_required"] = True
    changed_matrix = evaluate_support_matrix(tmp_path, changed, [])
    changed_metadata = build_analysis_metadata(changed, changed_matrix)
    assert changed_metadata.policy_digest != first.policy_digest
    assert changed_metadata.tool_policy_digest != first.tool_policy_digest

    observed_matrix = deepcopy(matrix)
    observed_matrix.entries[0].applicable = True
    observed_matrix.entries[0].active_mode = AnalysisMode.EXACT
    observed_matrix.entries[0].evidence = EvidenceState.MEASURED
    observed_metadata = build_analysis_metadata(config, observed_matrix)
    assert observed_metadata.policy_digest == first.policy_digest
    assert observed_metadata.tool_policy_digest != first.tool_policy_digest


def test_baseline_delta_serialization_is_complete_and_redacted(tmp_path):
    baseline = _write_baseline(tmp_path, [], metadata=_metadata())
    current = _suite(
        [
            _finding(
                "new",
                5,
                severity=FindingSeverity.HIGH,
                message="password=supersecret",
            )
        ],
        metadata=_metadata(),
    )
    current.baseline_comparison = _compare(tmp_path, current, baseline, fail_on_new=True)
    current.baseline_comparison.baseline_metadata = _metadata(producer="password=metadata-secret")
    current.baseline_comparison.warnings.append("token=warning-secret")

    payload = serialize_suite_result(current, project_root=tmp_path)

    assert payload["analysis_metadata"]["fingerprint_version"] == "ici-fingerprint/v1"
    comparison = payload["baseline_comparison"]
    assert comparison["source_path"] == "baseline.json"
    assert comparison["new_count"] == 1
    assert comparison["unchanged_count"] == 0
    assert comparison["moved_count"] == 0
    assert comparison["resolved_count"] == 0
    assert comparison["regressed_count"] == 0
    assert comparison["gated_count"] == 1
    assert comparison["gate_failed"] is True
    assert comparison["entries"][0]["state"] == "new"
    assert "supersecret" not in json.dumps(payload)
    assert "metadata-secret" not in json.dumps(payload)
    assert "warning-secret" not in json.dumps(payload)
    assert "***REDACTED***" in comparison["entries"][0]["message"]


def test_checked_in_schema_declares_optional_baseline_contract():
    schema_path = (
        Path(__file__).parents[1] / "src" / "ici" / "schemas" / "ici-result-v3.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    suite_properties = schema["$defs"]["suite"]["properties"]
    assert "analysis_metadata" in suite_properties
    assert "baseline_comparison" in suite_properties
    assert "analysis_metadata" not in schema["$defs"]["suite"]["required"]
    assert "baseline_comparison" not in schema["$defs"]["suite"]["required"]
    assert set(schema["$defs"]["baselineComparison"]["required"]) >= {
        "new_count",
        "unchanged_count",
        "moved_count",
        "resolved_count",
        "regressed_count",
        "gated_count",
        "entries",
    }


def test_json_writer_replaces_atomically_and_cleans_failed_temporary_file(tmp_path, monkeypatch):
    output = tmp_path / "baseline.json"
    output.write_text("old", encoding="utf-8")
    original_replace = Path.replace

    def fail_replace(source, target):
        if source.parent == output.parent and source.name.startswith(f".{output.name}."):
            raise OSError("replace failed")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_json_report(_suite([], metadata=_metadata()), output, project_root=tmp_path)

    assert output.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_json_writer_rejects_contradictory_delta_and_metadata_states(tmp_path):
    location = SourceLocation("src/service.py", 1, label="run")
    invalid_new = FindingDelta(
        state=DeltaState.NEW,
        engine_name="lint",
        fingerprint=_DIGEST_A,
        rule_id="ici.test.baseline",
        message="new",
        current_location=location,
        baseline_location=location,
        current_severity=FindingSeverity.HIGH,
        baseline_severity=FindingSeverity.MEDIUM,
        gated=True,
    )
    suite = _suite([], metadata=_metadata())
    suite.baseline_comparison = BaselineComparison(
        source_path="baseline.json",
        entries=[invalid_new],
        fail_on_new=True,
        gate_failed=True,
    )
    with pytest.raises(ValueError, match="must not contain baseline"):
        serialize_suite_result(suite, project_root=tmp_path)

    suite.baseline_comparison = BaselineComparison(
        source_path="baseline.json", fail_on_new=False, gate_failed=True
    )
    with pytest.raises(ValueError, match="gate_failed contradicts"):
        serialize_suite_result(suite, project_root=tmp_path)

    suite.baseline_comparison = None
    suite.analysis_metadata = _metadata(policy="not-a-digest")
    with pytest.raises(ValueError, match="sha256 digest"):
        serialize_suite_result(suite, project_root=tmp_path)
