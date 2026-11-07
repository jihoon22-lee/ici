//! Frozen `next verify` pytest provider identities replayed through the native application.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

const ORACLE: &str = include_str!("../../../tests/rust-parity/validation/pytest-next-oracle.json");

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn run(scenario: &Value, config: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("tests")).unwrap();
    fs::write(root.path().join("app.py"), "X = 1\n").unwrap();
    for (path, text) in scenario["files"].as_object().unwrap() {
        fs::write(root.path().join(path), text.as_str().unwrap()).unwrap();
    }
    fs::write(
        root.path().join("ici.toml"),
        config.replace("\"<PYTHON>\"", &serde_json::to_string(&python()).unwrap()),
    )
    .unwrap();
    commands::dispatch(
        &registry::parse(["ici", "next", "verify", "--python", "--no-cache"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
        .unwrap()
}
fn pytest_findings(result: &Value) -> Vec<Value> {
    result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["provider"] == "pytest")
        .cloned()
        .collect()
}
fn state(result: &Value, check: &str) -> Value {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["check_id"] == check)
        .map(|item| item["state"].clone())
        .unwrap()
}

#[test]
#[ignore = "explicit project pytest provider identity acceptance"]
fn failed_case_identity_matches_frozen_and_the_spurious_summary_collection_error_is_gone() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["failing"];
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let native = pytest_findings(&result);
    let failed = &frozen["findings"]
        .as_array()
        .unwrap()
        .iter()
        .find(|finding| finding["rule_id"] == "pytest.failed")
        .unwrap();
    assert_eq!(native.len(), 1, "{native:?}");
    for key in ["rule_id", "message", "fingerprint"] {
        assert_eq!(native[0][key], failed[key], "{key}");
    }
    assert_eq!(native[0]["primary_location"]["path"], failed["path"]);
    // Frozen `(?:collecting\s+)?` also matched the short-summary `FAILED tests/x.py::t` line.
    assert!(
        frozen["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["rule_id"] == "pytest.collection-error")
    );
    assert_eq!(state(&result, "python.test"), "SUCCEEDED");
    let cases = result["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .find(|metric| metric["name"] == "pytest.cases")
        .unwrap();
    assert_eq!(cases["numerator"], frozen["metrics"][0]["numerator"]);
    assert_eq!(cases["denominator"], frozen["metrics"][0]["denominator"]);
}

#[test]
#[ignore = "explicit project pytest collection-error identity acceptance"]
fn collection_error_identity_matches_frozen_but_the_suite_stays_incomplete() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["collection"];
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let native = pytest_findings(&result);
    assert_eq!(native.len(), 1, "{native:?}");
    for key in ["rule_id", "message", "fingerprint"] {
        assert_eq!(native[0][key], frozen["findings"][0][key], "{key}");
    }
    // Frozen marked the interrupted suite SUCCEEDED; its scope is not complete evidence.
    assert_eq!(frozen["checks"]["python.test"], "SUCCEEDED");
    assert_ne!(state(&result, "python.test"), "SUCCEEDED");
}

/// The python.test facts of one run: state, evidence completeness, the test-related run
/// limitations, the pytest findings' rule ids and the pytest.cases measurement.
fn test_facts(result: &Value) -> Value {
    let check = result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["check_id"] == "python.test")
        .unwrap();
    serde_json::json!({
        "gate": result["gate"]["selected"],
        "state": check["state"],
        "evidence_complete": check["evidence_complete"],
        "limitations": result["limitations"].as_array().unwrap().iter()
            .filter(|text| text.as_str().is_some_and(|text| text.contains("pytest") || text.contains("test")))
            .cloned().collect::<Vec<_>>(),
        "findings": pytest_findings(result).iter().map(|finding| finding["rule_id"].clone()).collect::<Vec<_>>(),
        "cases": result["metrics"].as_array().unwrap().iter()
            .filter(|metric| metric["name"] == "pytest.cases")
            .map(|metric| serde_json::json!([metric["numerator"], metric["denominator"]]))
            .collect::<Vec<_>>(),
    })
}
fn frozen_facts(frozen: &Value) -> Value {
    serde_json::json!({
        "gate": frozen["gate"],
        "state": frozen["test_check"]["state"],
        "evidence_complete": frozen["test_check"]["evidence_complete"],
        "limitations": frozen["limitations"],
        "findings": frozen["findings"].as_array().unwrap().iter().map(|finding| finding["rule_id"].clone()).collect::<Vec<_>>(),
        "cases": frozen["metrics"].as_array().unwrap().iter()
            .map(|metric| serde_json::json!([metric["numerator"], metric["denominator"]]))
            .collect::<Vec<_>>(),
    })
}

