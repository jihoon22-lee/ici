//! Frozen `_merge_note_target` keeps the unsuffixed note and the repeat count in `metrics`
//! (public in the JSON report). `capture_mypy_note_metrics.py` records whole target objects, a
//! repeated note and a single one, because `legacy-engines.json` keeps only other fields.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

fn run(base: &str, source: &str, path: &str) -> Value {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(root.path().join("src/a.py"), source).unwrap();
    fs::write(root.path().join("ici.toml"), base).unwrap();
    let invocation = registry::parse(["ici", "type", "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_owned(), path.to_owned())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("type_report.json")).unwrap()).unwrap();
    serde_json::json!({
        "exit_code": output.exit_code,
        "status": report["status"],
        "targets": report["targets"],
    })
}

#[test]
#[ignore = "explicit pinned mypy gate: ICI_TEST_MYPY must be the captured version"]
fn a_repeated_mypy_note_has_the_frozen_metrics_and_a_single_one_has_none() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/mypy-note-metrics-oracle.json"
    ))
    .unwrap();
    let mypy = std::env::var("ICI_TEST_MYPY").expect("ICI_TEST_MYPY is required");
    let version = std::process::Command::new(&mypy)
        .arg("--version")
        .output()
        .unwrap();
    assert_eq!(
        String::from_utf8_lossy(&version.stdout).trim(),
        oracle["mypy"],
        "ICI_TEST_MYPY is not the captured mypy"
    );
    let tools = tempfile::tempdir().unwrap();
    std::os::unix::fs::symlink(&mypy, tools.path().join("mypy")).unwrap();
    let path = format!("{}:/usr/bin:/bin", tools.path().display());
    for (name, case) in oracle["cases"].as_object().unwrap() {
        let actual = run(
            oracle["base"].as_str().unwrap(),
            case["source"].as_str().unwrap(),
            &path,
        );
        assert_eq!(actual, case["frozen"], "{name}");
    }
}
