//! Frozen `core/support.py` declarations and `evaluate_support_matrix` outputs
//! (`tests/rust-parity/python/capture_legacy_support.py`, CPython 3.10.21) replayed exactly.
use ici_reporting::{LegacySupportInputs, legacy_support_declarations, legacy_support_matrix};
use serde_json::Value;

fn oracle() -> Value {
    serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-support-oracle.json"
    ))
    .unwrap()
}

#[test]
fn support_declarations_are_the_frozen_registry() {
    let oracle = oracle();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    assert_eq!(legacy_support_declarations(), oracle["declarations"]);
    assert_eq!(oracle["declarations"].as_array().unwrap().len(), 38);
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap().to_owned())
        .collect()
}

#[test]
fn support_evaluation_matches_frozen_profiles_policies_and_language_evidence() {
    let oracle = oracle();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 19);
    for (name, case) in cases {
        let frozen = &case["frozen"];
        let inputs = LegacySupportInputs {
            languages: strings(&frozen["project_languages"]),
            frameworks: strings(&frozen["project_frameworks"]),
            profile: case["profile"].as_str().unwrap().to_owned(),
            engines: case["engines"].clone(),
        };
        let names = case["engine_names"].as_array().map(|names| {
            names
                .iter()
                .map(|n| n.as_str().unwrap())
                .collect::<Vec<_>>()
        });
        let actual = legacy_support_matrix(
            &inputs,
            case["results"].as_array().unwrap(),
            names.as_deref(),
        );
        assert_eq!(&actual, frozen, "{name}");
    }
}

#[test]
fn support_evaluation_fails_closed_on_missing_or_malformed_results() {
    // A result without evidence never claims an observed mode, and a malformed per-language
    // value falls back to the engine's own evidence (frozen `_language_evidence`).
    let inputs = LegacySupportInputs {
        languages: vec!["python".into()],
        frameworks: vec![],
        profile: "standard".into(),
        engines: serde_json::json!({}),
    };
    let results =
        [serde_json::json!({"engine_name":"dead","extra":{"language_evidence":{"python":7}}})];
    let matrix = legacy_support_matrix(&inputs, &results, Some(&["dead"]));
    let python = &matrix["entries"][0];
    assert_eq!(python["evidence"], "NOT_RUN");
    assert_eq!(python["active_mode"], Value::Null);
    assert_eq!(python["confidence"], "low");
    // Unknown profile disables every descriptor rather than enabling deep-only engines.
    let unknown = LegacySupportInputs {
        profile: "bogus".into(),
        ..inputs
    };
    let matrix = legacy_support_matrix(&unknown, &[], None);
    assert!(
        matrix["entries"]
            .as_array()
            .unwrap()
            .iter()
            .all(|entry| entry["enabled"] == false)
    );
}
