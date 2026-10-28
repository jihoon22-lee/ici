use ici_cli::{commands::dispatch, registry::parse};
use serde_json::Value;
use std::{collections::BTreeMap, fs, os::unix::fs::PermissionsExt};

#[test]
fn native_plan_names_real_argv_without_spawning_installing_or_writing_state() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "x=1\n").unwrap();
    let tool = root.path().join("ruff-stub");
    fs::write(&tool, "#!/bin/sh\ntouch probe-ran\nexit 98\n").unwrap();
    fs::set_permissions(&tool, fs::Permissions::from_mode(0o755)).unwrap();
    fs::write(
        root.path().join("ici.toml"),
        "schema_version=2\n[tools.ruff]\npath='ruff-stub'\n",
    )
    .unwrap();
    let args = parse(["ici", "analyze", "--plan", "--json"]).unwrap();
    let output = dispatch(
        &args,
        root.path(),
        &BTreeMap::from([("PATH".into(), String::new())]),
    )
    .unwrap();
    assert!(output.stderr.is_empty());
    let document: Value = serde_json::from_str(&output.stdout).unwrap();
    assert_eq!(document["schema_id"], "ici.next.plan");
    assert_eq!(
        document["scope"]["languages"],
        serde_json::json!(["python"])
    );
    let checks = document["plans"][0]["checks"].as_array().unwrap();
    assert_eq!(checks.len(), 13);
    let lint = checks.iter().find(|c| c["check"] == "python.lint").unwrap();
    assert_eq!(lint["kind"], "process");
    assert_eq!(lint["argv"][0], tool.to_string_lossy().as_ref());
    assert!(
        lint["argv"]
            .as_array()
            .unwrap()
            .iter()
            .any(|arg| arg == "--no-fix")
    );
    assert!(
        checks.iter().find(|c| c["check"] == "python.type").unwrap()["blocked"]
            .as_str()
            .unwrap()
            .contains("ty")
    );
    assert!(!root.path().join("probe-ran").exists());
    assert!(!root.path().join(".ici").exists());
}
#[test]
fn broken_explicit_tool_is_blocked_and_never_falls_back_to_an_available_tool() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), "x=1\n").unwrap();
    fs::write(
        root.path().join("ici.toml"),
        "schema_version=2\n[tools.ruff]\npath='missing-tool'\n",
    )
    .unwrap();
    let tool = root.path().join("ruff");
    fs::write(&tool, "#!/bin/sh\ntouch probe-ran\nexit 0\n").unwrap();
    fs::set_permissions(&tool, fs::Permissions::from_mode(0o755)).unwrap();
    let output = dispatch(
        &parse(["ici", "analyze", "--plan", "--json"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), root.path().to_string_lossy().into_owned())]),
    )
    .unwrap();
    let document: Value = serde_json::from_str(&output.stdout).unwrap();
    let checks = document["plans"][0]["checks"].as_array().unwrap();
    assert!(
        checks
            .iter()
            .filter(|c| matches!(c["check"].as_str(), Some("python.lint" | "python.format")))
            .all(|c| c["kind"] == "blocked"
                && c["blocked"].as_str().unwrap().contains("missing-tool"))
    );
    assert!(!root.path().join("probe-ran").exists());
}
