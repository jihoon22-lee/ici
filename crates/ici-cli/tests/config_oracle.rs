//! Frozen configuration refusals through the actual native `ici` binary.
//! Golden: tests/rust-parity/config/legacy-schema-oracle.json (capture_legacy_schema.py).
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

fn fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/rust-parity/config");
    serde_json::from_str(&fs::read_to_string(path.join(name)).unwrap()).unwrap()
}

fn run_line(text: &str) -> (Option<i32>, String) {
    let project = tempfile::tempdir().unwrap();
    let xdg = tempfile::tempdir().unwrap();
    fs::create_dir(project.path().join(".git")).unwrap();
    fs::write(project.path().join("ici.toml"), text).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .arg("line")
        .current_dir(project.path())
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", xdg.path())
        .env("XDG_CONFIG_HOME", xdg.path())
        .output()
        .unwrap();
    assert!(
        fs::read_dir(xdg.path()).unwrap().next().is_none(),
        "a refused configuration writes no global default"
    );
    (
        output.status.code(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

#[test]
fn every_frozen_legacy_refusal_exits_two_from_a_historical_command_and_names_the_key() {
    let oracle = fixture("legacy-schema-oracle.json");
    let differences = fixture("legacy-schema-differences.json");
    let mut failures = vec![];
    let mut checked = 0;
    for case in oracle["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let native = differences
            .get(id)
            .and_then(|d| d["native"].as_str())
            .unwrap_or(case["outcome"].as_str().unwrap());
        if native != "reject" {
            continue;
        }
        let key = differences
            .get(id)
            .and_then(|d| d.get("native_key"))
            .or(case.get("error_key"))
            .and_then(Value::as_str)
            .unwrap();
        let (code, stderr) = run_line(case["toml"].as_str().unwrap());
        checked += 1;
        let named = key.is_empty() || {
            let base = key.split('[').next().unwrap();
            stderr.contains(base)
        };
        if code != Some(2) || !named || !stderr.contains("ici.toml") {
            failures.push(format!("{id}: exit {code:?} stderr {stderr}"));
        }
    }
    assert!(checked > 450, "{checked}");
    assert!(failures.is_empty(), "{}", failures.join("\n"));
    for sample in oracle["cli"].as_array().unwrap() {
        assert_eq!(sample["exit_code"], 2, "frozen CLI sample {sample}");
        assert!(
            sample["stderr"]
                .as_str()
                .unwrap()
                .starts_with("Configuration error: ")
        );
    }
}

fn run_next_plan(case: &Value) -> (Option<i32>, String) {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("project");
    for (name, text) in case["files"].as_object().unwrap() {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir(root.join(".git")).unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_ici"));
    command.args(["next", "plan", "--json"]);
    if let Some(name) = case["explicit"].as_str() {
        command.arg("--config").arg(root.join(name));
    }
    if let Some(text) = case["local"].as_str() {
        let path = temporary.path().join("local.toml");
        fs::write(&path, text).unwrap();
        command.arg("--local-config").arg(path);
    }
    let output = command
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", temporary.path())
        .env("XDG_CONFIG_HOME", temporary.path())
        .output()
        .unwrap();
    (
        output.status.code(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

#[test]
fn every_frozen_next_v1_case_has_the_frozen_plan_exit_code_through_the_binary() {
    let oracle = fixture("next-schema-oracle.json");
    let differences = fixture("next-schema-differences.json");
    let mut failures = vec![];
    let mut samples = 0;
    for case in oracle["cases"].as_array().unwrap() {
        let id = case["id"].as_str().unwrap();
        let native = differences
            .get(id)
            .and_then(|d| d["native"].as_str())
            .unwrap_or(case["outcome"].as_str().unwrap());
        let (code, stderr) = run_next_plan(case);
        let expected = if native == "reject" { 2 } else { 0 };
        if code != Some(expected) {
            failures.push(format!("{id}: exit {code:?} expected {expected}: {stderr}"));
        }
        if let Some(cli) = case.get("cli") {
            samples += 1;
            if differences.get(id).is_none() {
                assert_eq!(cli["exit_code"], json!(expected), "{id}: frozen CLI sample");
            }
        }
    }
    assert_eq!(samples, 4);
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
