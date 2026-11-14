//! Frozen v2 intake edges: Python str/int/bool/float coercion, Windows roots and redaction.
use ici_reporting::{StoredResult, migrate_report_payload};
use serde_json::{Value, json};

#[test]
fn malformed_v2_fields_coerce_exactly_like_the_frozen_reader() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/migration-v2-edges.json"
    ))
    .unwrap();
    let mut accepted = 0;
    for case in corpus["cases"].as_array().unwrap() {
        if case.get("error").is_some() {
            continue;
        }
        let actual = migrate_report_payload(&case["input"], case["project_root"].as_str())
            .unwrap_or_else(|error| panic!("{}: {error}", case["name"]));
        assert_eq!(actual.to_value(), case["expected"], "{}", case["name"]);
        accepted += 1;
    }
    assert_eq!(accepted, 10);
}

#[test]
fn paths_outside_or_ambiguous_to_the_root_are_refused_like_the_frozen_reader() {
    let corpus: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/migration-v2-edges.json"
    ))
    .unwrap();
    let refused: Vec<_> = corpus["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case.get("error").is_some())
        .collect();
    assert_eq!(refused.len(), 3);
    for case in refused {
        assert_eq!(case["error"], "ValueError", "{}", case["name"]);
        assert!(
            migrate_report_payload(&case["input"], case["project_root"].as_str()).is_err(),
            "{}",
            case["name"]
        );
        assert!(StoredResult::from_value(case["input"].clone()).is_err());
    }
}

#[test]
fn secret_flag_values_in_argv_are_hidden_at_every_output_boundary() {
    let payload = json!({"schema_version":"ici.result/v2","engine_name":"lint","status":"FAIL",
        "tool_evidence":[{"name":"tool","argv":["tool","--TOKEN","plain-value","--password=x","next"]}]});
    let migrated = migrate_report_payload(&payload, None).unwrap().to_value();
    assert_eq!(
        migrated["tool_evidence"][0]["argv"],
        json!([
            "tool",
            "--TOKEN",
            "***REDACTED***",
            "--password=***REDACTED***",
            "next"
        ])
    );
    // A stored v3 archive keeps raw bytes; the JSON output boundary hides the value.
    let mut stored = migrated.clone();
    stored["tool_evidence"][0]["argv"] = json!(["tool", "--token", "plain-value"]);
    let rendered = ici_reporting::render_json(&StoredResult::from_value(stored).unwrap()).unwrap();
    assert!(!rendered.contains("plain-value"), "{rendered}");
}
