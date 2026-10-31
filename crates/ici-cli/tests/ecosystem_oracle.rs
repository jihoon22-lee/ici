//! Actual ESLint, tsc and Cargo Clippy through `ici analyze`, compared with the frozen oracle.
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
};

fn tool_dir(variable: &str) -> PathBuf {
    let tool = std::env::var_os(variable).unwrap_or_else(|| panic!("set {variable}"));
    Path::new(&tool).parent().unwrap().to_owned()
}

#[test]
#[ignore = "explicit actual ESLint/tsc/Cargo gate; no tool installation or silent skip"]
fn actual_ecosystem_findings_and_fingerprints_match_frozen_analyze() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/providers/ecosystem-e2e-oracle.json"
    ))
    .unwrap();
    // Cargo writes its target directory below the project: keep it out of /tmp.
    let root = tempfile::tempdir_in(env!("CARGO_TARGET_TMPDIR")).unwrap();
    for (name, text) in oracle["files"].as_object().unwrap() {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let mut path = vec![
        tool_dir("ICI_TEST_ESLINT"),
        tool_dir("ICI_TEST_TSC"),
        tool_dir("ICI_TEST_CARGO"),
    ];
    // ESLint and tsc are `#!/usr/bin/env node` scripts; prefer the pinned Node when supplied.
    if std::env::var_os("ICI_TEST_NODE").is_some() {
        path.insert(0, tool_dir("ICI_TEST_NODE"));
    }
    path.extend(std::env::split_paths(&std::env::var_os("PATH").unwrap()));
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["analyze", "--json", "--result", "result.json"])
        .current_dir(root.path())
        .env("PATH", std::env::join_paths(path).unwrap())
        .output()
        .unwrap();
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(
        output.status.code(),
        oracle["exit"].as_i64().map(|code| code as i32),
        "{stderr}"
    );
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join("result.json")).unwrap()).unwrap();
    let states: serde_json::Map<String, Value> = result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            (
                check["check_id"].as_str().unwrap().into(),
                check["state"].clone(),
            )
        })
        .collect();
    assert_eq!(Value::Object(states), oracle["checks"]);
    let mut findings: Vec<Value> = result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            let location = &item["primary_location"];
            json!({
                "rule_id": item["rule_id"],
                "path": location["path"],
                "line": location["start_line"],
                "column": location["start_column"],
                "message": item["message"],
                "fingerprint": item["fingerprint"],
            })
        })
        .collect();
    findings.sort_by_key(|item| {
        (
            item["path"].as_str().unwrap().to_owned(),
            item["line"].as_u64().unwrap(),
            item["rule_id"].as_str().unwrap().to_owned(),
        )
    });
    assert_eq!(Value::from(findings), oracle["findings"]);
    // Intended difference: native never claims complete evidence for an ESLint/Cargo/TS run whose
    // plugin/configuration/dependency closure was not enumerated (frozen claimed it for ESLint).
    assert!(
        result["checks"]
            .as_array()
            .unwrap()
            .iter()
            .all(|check| check["evidence_complete"] == false)
    );
    assert!(!root.path().join("src/b.js").exists());
}
