//! Run TEM metrics and limitations against frozen `application/report.py` `tem_metrics`
//! (tests/rust-parity/reporting/capture_tem_metrics.py, synthetic observations).
use ici_domain::{Measurement, Observation, tem};
use serde_json::{Value, json};

fn observations(case: &Value) -> Vec<Observation> {
    case["observations"]
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| {
            let mut observation = Observation::new(entry[0].as_str().unwrap(), "capture");
            observation.measurements = entry[1]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| Measurement {
                    numerator: item["numerator"].as_u64(),
                    denominator: item["denominator"].as_u64(),
                    ..Measurement::measured(
                        item["name"].as_str().unwrap(),
                        item["value"].as_f64().unwrap(),
                    )
                })
                .collect();
            observation
        })
        .collect()
}

fn frozen(case: &str) -> (Value, Value) {
    let golden: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/tem-metrics.json"
    ))
    .unwrap();
    assert_eq!(golden["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let case = &golden["cases"][case];
    assert!(case.is_object(), "missing frozen case");
    let (metrics, limitations) = tem::run_metrics(&observations(case));
    let metrics: Vec<Value> = metrics
        .iter()
        .map(|metric| {
            assert_eq!(metric.evidence, ici_domain::EvidenceLevel::Measured);
            json!({"name":metric.name,"value":metric.value,"unit":metric.unit})
        })
        .collect();
    assert_eq!(json!(metrics), case["metrics"], "metrics");
    assert_eq!(json!(limitations), case["limitations"], "limitations");
    (case["metrics"].clone(), case["limitations"].clone())
}

#[test]
fn scored_components_and_raw_count_workspace_match_the_frozen_run_metrics() {
    for case in [
        "python-lines-and-functions",
        "two-components-merge",
        "branch-and-last-wins",
    ] {
        let (metrics, limitations) = frozen(case);
        assert!(!metrics.as_array().unwrap().is_empty(), "{case}");
        assert_eq!(limitations, json!([]), "{case}");
    }
}

#[test]
fn missing_coverage_or_function_evidence_is_a_limitation_never_a_score() {
    for case in ["no-coverage", "python-no-functions"] {
        let (metrics, limitations) = frozen(case);
        assert_eq!(metrics, json!([]), "{case}");
        assert_eq!(limitations.as_array().unwrap().len(), 1, "{case}");
    }
}

#[test]
fn invalid_percent_only_unmarked_and_unscorable_workspace_inputs_match_frozen() {
    for case in [
        "invalid-counts",
        "percent-only-excluded",
        "unmarked-and-zero-cases",
        "workspace-not-scored",
    ] {
        frozen(case);
    }
}
