//! Frozen legacy `ici test` on C++ trees without any test file: a located FAIL, exit 1.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-cpp-no-tests-oracle.json");

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
fn run(name: &str, extra: Option<(&str, &str)>) -> (i32, Value) {
    let root = tempfile::tempdir().unwrap();
    copy(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../parity-fixtures/cpp/legacy-no-tests")
            .join(name),
        root.path(),
    );
    if let Some((path, text)) = extra {
        let path = root.path().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }
    let result = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    let report =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    (result.exit_code, report)
}

#[test]
fn test_less_cpp_trees_keep_the_frozen_no_tests_verdict_target_and_identity() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for (name, frozen) in oracle["runs"].as_object().unwrap() {
        let (exit, native) = run(name, None);
        assert_eq!(
            i64::from(exit),
            frozen["exit_code"].as_i64().unwrap(),
            "{name}"
        );
        assert_eq!(native["status"], frozen["status"], "{name}");
        let targets: Vec<_> = native["targets"]
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                serde_json::json!({
                    "file_path": target["file_path"], "start_line": target["start_line"],
                    "target_name": target["target_name"], "status": target["status"],
                    "message": target["message"],
                })
            })
            .collect();
        assert_eq!(Value::Array(targets), frozen["targets"], "{name}");
        let findings: Vec<_> = native["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                serde_json::json!({
                    "rule_id": finding["rule_id"], "path": finding["primary_location"]["path"],
                    "message": finding["message"], "fingerprint": finding["fingerprint"],
                })
            })
            .collect();
        assert_eq!(Value::Array(findings), frozen["findings"], "{name}");
        // Frozen fabricates estimated coverage and TEM 0.00 next to the verdict; native has none.
        assert!(frozen["summary"].as_str().unwrap().contains("(est)"));
        assert_eq!(native["summary"], "0/0 Tests Passed; No tests collected");
        assert_eq!(native["score"], Value::Null);
    }
}

#[test]
fn a_declared_cpp_test_file_is_never_reported_as_no_tests_collected() {
    // tests/test_calc.cpp exists: the frozen engine attempts a suite, so the blocked native
    // checks stay an incomplete result (exit 3) instead of an invented FAIL verdict.
    let (exit, native) = run(
        "src-sources",
        Some(("tests/test_calc.cpp", "int main(){return 0;}\n")),
    );
    assert_eq!(exit, 3, "{native:#}");
    assert!(
        !native["targets"]
            .as_array()
            .unwrap()
            .iter()
            .any(|target| target["message"] == "No tests collected")
    );
}

#[test]
fn a_v2_project_with_blocked_test_checks_stays_incomplete_instead_of_no_tests() {
    // The frozen "look under tests/" rule is the legacy schema's; a v2 project whose test
    // checks are blocked keeps an incomplete result.
    let root = tempfile::tempdir().unwrap();
    fs::create_dir(root.path().join("src")).unwrap();
    fs::write(
        root.path().join("src/calc.cpp"),
        "int add(int a,int b){return a+b;}\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        "schema_version=2\n[[components]]\nid='app'\nroot='.'\nlanguages=['cpp']\nsources=['src/calc.cpp']\n",
    )
    .unwrap();
    let result = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    assert_eq!(result.exit_code, 3, "{} {}", result.stdout, result.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    assert!(
        report["extra"].get("no_tests_collected").is_none(),
        "{report:#}"
    );
}
