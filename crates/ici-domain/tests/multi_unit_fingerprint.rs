//! Frozen `finding_identity._fingerprint` for a task declaring several analysis units
//! (`tests/rust-parity/reporting/multi-unit-fingerprint.json`, `capture_multi_unit_fingerprint.py`).
use ici_domain::{Finding, SourceSpan, attributed_fingerprint};
use serde_json::Value;

fn finding(native: &str, unit: Option<&str>) -> Finding {
    let span = SourceSpan {
        end_line: Some(3),
        start_column: Some(1),
        end_column: Some(9),
        ..SourceSpan::line("pkg/main.py", 3)
    };
    let mut finding = Finding::new("python.line.limit", "line limit exceeded é", span, "ici");
    finding.fingerprint = native.into();
    finding.component_id = Some("app".into());
    finding.analysis_unit_id = unit.map(str::to_owned);
    finding
}

#[test]
fn several_units_append_the_sorted_unit_tuple_like_frozen() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/multi-unit-fingerprint.json"
    ))
    .unwrap();
    // Native planning sorts declared units (plan.rs task_analysis_units), as frozen sorts them.
    let mut units: Vec<String> = oracle["units"]
        .as_array()
        .unwrap()
        .iter()
        .map(|unit| unit.as_str().unwrap().to_owned())
        .collect();
    units.sort();
    let cases = oracle["findings"].as_array().unwrap();
    assert_eq!(cases.len(), 3);
    for case in cases {
        // The frozen attributed unit: kept when declared, cleared when foreign or absent.
        let unit = case["analysis_unit_id"].as_str();
        let item = finding(case["native_fingerprint"].as_str().unwrap(), unit);
        assert_eq!(
            attributed_fingerprint(&item, &units).unwrap(),
            case["fingerprint"].as_str().unwrap(),
            "{case}"
        );
    }
    // Failure modes: declaration order or a dropped unit tuple would change every identity.
    let item = finding("native-0", Some("app.python.a"));
    let expected = cases[0]["fingerprint"].as_str().unwrap();
    let unsorted: Vec<String> = units.iter().rev().cloned().collect();
    assert_ne!(attributed_fingerprint(&item, &unsorted).unwrap(), expected);
    assert_ne!(
        attributed_fingerprint(&item, &units[..1]).unwrap(),
        expected
    );
}
