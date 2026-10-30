//! Legacy python-compat runtime/import parity on a src-layout project (actual project Python).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("src/pkg")).unwrap();
    fs::write(
        root.path().join("src/pkg/__init__.py"),
        "import json\n\ndef f():\n    return json.dumps({})\n",
    )
    .unwrap();
    fs::write(
        root.path().join("pyproject.toml"),
        "[project]\nname = \"demo-pkg\"\nversion = \"1.0\"\nrequires-python = \">=3.11\"\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "[ici]\nprofile = \"standard\"\n[project]\nsource_dirs = [\"src\"]\n[engines.python_compat]\nenabled = true\ninterpreters = [{0}, \"/nonexistent/python3.99\"]\nrequired_interpreters = [{0}]\nimports = [\"pkg\", \"pkg.nothere\"]\n",
            serde_json::to_string(&python()).unwrap()
        ),
    )
    .unwrap();
    root
}
fn report(root: &Path) -> Value {
    commands::dispatch(
        &registry::parse(["ici", "python-compat", "--report"]).unwrap(),
        root,
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    serde_json::from_slice(&fs::read(root.join("python_compat_report.json")).unwrap()).unwrap()
}

#[test]
#[ignore = "explicit project Python src-layout import-only and runtime matrix acceptance"]
fn src_layout_imports_resolve_through_declared_source_dirs_and_unavailable_is_reported_once() {
    let root = fixture();
    let engine = report(root.path());
    let messages: Vec<_> = engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| finding["tool_rule_id"] == "python.compat.import")
        .map(|finding| finding["message"].as_str().unwrap().to_owned())
        .collect();
    // Frozen prepends [root, *source_dirs]; only the genuinely missing module fails.
    assert_eq!(messages.len(), 1, "{messages:?}");
    assert!(
        messages[0].starts_with("import pkg.nothere:"),
        "{messages:?}"
    );
    let unavailable = engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .unwrap()
                .contains("/nonexistent/python3.99")
        })
        .count();
    assert_eq!(unavailable, 1);
    assert!(
        engine["findings"]
            .as_array()
            .unwrap()
            .iter()
            .any(|finding| finding["tool_rule_id"] == "python.compat.runtime-version")
    );
    assert!(!root.path().join("src/pkg/__pycache__").exists());
}

#[test]
#[ignore = "explicit project Python runtime-version and compileall acceptance"]
fn runtime_version_and_compile_failure_keep_frozen_rule_ids_messages_and_locations() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/compat-next-oracle.json"
    ))
    .unwrap();
    let root = tempfile::tempdir().unwrap();
    for (path, text) in oracle["files"].as_object().unwrap() {
        fs::write(root.path().join(path), text.as_str().unwrap()).unwrap();
    }
    fs::write(
        root.path().join("ici.toml"),
        oracle["config"]
            .as_str()
            .unwrap()
            .replace("\"<PYTHON>\"", &serde_json::to_string(&python()).unwrap()),
    )
    .unwrap();
    commands::dispatch(
        &registry::parse(["ici", "next", "verify", "--python", "--no-cache"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    let native: Vec<_> = result["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| {
            item["rule_id"]
                .as_str()
                .is_some_and(|rule| rule.starts_with("python.compat."))
        })
        .map(|item| {
            (
                item["rule_id"].clone(),
                item["message"].clone(),
                item["severity"].clone(),
                item["category"].clone(),
                item["primary_location"]["path"].clone(),
                item["primary_location"]["start_line"].clone(),
            )
        })
        .collect();
    let frozen: Vec<_> = oracle["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            (
                item["rule_id"].clone(),
                item["message"].clone(),
                item["severity"].clone(),
                item["category"].clone(),
                item["location"]["path"].clone(),
                item["location"]["start_line"].clone(),
            )
        })
        .collect();
    let mut native_sorted = native.clone();
    native_sorted.sort_by_key(|item| item.0.to_string());
    let mut frozen_sorted = frozen.clone();
    frozen_sorted.sort_by_key(|item| item.0.to_string());
    assert_eq!(native_sorted, frozen_sorted);
}
