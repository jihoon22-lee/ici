//! Frozen next `python.compat-runtime` facts (`adapters/providers/pycompat.py`): the measured
//! runtime and requires-python limitations, the `compileall.checked` measurement and the
//! runtime-version finding (`tests/rust-parity/validation/compat-runtime-facts-oracle.json`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/compat-runtime-facts-oracle.json");
const RUNTIME: [&str; 3] = ["runtime", "requires-python", "floor"];

fn run(oracle: &Value, scenario: &str) -> Value {
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter");
    let root = tempfile::tempdir().unwrap();
    for (path, text) in oracle["runs"][scenario]["files"].as_object().unwrap() {
        fs::write(root.path().join(path), text.as_str().unwrap()).unwrap();
    }
    let config = oracle["config"]
        .as_str()
        .unwrap()
        .replace("<PYTHON_JSON>", &serde_json::to_string(&python).unwrap());
    fs::write(root.path().join("ici.toml"), config).unwrap();
    commands::dispatch(
        &registry::parse(["ici", "next", "verify", "--python", "--no-cache"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    let limitations: Vec<_> = result["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|text| {
            let text = text.as_str().unwrap();
            RUNTIME.iter().any(|word| text.contains(word))
        })
        .cloned()
        .collect();
    let metrics: Vec<_> = result["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|metric| metric["name"] == "compileall.checked")
        .map(|metric| json!({"name":metric["name"],"value":metric["value"],"unit":metric["unit"]}))
        .collect();
    let findings: Vec<_> = result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| {
            finding["rule_id"]
                .as_str()
                .unwrap()
                .starts_with("python.compat.")
        })
        .map(|finding| json!({"rule_id":finding["rule_id"],"message":finding["message"]}))
        .collect();
    json!({"limitations":limitations,"metrics":metrics,"findings":findings})
}

fn compare(scenario: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"][scenario];
    let native = run(&oracle, scenario);
    let metrics: Vec<_> = frozen["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .map(|metric| json!({"name":metric["name"],"value":metric["value"],"unit":metric["unit"]}))
        .collect();
    assert_eq!(native["metrics"], json!(metrics), "{scenario}");
    assert_eq!(native["findings"], frozen["findings"], "{scenario}");
    // Every frozen runtime limitation is reported with frozen's wording.
    for text in frozen["limitations"].as_array().unwrap() {
        assert!(
            native["limitations"].as_array().unwrap().contains(text),
            "{scenario}: {text} missing from {:#}",
            native["limitations"]
        );
    }
}

#[test]
#[ignore = "explicit project interpreter compat-runtime acceptance"]
fn a_satisfied_floor_reports_the_measured_runtime_and_the_verdict() {
    compare("satisfied");
}

#[test]
#[ignore = "explicit project interpreter compat-runtime acceptance"]
fn a_runtime_outside_requires_python_is_the_frozen_finding() {
    compare("outside");
}

#[test]
#[ignore = "explicit project interpreter compat-runtime acceptance"]
fn no_declared_floor_is_reported_with_the_frozen_limitation() {
    compare("no-floor");
}
