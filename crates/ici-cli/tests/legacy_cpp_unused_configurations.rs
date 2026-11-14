//! Legacy `ici verify` C++ unused-function configuration consensus (frozen
//! `_cpp_unused_functions._merge_observations`) against frozen v0.13.0 runs
//! (`tests/rust-parity/cpp/unused-configurations-oracle.json`, `capture_unused_configurations.py`)
//! over parity-fixtures/cpp/unused-configurations: configurations that disagree on a source's
//! `-Wunused-function` diagnostics are a `C++UnusedFunctionError` that discards every source's
//! result (`C++UnusedFunctionsInvalidated`, ERROR NOT_RUN, exit 1), never an intersection;
//! agreeing configurations keep the `Compiler:-Wunused-function` warning "in all 2" and clean
//! sources their `C++UnusedFunctions` PASS. Compared: exit code, suite status, dead engine status,
//! evidence, summary, targets, findings and tool evidence (name, return code).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

#[test]
#[ignore = "explicit actual /usr/bin/g++ unused-function probes (the captured g++ version, ICI_TEST_GXX)"]
fn legacy_unused_function_configurations_must_agree_like_frozen() {
    compare(true);
}

/// The same trees with whatever `/usr/bin/g++` the host has (the CI runner's): the compared
/// facts (verdicts, targets, findings, tool evidence names and return codes) name no version.
#[test]
fn legacy_unused_function_configurations_agree_like_frozen_with_the_host_gxx() {
    compare(false);
}

fn compare(captured_version: bool) {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/unused-configurations-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    // A missing compiler fails the comparison; it is never a skipped pass.
    let gxx = Command::new("/usr/bin/g++")
        .arg("--version")
        .output()
        .expect("/usr/bin/g++");
    if captured_version {
        assert_eq!(
            String::from_utf8(gxx.stdout).unwrap().lines().next(),
            oracle["gxx"].as_str()
        );
    }
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/unused-configurations")
        .canonicalize()
        .unwrap();
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 2);
    let mut mismatches = vec![];
    for (name, expected) in cases {
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&fixtures.join(name), &root);
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let invocation = registry::parse([
            "ici",
            "verify",
            "--profile",
            "deep",
            "--no-cache",
            "--report",
        ])
        .unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let text = fs::read_to_string(root.join("verify_report.json"))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let report: Value = serde_json::from_str(&text).unwrap();
        let engine = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "dead")
            .unwrap();
        let mut findings: Vec<Value> = engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                json!({"rule_id": finding["rule_id"], "severity": finding["severity"],
                    "message": finding["message"], "path": finding["primary_location"]["path"]})
            })
            .collect();
        findings.sort_by_key(|item| (item["path"].to_string(), item["message"].to_string()));
        let actual = json!({
            "exit_code": output.exit_code,
            "suite_status": report["suite_status"],
            "status": engine["status"],
            "evidence": engine["evidence"],
            "summary": engine["summary"],
            "targets": engine["targets"].as_array().unwrap().iter()
                .map(|target| pick(target, &["target_name", "status", "message", "file_path", "start_line"]))
                .collect::<Vec<_>>(),
            "findings": findings,
            "tool_evidence": engine["tool_evidence"].as_array().unwrap().iter()
                .map(|item| pick(item, &["name", "returncode"]))
                .collect::<Vec<_>>(),
        });
        if &actual != expected {
            eprintln!("== {name}\nnative: {actual:#}\nfrozen: {expected:#}");
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
