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

/// Distinct repeat failure modes: a later repeat times out after a bounded one (the bounded
/// repeat still counts), a repeat killed by a signal keeps its negative return code, and a
/// quiet repeat that fails (exit 1) still has no per-test collection evidence.
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn later_timeout_signal_and_failing_quiet_repeats_are_the_frozen_repeat_warnings() {
    compare("timeout_third");
    compare("signal");
    compare("quiet_failing_repeat");
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
/// when the base run prints no per-test verdict (test_output.py:203-234), measures its coverage,
/// still runs the repeats and warns '[test-quality] Base run outcomes' (test_quality.py:375-392).
/// The legacy view reads it like frozen: every saved target, status, exit code and the quality
/// counters equal frozen's, except the approved empty-denominator gates (validation.json
/// finding_site:src/ici/engines/coverage_policy.py:247:12, "n/a for an empty denominator"):
/// `src/calc.py` has no function and no branch, so frozen's vacuous 'Aggregate function coverage
/// 100.0%' PASS and 'TEM score 0.00' WARN are native's 'measured scope has no applicable
/// denominator' SKIP targets (with the branch gate's), and without a TEM the summary is the
/// measured count instead of frozen's TEM line.
#[test]
#[ignore = "explicit project CPython with pytest and coverage (ICI_TEST_PYTHON)"]
fn base_run_without_per_test_verdicts_is_the_frozen_summary_count_reading() {
    compare("quiet_base");
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["quiet_base"];
    let (_, engine) = verify(&oracle, frozen);
    let keys = [
        "file_path",
        "start_line",
        "target_name",
        "status",
        "message",
        "metrics",
    ];
    let project = |targets: &Value| -> Vec<Value> {
        targets
            .as_array()
            .unwrap()
            .iter()
            .map(|target| {
                Value::Object(
                    keys.iter()
                        .map(|key| ((*key).to_owned(), target[*key].clone()))
                        .collect(),
                )
            })
            .collect()
    };
    const NO_DENOMINATOR: &str =
        "measured scope has no applicable denominator; no percentage was fabricated";
    let gates = ["Coverage:Functions", "Threshold: TEM score"];
    let measured = |targets: Vec<Value>| -> Vec<Value> {
        targets
            .into_iter()
            .filter(|target| target["message"] != NO_DENOMINATOR)
            .filter(|target| !gates.contains(&target["target_name"].as_str().unwrap()))
            .collect()
    };
    assert_eq!(
        measured(project(&engine["targets"])),
        measured(project(&frozen["targets"])),
        "{engine:#}"
    );
    let native_gates: Vec<_> = project(&engine["targets"])
        .into_iter()
        .filter(|target| target["message"] == NO_DENOMINATOR)
        .map(|target| (target["target_name"].clone(), target["status"].clone()))
        .collect();
    assert_eq!(
        native_gates,
        [
            (json!("Coverage:Functions"), json!("SKIP")),
            (json!("Threshold: TEM score"), json!("SKIP")),
            (json!("Threshold: Branch coverage"), json!("SKIP")),
        ]
    );
    let frozen_gates: Vec<_> = project(&frozen["targets"])
        .into_iter()
        .filter(|target| gates.contains(&target["target_name"].as_str().unwrap()))
        .map(|target| (target["target_name"].clone(), target["status"].clone()))
        .collect();
    assert_eq!(
        frozen_gates,
        [
            (json!("Coverage:Functions"), json!("PASS")),
            (json!("Threshold: TEM score"), json!("WARN")),
        ]
    );
    assert_eq!(engine["status"], frozen["status"]);
    assert_eq!(engine["evidence"], "MEASURED");
    assert_eq!(engine["summary"], "1/1 pytest cases passed");
    assert_eq!(
        frozen["summary"],
        "1/1 Tests Passed | Line: 0.0%, Func: 100.0%, Branch: 100.0% -> TEM: 0.00 / 5.0"
    );
}
