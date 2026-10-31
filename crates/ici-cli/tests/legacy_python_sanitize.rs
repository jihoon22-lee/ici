//! Frozen legacy `ici sanitize` on Python projects: ResourceWarning targets, identities, summary.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-python-sanitize-oracle.json");

fn run(oracle: &Value, scenario: &str) -> (i32, Value) {
    let root = tempfile::tempdir().unwrap();
    for (path, text) in oracle["scenarios"][scenario].as_object().unwrap() {
        let path = root.path().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter");
    fs::write(
        root.path().join("ici.toml"),
        oracle["config"]
            .as_str()
            .unwrap()
            .replace("<PYTHON>", &python),
    )
    .unwrap();
    let result = commands::dispatch(
        &registry::parse(["ici", "sanitize", "--report"]).unwrap(),
        root.path(),
        &BTreeMap::from([
            ("PATH".into(), "/usr/bin:/bin".into()),
            ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
        ]),
    )
    .unwrap();
    let report =
        serde_json::from_slice(&fs::read(root.path().join("sanitize_report.json")).unwrap())
            .unwrap();
    (result.exit_code, report)
}

#[test]
#[ignore = "explicit project pytest legacy Python sanitize acceptance"]
fn python_resource_warning_scopes_keep_frozen_targets_identities_summary_and_exit() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for (scenario, frozen) in oracle["runs"].as_object().unwrap() {
        let (exit, native) = run(&oracle, scenario);
        assert_eq!(
            i64::from(exit),
            frozen["exit_code"].as_i64().unwrap(),
            "{scenario}"
        );
        for key in ["status", "summary", "evidence"] {
            assert_eq!(native[key], frozen["engine"][key], "{scenario} {key}");
        }
        let targets: Vec<_> = native["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                json!({"file_path":target["file_path"],"start_line":target["start_line"],
                    "target_name":target["target_name"],"status":target["status"],
                    "message":target["message"]})
            })
            .collect();
        assert_eq!(Value::Array(targets), frozen["targets"], "{scenario}");
        let mut findings: Vec<_> = native["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                json!({"rule_id":finding["rule_id"],"path":finding["primary_location"]["path"],
                    "line":finding["primary_location"]["start_line"],"message":finding["message"],
                    "fingerprint":finding["fingerprint"]})
            })
            .collect();
        findings.sort_by_key(|finding| finding["fingerprint"].as_str().unwrap().to_owned());
        assert_eq!(Value::Array(findings), frozen["findings"], "{scenario}");
    }
}
