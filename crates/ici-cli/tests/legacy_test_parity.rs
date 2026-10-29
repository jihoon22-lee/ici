//! Frozen legacy `ici test --report` targets, finding identities and summary replayed natively.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

const ORACLE: &str = include_str!("../../../tests/rust-parity/validation/legacy-test-oracle.json");

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn run(oracle: &Value, scenario: &str) -> (i32, Value) {
    let root = tempfile::tempdir().unwrap();
    let files = oracle["scenarios"][scenario].as_object().unwrap();
    for (path, text) in files {
        if path == "extra_config" {
            continue;
        }
        let path = root.path().join(path);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    let config = oracle["config"]
        .as_str()
        .unwrap()
        .replace("<PYTHON>", &python())
        + files
            .get("extra_config")
            .and_then(Value::as_str)
            .unwrap_or("");
    fs::write(root.path().join("ici.toml"), config).unwrap();
    let result = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &BTreeMap::from([
            ("PATH".into(), "/usr/bin:/bin".into()),
            ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
        ]),
    )
    .unwrap();
    let report =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    (result.exit_code, report)
}
fn project(target: &Value, line: bool) -> Value {
    let mut value = serde_json::json!({
        "file_path": target["file_path"], "target_name": target["target_name"],
        "status": target["status"], "message": target["message"],
    });
    if line {
        value["start_line"] = target["start_line"].clone();
        value["end_line"] = target["end_line"].clone();
        value["metrics"] = target["metrics"].clone();
    }
    value
}
fn findings(engine: &Value) -> Vec<Value> {
    let mut items: Vec<_> = engine["findings"]
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
    items.sort_by_key(|item| item["fingerprint"].as_str().unwrap().to_owned());
    items
}
fn replay(scenario: &str) -> (Value, Value) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = oracle["runs"][scenario].clone();
    let (exit, native) = run(&oracle, scenario);
    assert_eq!(i64::from(exit), frozen["exit_code"].as_i64().unwrap());
    for key in ["status", "score", "max_score", "evidence", "required"] {
        assert_eq!(native[key], frozen["engine"][key], "{key}");
    }
    let frozen_targets = frozen["targets"].as_array().unwrap();
    let native_targets = native["targets"].as_array().unwrap();
    // Test cases keep the frozen name/verdict/message; their location is the test definition
    // instead of the frozen line-1 placeholder (identity excludes the line).
    for (frozen_target, native_target) in frozen_targets.iter().zip(native_targets) {
        let exact = !frozen_target["target_name"]
            .as_str()
            .unwrap()
            .starts_with("tests/");
        assert_eq!(
            project(native_target, exact),
            project(frozen_target, exact),
            "{native_target:#}"
        );
    }
    (frozen, native)
}

#[test]
#[ignore = "explicit project pytest/coverage legacy test-engine acceptance"]
fn failing_suite_keeps_frozen_case_coverage_and_function_identities() {
    let (frozen, native) = replay("failing");
    // A failing case already fails the suite: no TEM/branch breach is added, as frozen.
    assert_eq!(
        native["targets"].as_array().unwrap().len(),
        frozen["targets"].as_array().unwrap().len()
    );
    assert_eq!(findings(&native), *frozen["findings"].as_array().unwrap());
    // An empty branch denominator is explicit N/A, never the frozen vacuous 100%.
    assert_eq!(
        native["summary"],
        frozen["engine"]["summary"]
            .as_str()
            .unwrap()
            .replace("Branch: 100.0%", "Branch: n/a")
    );
    for key in [
        "function_coverage",
        "line_coverage",
        "metrics_summary",
        "pass_rate",
        "passed_tests",
        "quality_enabled",
        "skipped_tests",
        "tem_score",
        "test_suites",
        "total_tests",
    ] {
        assert_eq!(native["extra"][key], frozen["extra"][key], "{key}");
    }
}

#[test]
#[ignore = "explicit project pytest/coverage legacy test-engine acceptance"]
fn passing_suite_keeps_frozen_file_changed_line_tem_and_branch_gates() {
    let (frozen, native) = replay("passing");
    assert_eq!(
        native["targets"].as_array().unwrap().len(),
        frozen["targets"].as_array().unwrap().len()
    );
    assert_eq!(findings(&native), *frozen["findings"].as_array().unwrap());
    assert_eq!(native["summary"], frozen["engine"]["summary"]);
    for key in [
        "function_coverage",
        "line_coverage",
        "metrics_summary",
        "pass_rate",
        "passed_tests",
        "quality_enabled",
        "skipped_tests",
        "tem_score",
        "total_tests",
    ] {
        assert_eq!(native["extra"][key], frozen["extra"][key], "{key}");
    }
    // Frozen `_build_test_suites` also counted the `Threshold:` gate targets as two failed
    // "tests" of a `.` suite; native suites list only executed test cases.
    let mut suites = frozen["extra"]["test_suites"].as_array().unwrap().clone();
    let gate = suites.pop().unwrap();
    assert_eq!(gate["file"], ".");
    assert!(
        gate["tests"]
            .as_array()
            .unwrap()
            .iter()
            .all(|test| test["name"].as_str().unwrap().starts_with("Threshold: "))
    );
    assert_eq!(native["extra"]["test_suites"], Value::Array(suites));
}
