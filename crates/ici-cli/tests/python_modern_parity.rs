//! Replays the frozen modern Python projects in `tests/rust-parity/python/modern-findings.json`
//! through the native CLI and compares every check state, finding, measurement, limitation,
//! gate and exit code with the recorded original observation. Reviewed differences are
//! explicit per-case `corrections` with a reason; the original observation stays unchanged.
use ici_cli::{commands, execute, registry};
use ici_execution::Cancellation;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const FIXTURE: &str = include_str!("../../../tests/rust-parity/python/modern-findings.json");
const ALL_CHECKS: [&str; 16] = [
    "line",
    "lint",
    "format",
    "type",
    "test",
    "coverage",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "security",
    "resource",
    "exception",
    "dead",
    "compat",
    "compat-runtime",
];

fn materialize(root: &Path, case: &Value) {
    let checks: Vec<&str> = case["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| check.as_str().unwrap())
        .collect();
    let mut config = String::from(
        "schema_version=1\n[workspace]\nname=\"oracle\"\nprofile=\"fast\"\n[[components]]\nid=\"project\"\nroot=\".\"\nlanguages=[\"python\"]\n",
    );
    for check in ALL_CHECKS {
        if !checks.contains(&check) {
            config.push_str(&format!("[checks.\"python.{check}\"]\nenabled=false\n"));
        }
    }
    fs::write(root.join("ici.toml"), config).unwrap();
    for (relative, text) in case["files"].as_object().unwrap() {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        let text = text.as_str().unwrap();
        let bytes = match case["encoding"].as_str().unwrap() {
            "utf-8" => text.as_bytes().to_vec(),
            "latin-1" => text
                .chars()
                .map(|ch| u8::try_from(u32::from(ch)).unwrap())
                .collect(),
            other => panic!("unsupported fixture encoding {other}"),
        };
        fs::write(path, bytes).unwrap();
    }
}

fn normalize(document: &Value, out_of_scope: &[Value]) -> BTreeMap<&'static str, Value> {
    let checks: Vec<Value> = document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            json!({"check_id":check["check_id"],"state":check["state"],"evidence_complete":check["evidence_complete"]})
        })
        .collect();
    let findings: Vec<Value> = document["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            let mut finding = finding.clone();
            let object = finding.as_object_mut().unwrap();
            for key in ["task_id", "fingerprint", "analysis_unit_id"] {
                object.remove(key);
            }
            finding
        })
        .collect();
    let limitations: Vec<Value> = document["limitations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|line| !out_of_scope.contains(line))
        .cloned()
        .collect();
    BTreeMap::from([
        ("checks", json!(checks)),
        ("findings", json!(findings)),
        ("metrics", document["metrics"].clone()),
        ("limitations", json!(limitations)),
        ("gate", document["gate"].clone()),
    ])
}

#[test]
fn modern_python_checks_match_frozen_projects_and_reviewed_corrections() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    assert_eq!(
        fixture["source"],
        "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let out_of_scope = fixture["out_of_scope_limitations"]
        .as_array()
        .unwrap()
        .clone();
    let mut compared = 0;
    for case in fixture["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let corrections = &case["corrections"];
        for key in corrections["expected"].as_object().unwrap().keys() {
            assert!(
                corrections["reasons"][key]
                    .as_str()
                    .is_some_and(|reason| !reason.is_empty()),
                "{id}: correction {key} needs a reason"
            );
        }
        let expect = |key: &str| -> Value {
            corrections["expected"]
                .get(key)
                .cloned()
                .unwrap_or_else(|| case["oracle"][key].clone())
        };
        let mut oracle = normalize(&case["oracle"], &out_of_scope);
        for (key, value) in corrections["expected"].as_object().unwrap() {
            if key != "exit_code" {
                let key = *oracle.keys().find(|candidate| *candidate == key).unwrap();
                oracle.insert(key, value.clone());
            }
        }

        let directory = tempfile::tempdir().unwrap();
        materialize(directory.path(), case);
        let environment = BTreeMap::from([("PATH".to_string(), String::new())]);
        let output = commands::dispatch(
            &registry::parse(["ici", "analyze", "--json"]).unwrap(),
            directory.path(),
            &environment,
        )
        .unwrap();
        assert_eq!(
            json!(output.exit_code),
            expect("exit_code"),
            "{id}: exit code"
        );
        let document: Value = serde_json::from_str(&output.stdout).unwrap();
        let native = normalize(&document, &out_of_scope);
        for key in ["checks", "findings", "metrics", "limitations", "gate"] {
            assert_eq!(native[key], oracle[key], "{id}: {key}");
        }

        // Finding identity: the attributed fingerprint covers the provider's raw
        // fingerprint. Recompute it without the native analysis-unit attribution
        // and require the original value for every finding the original also had.
        let directory = tempfile::tempdir().unwrap();
        materialize(directory.path(), case);
        let run = execute::execute(
            &registry::parse(["ici", "analyze"]).unwrap(),
            directory.path(),
            &environment,
            true,
            &Cancellation::default(),
        )
        .unwrap();
        let original: Vec<&Value> = case["oracle"]["findings"]
            .as_array()
            .unwrap()
            .iter()
            .collect();
        for finding in run.result.findings.iter() {
            let raw = run.outputs[finding.task_id.as_ref().unwrap()]
                .observation
                .findings
                .iter()
                .find(|raw| {
                    raw.rule_id == finding.rule_id
                        && raw.message == finding.message
                        && raw.primary_location == finding.primary_location
                })
                .unwrap_or_else(|| panic!("{id}: raw finding for {}", finding.rule_id));
            let mut unattributed = finding.clone();
            unattributed.analysis_unit_id = None;
            unattributed.fingerprint = raw.fingerprint.clone();
            let fingerprint = ici_domain::attributed_fingerprint(&unattributed, &[]).unwrap();
            let mut shape = serde_json::to_value(finding).unwrap();
            let object = shape.as_object_mut().unwrap();
            for key in ["task_id", "fingerprint", "analysis_unit_id"] {
                object.remove(key);
            }
            if let Some(matched) = original.iter().find(|item| {
                let mut item = (**item).clone();
                let object = item.as_object_mut().unwrap();
                for key in ["task_id", "fingerprint", "analysis_unit_id"] {
                    object.remove(key);
                }
                item == shape
            }) {
                assert_eq!(
                    json!(fingerprint),
                    matched["fingerprint"],
                    "{id}: fingerprint of {}",
                    finding.rule_id
                );
                compared += 1;
            }
        }
    }
    assert!(compared >= 30, "only {compared} fingerprints compared");
}
