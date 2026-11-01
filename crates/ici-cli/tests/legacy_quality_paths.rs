//! Frozen legacy deep `ici verify` test-quality repeat and mutation paths
//! (`tests/rust-parity/validation/capture_legacy_quality_paths.py`): `[test-quality]` targets,
//! their legacy findings and the repeat/mutation counters of `extra.test_quality`.
use serde_json::{Value, json};
use std::{fs, path::Path};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-quality-paths-oracle.json");
const COUNTERS: [&str; 9] = [
    "repeat_runs",
    "repeat_reruns",
    "repeat_cases",
    "repeat_unavailable",
    "repeat_timeouts",
    "mutation_probes",
    "mutation_available",
    "mutation_unavailable",
    "mutation_status",
];

fn python() -> String {
    std::env::var("ICI_TEST_PYTHON").expect("explicit ICI_TEST_PYTHON (project CPython)")
}

fn verify(oracle: &Value, run: &Value) -> (i32, Value) {
    let root = tempfile::tempdir().unwrap();
    let root = root.path();
    fs::create_dir_all(root.join("src")).unwrap();
    fs::create_dir_all(root.join("tests")).unwrap();
    fs::write(root.join("src/calc.py"), "X = 1\n").unwrap();
    fs::write(
        root.join("tests/conftest.py"),
        oracle["conftest"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        root.join("tests/mode.txt"),
        format!("{}\n", run["mode"].as_str().unwrap()),
    )
    .unwrap();
    fs::write(
        root.join("tests/test_calc.py"),
        oracle["test"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        root.join("ici.toml"),
        run["config"]
            .as_str()
            .unwrap()
            .replace("\"<PYTHON>\"", &format!("{:?}", python())),
    )
    .unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["verify", "--report", "--no-cache"])
        .current_dir(root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(
        &fs::read(Path::new(root).join("verify_report.json"))
            .unwrap_or_else(|_| panic!("no report: {}", String::from_utf8_lossy(&output.stderr))),
    )
    .unwrap();
    let engine = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "test")
        .cloned()
        .unwrap();
    (output.status.code().unwrap(), engine)
}

fn quality_targets(engine: &Value) -> Vec<Value> {
    engine["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .unwrap()
                .starts_with("[test-quality]")
        })
        .map(|target| {
            json!({
                "file_path": target["file_path"],
                "start_line": target["start_line"],
                "target_name": target["target_name"],
                "status": target["status"],
                "message": target["message"],
                "metrics": target["metrics"],
            })
        })
        .collect()
}

fn quality_findings(engine: &Value, messages: &[Value]) -> Vec<Value> {
    engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|finding| messages.contains(&finding["message"]))
        .map(|finding| {
            json!({
                "rule_id": finding["rule_id"],
                "message": finding["message"],
                "fingerprint": finding["fingerprint"],
            })
        })
        .collect()
}

fn compare(scenario: &str) {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"][scenario];
    let (code, engine) = verify(&oracle, frozen);
    assert_eq!(
        i64::from(code),
        frozen["exit_code"].as_i64().unwrap(),
        "{scenario}"
    );
    let expected: Vec<Value> = frozen["targets"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|target| {
            target["target_name"]
                .as_str()
                .unwrap()
                .starts_with("[test-quality]")
        })
        .cloned()
        .collect();
    assert_eq!(quality_targets(&engine), expected, "{scenario}");
    let messages: Vec<Value> = expected
        .iter()
        .map(|target| target["message"].clone())
        .collect();
    assert_eq!(
        quality_findings(&engine, &messages),
        frozen["quality_findings"].as_array().unwrap().clone(),
        "{scenario}"
    );
    for key in COUNTERS {
        assert_eq!(
            engine["extra"]["test_quality"][key], frozen["test_quality"][key],
            "{scenario} {key}"
        );
    }
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn timed_out_quality_repeat_is_the_frozen_repeat_run_warning() {
    compare("timeout");
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn unbounded_quality_repeat_exit_is_the_frozen_repeat_run_warning() {
    compare("exit");
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn quality_repeat_without_per_test_verdicts_is_the_frozen_collection_warning() {
    compare("quiet_repeat");
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn missing_mutation_tool_is_the_frozen_skip_without_a_probe() {
    compare("mutation_missing");
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn succeeding_mutation_probe_command_is_the_frozen_available_pass() {
    compare("mutation_available");
}

#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn failing_or_absent_mutation_probe_command_is_the_frozen_probe_unavailable_skip() {
    compare("mutation_failing");
    compare("mutation_command_missing");
}

/// Frozen falls back to pytest summary counts ('[Python] Passed (1)' 'Pytest summary: passed')
/// when the base run prints no per-test verdict, then warns '[test-quality] Base run outcomes'.
/// Native never turns summary counts into case evidence: the suite stays incomplete (exit 3)
/// with the measured reason, and it is not misreported as 'No tests collected'.
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn base_run_without_per_test_verdicts_stays_incomplete_instead_of_summary_counts() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["quiet_base"];
    let names: Vec<_> = frozen["targets"]
        .as_array()
        .unwrap()
        .iter()
        .map(|target| target["target_name"].as_str().unwrap())
        .collect();
    assert!(
        names.contains(&"[Python] Passed (1)")
            && names.contains(&"[test-quality] Base run outcomes")
    );
    let (code, engine) = verify(&oracle, frozen);
    assert_eq!(code, 3, "{engine:#}");
    assert!(
        engine["targets"]
            .as_array()
            .unwrap()
            .iter()
            .all(|target| target["message"] != "No tests collected"
                && !target["target_name"]
                    .as_str()
                    .unwrap()
                    .starts_with("[Python] Passed")),
        "{engine:#}"
    );
    let test = engine["extra"]["native_checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["check_id"] == "python.test")
        .unwrap();
    assert_eq!(
        test["summary"],
        "pytest reported test cases without per-test verdicts; suite evidence is incomplete"
    );
}
