//! Frozen legacy `ici sanitize --report` `_missing_python_scope` (`analysis/_sanitize_python_scope.py`),
//! replayed from `tests/rust-parity/validation/legacy-python-sanitize-scopes-oracle.json`
//! (`capture_legacy_python_sanitize_scopes.py`). A missing `tests` directory, a `tests`
//! directory without `test_*.py`/`*_test.py` files and an interpreter without pytest are one
//! `PythonResourceWarnings` target at `tests`: ERROR when `engines.sanitize.required` is true
//! (frozen default), the skipped-scope SKIP/ESTIMATED verdict when it is false. Collection
//! errors and failures without ResourceWarning evidence are the frozen scope errors.
use serde_json::{Value, json};
use std::{fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-python-sanitize-scopes-oracle.json");

fn interpreter(key: &str) -> String {
    match key {
        "python" => std::env::var("ICI_TEST_PYTHON")
            .expect("explicit ICI_TEST_PYTHON (venv CPython with pytest)"),
        _ => std::env::var("ICI_TEST_PYTHON_BARE")
            .expect("explicit ICI_TEST_PYTHON_BARE (CPython 3.12 without pytest)"),
    }
}

fn replay(scenario: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"][scenario];
    let work = tempfile::tempdir().unwrap();
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(&root).unwrap();
    let root = root.canonicalize().unwrap();
    let key = frozen["interpreter"].as_str().unwrap();
    let python = interpreter(key);
    fs::write(
        root.join("ici.toml"),
        frozen["config"]
            .as_str()
            .unwrap()
            .replace(&format!("<{}>", key.to_uppercase()), &python),
    )
    .unwrap();
    for (name, text) in frozen["files"].as_object().unwrap() {
        fs::create_dir_all(root.join(name).parent().unwrap()).unwrap();
        fs::write(root.join(name), text.as_str().unwrap()).unwrap();
    }
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["sanitize", "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    let log = String::from_utf8_lossy(&output.stdout).into_owned()
        + &String::from_utf8_lossy(&output.stderr);
    let text = fs::read_to_string(root.join("sanitize_report.json"))
        .unwrap_or_else(|_| panic!("{scenario}: no report: {log}"))
        .replace(&root.display().to_string(), "<ROOT>")
        .replace(&python, &format!("<{}>", key.to_uppercase()));
    let report: Value = serde_json::from_str(&text).unwrap();
    // Accepted difference (root decision): a required missing scope is native exit 3
    // (incomplete) where frozen exits 1; the not-required SKIP keeps frozen exit 2 and a tool
    // failure keeps frozen exit 1.
    let missing_required = frozen["engine"]["required"] == true
        && frozen["targets"][0]["status"] == "ERROR"
        && !frozen["targets"][0]["message"]
            .as_str()
            .unwrap()
            .starts_with("Pytest ResourceWarning check failed");
    let expected_exit = if missing_required {
        3
    } else {
        frozen["exit_code"].as_i64().unwrap()
    };
    assert_eq!(
        i64::from(output.status.code().unwrap()),
        expected_exit,
        "{scenario}: {log}"
    );
    for key in ["status", "evidence", "required"] {
        assert_eq!(report[key], frozen["engine"][key], "{scenario}: {key}");
    }
    let targets: Vec<Value> = report["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| {
            json!({
                "file_path": target["file_path"],
                "start_line": target["start_line"],
                "target_name": target["target_name"],
                "status": target["status"],
                "message": target["message"],
            })
        })
        .collect();
    // The tool-failure message ends in a snippet of each side's own pytest output (frozen
    // runs pytest without -v under its own temporary root); everything up to the snippet is
    // compared, as is every other field.
    let message = |value: &Value| -> String {
        let text = value.as_str().unwrap();
        match text.split_once(": ") {
            Some((head, _)) if head.starts_with("Pytest ResourceWarning check failed") => {
                format!("{head}: <snippet>")
            }
            _ => text.to_owned(),
        }
    };
    let normalize = |items: &mut Vec<Value>, key: &str| {
        for item in items {
            item[key] = json!(message(&item[key]));
        }
    };
    let mut targets = targets;
    normalize(&mut targets, "message");
    let mut frozen_targets = frozen["targets"].as_array().unwrap().clone();
    normalize(&mut frozen_targets, "message");
    assert_eq!(targets, frozen_targets, "{scenario}: targets");
    let mut summaries = vec![
        report["summary"].clone(),
        frozen["engine"]["summary"].clone(),
    ];
    for summary in &mut summaries {
        *summary = json!(message(summary));
    }
    assert_eq!(summaries[0], summaries[1], "{scenario}: summary");
    let mut findings: Vec<Value> = report["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            json!({
                "rule_id": finding["rule_id"],
                "path": finding["primary_location"]["path"],
                "message": finding["message"],
                "fingerprint": finding["fingerprint"],
                "severity": finding["severity"],
            })
        })
        .collect();
    findings.sort_by(|a, b| a["fingerprint"].as_str().cmp(&b["fingerprint"].as_str()));
    normalize(&mut findings, "message");
    let mut frozen_findings = frozen["findings"].as_array().unwrap().clone();
    normalize(&mut frozen_findings, "message");
    assert_eq!(findings, frozen_findings, "{scenario}: findings");
}

#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON)"]
fn missing_or_test_less_tests_directory_is_the_frozen_scope_refusal() {
    for scenario in ["no_tests_dir", "no_tests_dir_optional", "no_test_files"] {
        replay(scenario);
    }
}

/// Not missing scopes: a collection error has no executed count (frozen's missing-scope
/// message), and a failing suite without ResourceWarning evidence is the always-ERROR
/// `_tool_failure_message` even when `engines.sanitize.required = false`.
#[test]
#[ignore = "explicit project CPython with pytest (ICI_TEST_PYTHON)"]
fn collection_errors_and_failures_without_resource_warnings_are_frozen_scope_errors() {
    for scenario in ["collection_error", "tool_failure"] {
        replay(scenario);
    }
}

#[test]
#[ignore = "explicit bare CPython 3.12 without pytest (ICI_TEST_PYTHON_BARE)"]
fn interpreter_without_pytest_is_the_frozen_unavailable_scope() {
    for scenario in ["pytest_unavailable", "pytest_unavailable_optional"] {
        replay(scenario);
    }
}
