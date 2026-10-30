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
