//! Frozen console of legacy single-engine commands, captured from the frozen printers with
//! rich 15.0.0 (tests/rust-parity/reporting/capture_engine_console.py).
use ici_reporting::legacy_engine_console;
use serde_json::Value;

#[test]
fn single_engine_console_matches_the_frozen_rich_output_byte_for_byte() {
    let golden: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/engine-console.json"
    ))
    .unwrap();
    assert_eq!(golden["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = golden["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 11);
    for case in cases {
        let actual = legacy_engine_console(&case["engine"], case["report"].as_str());
        assert_eq!(
            actual,
            case["stdout"].as_str().unwrap(),
            "{}",
            case["engine"]
        );
    }
}
