//! Frozen `make check` transcript parsing (`tests/rust-parity/cpp/capture_make_check.py`): the
//! real qmake QTest `make check TESTARGS=-xunitxml` transcript and bounded synthetic transcripts
//! replayed through the four frozen functions.
use ici_validation::make_check::{
    MakeCase, attach_sanitizer_output, parse_make_check_stdout, parse_qtest_xunit, qmake_results,
};
use serde_json::{Value, json};

fn view(cases: &[MakeCase]) -> Value {
    json!(
        cases
            .iter()
            .map(|case| json!({
                "name": case.name, "passed": case.passed, "message": case.message,
                "executed": case.executed, "diagnostic_output": case.diagnostic_output,
                "diagnostic_output_truncated": case.diagnostic_output_truncated,
            }))
            .collect::<Vec<_>>()
    )
}

#[test]
fn make_check_transcripts_match_the_frozen_parsers_exactly() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/make-check-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 16);
    let mut mismatches = vec![];
    for (name, case) in cases {
        let text = case["transcript"].as_str().unwrap();
        let code = case["returncode"].as_i64().unwrap() as i32;
        let merged = qmake_results(text, code);
        let native = json!({
            "make": view(&parse_make_check_stdout(text, code)),
            "xunit": view(&parse_qtest_xunit(text)),
            "qmake": view(&merged),
            "attached": view(&attach_sanitizer_output(merged, text)),
        });
        if native != case["frozen"] {
            eprintln!(
                "== {name}\nfrozen: {}\nnative: {}",
                serde_json::to_string_pretty(&case["frozen"]).unwrap(),
                serde_json::to_string_pretty(&native).unwrap()
            );
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
    // Positive: the failed QTest binary carries both failed cases. Negative: a passing binary
    // has no message. Failure: a non-zero exit without an Error line blames the last binary.
    let qmake = &cases["test-qmake"]["frozen"]["qmake"][0];
    assert_eq!(qmake["name"], "test_unit");
    assert!(
        qmake["message"]
            .as_str()
            .unwrap()
            .starts_with("make check failed — Unit::fails: ")
    );
    assert_eq!(cases["plain-pass"]["frozen"]["qmake"][0]["message"], "");
    assert_eq!(
        cases["nonzero-no-error-line"]["frozen"]["make"][1]["message"],
        "make check exited non-zero after this test started"
    );
}
