use ici_domain::Validate;
use ici_validation::coverage_baseline::*;
use serde_json::json;
use std::collections::BTreeMap;

fn policy(
    line: (u64, u64),
    branch: (u64, u64),
    function: (u64, u64),
    files: &[(&str, u64, u64)],
    tolerance: Option<f64>,
) -> CoveragePolicySnapshot {
    let raw_metrics: BTreeMap<String, CoverageRatio> = BTreeMap::from([
        (
            "line".into(),
            CoverageRatio::counts(Some(line.0), Some(line.1)),
        ),
        (
            "branch".into(),
            CoverageRatio::counts(Some(branch.0), Some(branch.1)),
        ),
        (
            "function".into(),
            CoverageRatio::counts(Some(function.0), Some(function.1)),
        ),
        (
            "changed_line".into(),
            CoverageRatio {
                covered: None,
                total: None,
                state: RatioState::NotSelected,
                reason: "not selected".into(),
            },
        ),
    ]);
    let raw_files = files
        .iter()
        .map(|(path, covered, total)| {
            (
                path.to_string(),
                CoverageRatio::counts(Some(*covered), Some(*total)),
            )
        })
        .collect::<BTreeMap<_, _>>();
    CoveragePolicySnapshot {
        schema_id: SNAPSHOT_SCHEMA.into(),
        schema_version: 1,
        task_id: "project.python.coverage".into(),
        component_id: "project".into(),
        provider: "coverage".into(),
        anchor_path: "src/service.py".into(),
        metrics: raw_metrics
            .iter()
            .filter_map(|(name, ratio)| {
                ratio
                    .percentage()
                    .map(|percentage| (name.clone(), percentage))
            })
            .collect(),
        files: raw_files
            .iter()
            .filter(|(_, ratio)| ratio.total.unwrap_or(0) >= 5)
            .filter_map(|(path, ratio)| {
                ratio
                    .percentage()
                    .map(|percentage| (path.clone(), percentage))
            })
            .collect(),
        regression_tolerance: tolerance,
        raw_metrics,
        raw_files,
        min_file_statements: 5,
    }
}
#[test]
fn configured_baseline_only_policy_retains_raw_locations_tolerance_and_stable_fingerprints() {
    let old = policy(
        (180, 200),
        (80, 100),
        (95, 100),
        &[("src/service.py", 92, 100), ("src/other.py", 88, 100)],
        None,
    );
    let current = policy(
        (176, 200),
        (79, 100),
        (95, 100),
        &[("src/service.py", 85, 100), ("src/other.py", 91, 100)],
        Some(1.0),
    );
    let result = compare(&current, Some(&old)).unwrap();
    assert!(result.policy_enabled && result.gating_applicable && result.comparison_complete);
    assert_eq!(result.findings.len(), 2);
    assert!(
        result
            .findings
            .iter()
            .all(|finding| finding.tags == vec!["baseline-only", "advisory"])
    );
    assert!(
        result
            .findings
            .iter()
            .all(|finding| finding.validate().is_ok())
    );
    assert!(
        result
            .findings
            .iter()
            .any(|finding| finding.rule_id.ends_with("aggregate-line"))
    );
    assert!(
        result
            .findings
            .iter()
            .any(|finding| finding.rule_id.ends_with("file-line")
                && finding.primary_location.path == "src/service.py")
    );
    assert!(
        !result
            .findings
            .iter()
            .any(|finding| finding.rule_id.contains("branch"))
    );
    let target = result
        .targets
        .iter()
        .find(|target| {
            target.file_path == "src/service.py" && target.target_name.ends_with("file-line")
        })
        .unwrap();
    assert_eq!(target.metrics["baseline_raw"]["covered"], 92);
    assert_eq!(target.metrics["current_raw"]["covered"], 85);
    let stronger = policy(
        (174, 200),
        (79, 100),
        (95, 100),
        &[("src/service.py", 83, 100), ("src/other.py", 91, 100)],
        Some(1.0),
    );
    let next = compare(&stronger, Some(&old)).unwrap();
    assert_eq!(
        result
            .findings
            .iter()
            .map(|finding| &finding.fingerprint)
            .collect::<Vec<_>>(),
        next.findings
            .iter()
            .map(|finding| &finding.fingerprint)
            .collect::<Vec<_>>()
    );
}
#[test]
fn policy_is_opt_in_and_absent_or_wrong_identity_baselines_are_explicitly_uncompared() {
    let current = policy((5, 10), (0, 0), (1, 2), &[("src/service.py", 5, 10)], None);
    let result = compare(&current, None).unwrap();
    assert!(!result.policy_enabled && !result.gating_applicable);
    let mut enabled = current.clone();
    enabled.regression_tolerance = Some(0.0);
    let missing = compare(&enabled, None).unwrap();
    assert!(!missing.comparison_complete && !missing.gating_applicable);
    assert!(missing.limitations[0].contains("no typed coverage policy snapshot"));
    let mut foreign = current;
    foreign.provider = "gcov".into();
    let result = compare(&enabled, Some(&foreign)).unwrap();
    assert!(!result.comparison_complete && !result.gating_applicable);
}
#[test]
fn known_empty_and_unmeasured_denominators_never_become_zero_percent_drops() {
    let old = policy(
        (8, 10),
        (0, 0),
        (1, 2),
        &[("src/service.py", 8, 10)],
        Some(0.0),
    );
    let mut current = old.clone();
    current
        .raw_metrics
        .insert("function".into(), CoverageRatio::counts(None, None));
    current.metrics.remove("function");
    let result = compare(&current, Some(&old)).unwrap();
    assert!(result.findings.is_empty());
    assert!(!result.comparison_complete);
    assert!(
        result
            .targets
            .iter()
            .any(|target| target.target_name.ends_with("aggregate-branch")
                && target.status == "SKIP"
                && target.metrics["current_raw"]["total"] == 0)
    );
    assert!(
        result
            .targets
            .iter()
            .any(|target| target.target_name.ends_with("aggregate-function")
                && target.status == "SKIP"
                && target.metrics["current_raw"]["total"].is_null())
    );
    assert!(
        result
            .limitations
            .iter()
            .any(|limit| limit.contains("function"))
    );
}
#[test]
fn malformed_or_inconsistent_raw_baselines_are_rejected_before_comparison() {
    let current = policy(
        (8, 10),
        (0, 0),
        (1, 2),
        &[("src/service.py", 8, 10)],
        Some(0.0),
    );
    let value = json!(current);
    assert!(CoveragePolicySnapshot::from_value(value.clone()).is_ok());
    for pointer in [
        "/raw_metrics/line/covered",
        "/raw_files/src~1service.py/covered",
    ] {
        let mut malformed = value.clone();
        *malformed.pointer_mut(pointer).unwrap() = json!(11);
        assert!(CoveragePolicySnapshot::from_value(malformed).is_err());
    }
    let mut wrong = value.clone();
    wrong["metrics"]["line"] = json!(5.0);
    assert!(CoveragePolicySnapshot::from_value(wrong).is_err());
    let mut wrong = value;
    wrong["schema_id"] = json!("ici.next.run");
    assert!(CoveragePolicySnapshot::from_value(wrong).is_err());
}