/// Green, all-skipped, nothing-collected and `pytest.exit` runs (frozen
/// adapters/providers/pytest.py exit contract and limitations) against frozen `next verify`.
#[test]
#[ignore = "explicit project pytest provider contract acceptance"]
fn green_skipped_empty_and_exited_suites_keep_the_frozen_provider_contract() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    // Native-only plan notes on every native run (the visible coverage/unittest branch policy).
    const NOTES: [&str; 2] = [
        "coverage capability selects one visible suite branch",
        "only an exact missing-pytest diagnostic activates",
    ];
    // Reviewed differences: (scenario, fact, native value, reason).
    let corrections: [(&str, &str, Value, &str); 4] = [
        (
            "no-tests",
            "limitations",
            serde_json::json!([
                "pytest did not report a completed nonempty suite (exit 5)",
                "prerequisite project.python.test has no complete evidence"
            ]),
            "Same FAILED state and exit 5; native words the refusal and the blocked coverage prerequisite (the exit was Rust Debug `Some(5)` before this sweep).",
        ),
        (
            "interrupted",
            "state",
            "FAILED".into(),
            "Frozen defect not reproduced: pytest.exit(returncode=2) aborted a collected test and frozen reported SUCCEEDED with 'pytest collected no tests'; native refuses the aborted run as incomplete.",
        ),
        (
            "interrupted",
            "evidence_complete",
            false.into(),
            "Follows the FAILED state above.",
        ),
        (
            "interrupted",
            "limitations",
            serde_json::json!([
                "pytest did not report a completed nonempty suite (exit 2)",
                "prerequisite project.python.test has no complete evidence"
            ]),
            "Native refusal names the real exit instead of frozen's wrong 'collected no tests'.",
        ),
    ];
    let mut wrong = vec![];
    for name in ["green", "all-skipped", "no-tests", "interrupted"] {
        let frozen = &oracle["runs"][name];
        let mut native = test_facts(&run(frozen, oracle["config"].as_str().unwrap()));
        native["limitations"]
            .as_array_mut()
            .unwrap()
            .retain(|text| {
                !NOTES
                    .iter()
                    .any(|note| text.as_str().unwrap().starts_with(note))
            });
        let mut expected = frozen_facts(frozen);
        for (_, fact, value, reason) in corrections.iter().filter(|(case, ..)| *case == name) {
            assert!(!reason.is_empty());
            expected[*fact] = value.clone();
        }
        if native != expected {
            wrong.push(format!("{name}\nnative {native}\nfrozen {expected}"));
        }
    }
    assert!(wrong.is_empty(), "{}", wrong.join("\n"));
}

#[test]
#[ignore = "explicit project pytest provider identity acceptance"]
fn an_escaping_node_id_keeps_its_verdict_at_the_tests_scope_where_frozen_crashed() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["escaping-node"];
    // Frozen `_node_location` meant to keep the verdict at `tests`, but its short-summary
    // collection match produced a `../outside` span and the run ended in an internal error.
    assert!(
        frozen["limitations"][0]
            .as_str()
            .unwrap()
            .contains("span path must be relative and contained")
    );
    assert_eq!(frozen["checks"]["python.test"], "FAILED");
    let result = run(frozen, oracle["config"].as_str().unwrap());
    let native = pytest_findings(&result);
    assert_eq!(state(&result, "python.test"), "SUCCEEDED", "{result:#}");
    assert_eq!(native.len(), 1, "{native:?}");
    assert_eq!(native[0]["rule_id"], "pytest.failed");
    assert_eq!(
        native[0]["message"],
        "../outside/test_g.py::test_bad failed"
    );
    assert_eq!(native[0]["primary_location"]["path"], "tests");
    assert_eq!(native[0]["primary_location"]["start_line"], 1);
}
