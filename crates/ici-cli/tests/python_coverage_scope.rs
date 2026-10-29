//! Untested declared Python sources are measured 0%, never silently dropped (frozen defect).
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs, path::Path};

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON")
        .expect("set ICI_TEST_PYTHON to the explicit project interpreter")
}
fn fixture(main: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("main.py"), main).unwrap();
    fs::write(
        root.path().join("test_slow.py"),
        "def test_slow():\n    assert True\n",
    )
    .unwrap();
    fs::write(
        root.path().join("ici.toml"),
        format!(
            "schema_version=1\n[workspace]\nname='w'\nprofile='deep'\n[[components]]\nid='project'\nroot='.'\nlanguages=['python']\n[components.python]\nexecutable={}\n",
            serde_json::to_string(&python()).unwrap()
        ),
    )
    .unwrap();
    root
}
fn call(root: &Path, args: &[&str]) -> commands::CommandOutput {
    commands::dispatch(
        &registry::parse(args.iter().copied()).unwrap(),
        root,
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap()
}
fn check(result: &Value, id: &str) -> Value {
    result["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == id)
        .cloned()
        .unwrap()
}
fn metric(result: &Value, name: &str) -> Value {
    result["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .find(|metric| metric["name"] == name)
        .cloned()
        .unwrap_or(Value::Null)
}

#[test]
#[ignore = "explicit project pytest+coverage source-scope acceptance"]
fn never_imported_declared_source_is_measured_zero_instead_of_omitted() {
    let root = fixture("def main():\n    return 1\n");
    call(
        root.path(),
        &["ici", "next", "verify", "--profile", "deep", "--no-cache"],
    );
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    assert_eq!(check(&result, "python.test")["state"], "SUCCEEDED");
    assert_eq!(check(&result, "python.coverage")["state"], "SUCCEEDED");
    let lines = metric(&result, "coverage.lines");
    // Frozen oracle reported 2/2 = 100% by omitting main.py; its two statements count here.
    assert_eq!(lines["numerator"], 2, "{lines}");
    assert_eq!(lines["denominator"], 4, "{lines}");
    let frozen: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/validation/python-coverage-scope-oracle.json"
    ))
    .unwrap();
    let frozen_lines = frozen["metrics"]
        .as_array()
        .unwrap()
        .iter()
        .find(|metric| metric["name"] == "coverage.lines")
        .unwrap();
    // The corrected denominator differs from the frozen one by exactly main.py's statements.
    assert_eq!(frozen_lines["numerator"], lines["numerator"]);
    assert_eq!(
        frozen_lines["denominator"].as_u64().unwrap() + 2,
        lines["denominator"].as_u64().unwrap()
    );
    assert_eq!(metric(&result, "coverage.functions")["numerator"], 1);
    assert_eq!(metric(&result, "coverage.functions")["denominator"], 2);
    assert_eq!(
        fs::read_to_string(root.path().join("main.py")).unwrap(),
        "def main():\n    return 1\n"
    );
}

#[test]
#[ignore = "explicit project pytest+coverage unmeasurable-source acceptance"]
fn unmeasurable_declared_source_keeps_coverage_incomplete_and_the_legacy_line_says_why() {
    // coverage.py cannot parse this declared file, so its denominator is genuinely unknown.
    let root = fixture("def main(:\n    return 1\n");
    let output = call(root.path(), &["ici", "test"]);
    let line = output
        .stdout
        .lines()
        .find(|line| line.contains(" test:"))
        .unwrap_or_default()
        .to_owned();
    assert!(line.starts_with("ERROR test:"), "{}", output.stdout);
    assert!(line.contains("python.coverage:"), "{line}");
    assert!(!line.contains("passed"), "{line}");
}
