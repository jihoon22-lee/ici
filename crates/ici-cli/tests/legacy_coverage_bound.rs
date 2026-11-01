//! Root decision E4: frozen `evaluate_coverage_policy` refuses more than 10 000 function (or
//! file) rows with "coverage inventory exceeds the bounded 10000-row limit", which the stable
//! test engine reports as one `Coverage:Policy configuration` ERROR; 10 000 rows are evaluated
//! (`tests/rust-parity/validation/coverage-bound-oracle.json`, `capture_coverage_bound.py`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/coverage-bound-oracle.json");

fn run(oracle: &Value, count: usize) -> (i32, Value) {
    let python = std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter");
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("src")).unwrap();
    fs::create_dir_all(root.path().join("tests")).unwrap();
    let source: String = (0..count)
        .map(|index| format!("def f{index}():\n    return {index}\n\n\n"))
        .collect();
    fs::write(root.path().join("src/many.py"), source).unwrap();
    fs::write(
        root.path().join("tests/test_many.py"),
        oracle["test"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        oracle["config"]
            .as_str()
            .unwrap()
            .replace("<PYTHON>", &python),
    )
    .unwrap();
    let output = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        root.path(),
        &BTreeMap::from([
            ("PATH".into(), "/usr/bin:/bin".into()),
            ("PYTEST_DISABLE_PLUGIN_AUTOLOAD".into(), "1".into()),
        ]),
    )
    .unwrap();
    let engine =
        serde_json::from_slice(&fs::read(root.path().join("test_report.json")).unwrap()).unwrap();
    (output.exit_code, engine)
}

fn policy_targets(engine: &Value) -> Vec<Value> {
    engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .unwrap()
                .starts_with("Coverage:")
        })
        .map(|target| {
            json!({"file_path":target["file_path"],"target_name":target["target_name"],
                "status":target["status"],"message":target["message"]})
        })
        .collect()
}

#[test]
#[ignore = "explicit project pytest/coverage legacy coverage-policy bound acceptance"]
fn more_than_ten_thousand_function_rows_refuse_the_policy_like_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["10001"];
    let (exit, engine) = run(&oracle, 10_001);
    assert_eq!(i64::from(exit), frozen["exit_code"].as_i64().unwrap());
    for key in ["status", "summary", "evidence"] {
        assert_eq!(engine[key], frozen[key], "{key}: {engine:#}");
    }
    assert_eq!(json!(policy_targets(&engine)), frozen["policy_targets"]);
}

#[test]
#[ignore = "explicit project pytest/coverage legacy coverage-policy bound acceptance"]
fn exactly_ten_thousand_function_rows_are_still_evaluated() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["10000"];
    let (exit, engine) = run(&oracle, 10_000);
    assert_eq!(i64::from(exit), frozen["exit_code"].as_i64().unwrap());
    assert_eq!(engine["status"], frozen["status"]);
    let targets = policy_targets(&engine);
    assert!(
        targets
            .iter()
            .all(|target| target["target_name"] != "Coverage:Policy configuration")
    );
    assert_eq!(
        json!(targets.iter().take(3).collect::<Vec<_>>()),
        frozen["policy_targets"]
    );
    assert_eq!(
        targets.len() as u64,
        frozen["policy_target_count"].as_u64().unwrap()
    );
}
