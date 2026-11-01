//! Frozen `ici verify` exit for a coverage-minimum FAIL suite per schema
//! (`tests/rust-parity/validation/capture_verify_exit_floor.py`). The frozen legacy TestEngine
//! FAIL exits 1 even though the native gate keeps a coverage minimum advisory; frozen v0.13.0
//! rejects the modern schemas that could express the same minimum (exit 2), so a modern verify
//! keeps the native gate's exit.
use serde_json::Value;
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/verify-exit-floor-oracle.json");

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("explicit ICI_TEST_PYTHON (project CPython)")
}

fn project(oracle: &Value, config: &str) -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("tests")).unwrap();
    fs::write(root.path().join("app.py"), oracle["app"].as_str().unwrap()).unwrap();
    fs::write(
        root.path().join("tests/test_app.py"),
        oracle["test"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(root.path().join("ici.toml"), config).unwrap();
    root
}

fn verify(root: &Path) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["verify", "--report", "--no-cache"])
        .current_dir(root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    (
        output.status.code().unwrap(),
        String::from_utf8_lossy(&output.stderr).into_owned(),
    )
}

fn report(root: &Path) -> Value {
    serde_json::from_slice(&fs::read(root.join("verify_report.json")).unwrap()).unwrap()
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn legacy_coverage_minimum_fail_exits_one_like_the_frozen_suite() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["legacy"];
    let root = project(
        &oracle,
        &frozen["config"]
            .as_str()
            .unwrap()
            .replace("\"<PYTHON>\"", &format!("{:?}", python())),
    );
    let (code, stderr) = verify(root.path());
    assert_eq!(
        i64::from(code),
        frozen["exit_code"].as_i64().unwrap(),
        "{stderr}"
    );
    let report = report(root.path());
    assert_eq!(report["suite_status"], frozen["suite_status"]);
    let engines: serde_json::Map<String, Value> = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .map(|engine| {
            (
                engine["engine_name"].as_str().unwrap().into(),
                engine["status"].clone(),
            )
        })
        .collect();
    assert_eq!(Value::Object(engines), frozen["engines"]);
    // The native gate keeps the coverage minimum advisory; only the legacy suite floor exits 1.
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    assert_eq!(result["gate"]["selected"], "PASS", "{:#}", result["gate"]);
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn modern_schema_coverage_minimum_fail_keeps_the_native_gate_exit() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    // Frozen v0.13.0 has no modern schema able to express the minimum: both refuse the config.
    for scenario in ["sparse_v2", "next_v1_engines"] {
        let frozen = &oracle["runs"][scenario];
        assert_eq!(frozen["exit_code"], 2, "{scenario}");
        assert!(frozen["suite_status"].is_null(), "{scenario}");
    }
    let mut config = format!(
        "schema_version = 2\n[python]\nexecutable = {:?}\n[engines.test]\nmin_line_cov = 99.0\n",
        python()
    );
    for check in ici_domain::builtin_catalog().checks {
        let enabled = matches!(check.id.as_str(), "python.test" | "python.coverage");
        config.push_str(&format!("[checks.\"{}\"]\nenabled = {enabled}\n", check.id));
    }
    let root = project(&oracle, &config);
    let (code, stderr) = verify(root.path());
    let report = report(root.path());
    // The historical view still shows the coverage FAIL ...
    assert_eq!(report["suite_status"], "FAIL", "{stderr}");
    let result: Value =
        serde_json::from_slice(&fs::read(root.path().join(".ici/rust-v1/result.json")).unwrap())
            .unwrap();
    // ... but the native gate decides the exit of a modern-schema verify.
    assert_eq!(result["gate"]["selected"], "PASS", "{:#}", result["gate"]);
    assert_eq!(code, 0, "{stderr}");
    // The frozen-refused next [engines] key is refused natively too, before any engine runs.
    let next = project(
        &oracle,
        oracle["runs"]["next_v1_engines"]["config"]
            .as_str()
            .unwrap(),
    );
    let (code, stderr) = verify(next.path());
    assert_eq!(code, 2, "{stderr}");
    assert!(
        stderr.contains("engines: unknown configuration key"),
        "{stderr}"
    );
    assert!(!next.path().join("verify_report.json").exists());
}
