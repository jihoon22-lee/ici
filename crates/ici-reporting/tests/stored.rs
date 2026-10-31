use ici_reporting::{StoredResult, script_json, strict_json};
use serde_json::{Value, json};
#[test]
fn published_modern_and_legacy_envelopes_roundtrip_without_schema_confusion() {
    for (name, bytes) in [
        (
            "success",
            include_bytes!("../../../tests/fixtures/ici-next/run-success.json").as_slice(),
        ),
        (
            "partial",
            include_bytes!("../../../tests/fixtures/ici-next/run-partial-selection.json")
                .as_slice(),
        ),
        (
            "incomplete",
            include_bytes!("../../../tests/fixtures/ici-next/run-required-incomplete.json")
                .as_slice(),
        ),
        (
            "cancelled",
            include_bytes!("../../../tests/fixtures/ici-next/run-cancelled.json").as_slice(),
        ),
        (
            "legacy-suite",
            include_bytes!("../../../tests/fixtures/ici-next/legacy-v3-suite.json").as_slice(),
        ),
        (
            "legacy-archive",
            include_bytes!("../../../tests/fixtures/ici-next/legacy-v3-result.json").as_slice(),
        ),
    ] {
        let expected: Value = serde_json::from_slice(bytes).unwrap();
        let stored =
            StoredResult::from_bytes(bytes).unwrap_or_else(|error| panic!("{name}: {error}"));
        assert_eq!(stored.to_value().unwrap(), expected, "{name}");
        let roundtrip = StoredResult::from_bytes(stored.to_json().unwrap().as_bytes()).unwrap();
        assert_eq!(roundtrip, stored);
    }
}
#[test]
fn reader_preserves_unknown_legacy_extension_data_but_refuses_unknown_schemas_and_invalid_regions()
{
    let mut value: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/legacy-v3-suite.json"
    ))
    .unwrap();
    value["producer_extension"] = json!({"nested":["retain","all"]});
    value["results"][0]["custom"] = json!({"score_breakdown":[1,2,3]});
    assert_eq!(
        StoredResult::from_value(value.clone())
            .unwrap()
            .to_value()
            .unwrap(),
        value
    );
    for value in [
        json!({"schema_id":"ici.next.run","schema_version":3}),
        json!({"schema_version":"ici.result/v9","version":3,"results":[],"suite_status":"PASS"}),
        json!({"schema_id":"ici.auxiliary","schema_version":1}),
        json!({"schema_version":"ici.result/v3","engine_name":"lint","status":"PASS","targets":[{"file_path":"x.py","start_line":2,"end_line":1,"status":"PASS"}]}),
    ] {
        assert!(StoredResult::from_value(value).is_err());
    }
}
#[test]
fn duplicate_keys_trailing_data_and_embedded_script_closures_are_handled_at_intake_and_output() {
    assert!(strict_json(br#"{"status":"PASS","status":"FAIL"}"#).is_err());
    assert!(strict_json(b"{}{} ").is_err());
    assert!(strict_json(b"{\"value\":1e999}").is_err());
    let encoded =
        script_json(&json!({"message":"</script><script>alert('x')</script> & \u{2028}\u{2029}"}))
            .unwrap();
    assert!(!encoded.to_ascii_lowercase().contains("</script"));
    assert!(!encoded.contains('<'));
    assert!(!encoded.contains('&'));
    let decoded: Value = serde_json::from_str(&encoded).unwrap();
    assert_eq!(
        decoded["message"],
        "</script><script>alert('x')</script> & \u{2028}\u{2029}"
    );
}
#[test]
fn embedded_modern_facts_cannot_bypass_strict_saved_result_validation() {
    let mut legacy: Value = serde_json::from_slice(include_bytes!(
        "../../../tests/fixtures/ici-next/legacy-v3-suite.json"
    ))
    .unwrap();
    legacy["native_run"] =
        json!({"schema_id":"ici.next.run","schema_version":2,"gate":{"selected":"PASS"}});
    assert!(StoredResult::from_value(legacy.clone()).is_err());
    legacy["native_run"] = json!({"producer_extension":"unrelated historical extension"});
    assert_eq!(
        StoredResult::from_value(legacy.clone())
            .unwrap()
            .to_value()
            .unwrap(),
        legacy
    );
}
