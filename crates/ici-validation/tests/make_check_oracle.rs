//! Frozen `make check` transcript parsing (`tests/rust-parity/cpp/capture_make_check.py`): the
//! real qmake QTest `make check TESTARGS=-xunitxml` transcript and bounded synthetic transcripts
//! replayed through the four frozen functions.
use ici_validation::make_check::{
    MakeCase, attach_sanitizer_output, parse_ctest_junit, parse_ctest_stdout,
    parse_make_check_stdout, parse_qtest_xunit, qmake_results,
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
    assert_eq!(cases.len(), 35);
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
            "ctest": view(&parse_ctest_stdout(text).into_iter().map(|(_, case)| case).collect::<Vec<_>>()),
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

/// Every report of the corpus through frozen `parse_ctest_junit` (expat's refusals, ElementTree's
/// namespaced names and text assembly), against the frozen case list.
#[test]
fn ctest_reports_are_read_exactly_as_elementtree_reads_them() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/make-check-oracle.json"
    ))
    .unwrap();
    let reports = oracle["junit"].as_array().unwrap();
    assert!(reports.len() > 1500);
    let accepted = reports
        .iter()
        .filter(|report| !report["frozen"].as_array().unwrap().is_empty())
        .count();
    // Both sides of the reader are exercised: well-formed documents and refused ones.
    assert!(
        accepted > 300 && reports.len() - accepted > 800,
        "{accepted}"
    );
    let mut mismatches = 0;
    for report in reports {
        let document = report["document"].as_str().unwrap();
        let native = view(&parse_ctest_junit(document));
        if native != report["frozen"] {
            mismatches += 1;
            eprintln!(
                "== {document:?}\nfrozen: {}\nnative: {native}",
                report["frozen"]
            );
        }
    }
    assert_eq!(mismatches, 0);
    // Positive and negative of the same shape: a plain case, then the same in a namespace.
    assert_eq!(
        view(&parse_ctest_junit(
            r#"<testsuite><testcase name="a"/></testsuite>"#
        ))[0]["passed"],
        true
    );
    assert!(
        parse_ctest_junit(r#"<testsuite xmlns="u"><testcase name="a"/></testsuite>"#).is_empty()
    );
}

/// A deep report needs no recursion: frozen reads this one, and so must the reader.
#[test]
fn a_deeply_nested_report_does_not_exhaust_the_stack() {
    let depth = 100_000;
    let document = format!(
        "{}<testcase name=\"deep\"/>{}",
        "<a>".repeat(depth),
        "</a>".repeat(depth)
    );
    assert_eq!(parse_ctest_junit(&document)[0].name, "deep");
}
