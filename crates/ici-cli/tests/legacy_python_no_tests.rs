//! Frozen `TestEngine._run_project_tests` on a Python project without `tests/`: the located
//! "No tests collected" FAIL target, without running pytest or needing an interpreter
//! (frozen suite in `tests/rust-parity/reporting/legacy-summary-oracle.json`, case `issues`).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn materialize(base: &Path, tree: &Value) {
    for (name, text) in tree.as_object().unwrap() {
        let path = base.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::create_dir_all(base.join(".git")).unwrap();
}

fn test_engine(base: &Path, home: &Path) -> (i32, Value) {
    let environment = BTreeMap::from([
        ("HOME".to_owned(), home.display().to_string()),
        ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
    ]);
    let output = commands::dispatch(
        &registry::parse(["ici", "test", "--report"]).unwrap(),
        base,
        &environment,
    )
    .unwrap();
    let report = serde_json::from_slice(&fs::read(base.join("test_report.json")).unwrap()).unwrap();
    (output.exit_code, report)
}

#[test]
fn python_without_a_tests_directory_is_the_frozen_no_tests_fail() {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/reporting/legacy-summary-oracle.json"
    ))
    .unwrap();
    let case = &oracle["cases"]["issues"];
    let frozen = case["suite"]["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "test")
        .unwrap();
    let home = tempfile::tempdir().unwrap();
    let directory = tempfile::tempdir().unwrap();
    let base = directory.path().join("project");
    materialize(&base, &case["tree"]);
    let (exit, native) = test_engine(&base, home.path());
    assert_eq!(native["status"], "FAIL", "{native:#}");
    assert_eq!(exit, 1);
    assert_eq!(native["targets"], frozen["targets"], "{native:#}");
    // Failure mode: with a test file under tests/, frozen runs pytest, so the verdict is not
    // fabricated without an interpreter (incomplete evidence instead).
    fs::create_dir(base.join("tests")).unwrap();
    fs::write(
        base.join("tests/test_a.py"),
        "def test_a():\n    assert True\n",
    )
    .unwrap();
    let (_, with_dir) = test_engine(&base, home.path());
    assert!(
        with_dir["extra"].get("no_tests_collected").is_none(),
        "{with_dir:#}"
    );
}
