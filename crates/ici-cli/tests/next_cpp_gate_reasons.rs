//! Frozen `ici next verify` and `ici analyze` gates of the C++ non-compilation checks, replayed
//! from `tests/rust-parity/cpp/next-cpp-gate-reasons-oracle.json`
//! (`capture_next_cpp_gate_reasons.py`, frozen v0.13.0, two captures byte-identical): schema 1,
//! one C++ component, `[builds.native]` with `prepare = "explicit"` (nothing is built), every C++
//! check off but the one under test. `cpp.artifact` (frozen `gates.gate_artifact` and
//! `artifacts.verify_artifacts`): no linked build and no artifact contract are BLOCKED with
//! frozen's reason, an absent build directory, a glob without a match, a directory-only match
//! and one missing glob beside a satisfied one are `artifact.missing` findings; optional by
//! default (gate PASS, exit 0 whatever it found) and, under `required = true`, the INCOMPLETE
//! reason (exit 3) or the violation (FAIL, exit 1). `cpp.test` with `cpp.coverage` and no declared
//! suite: the INCOMPLETE reasons, the coverage one naming its prerequisite. `analyze` omits test,
//! coverage and artifact checks: with nothing else selected it refuses the run (exit 2), beside
//! `cpp.line` its gate is that check's. Every run compares the exit code, the gate (selected
//! verdict and every reason, literally), each check's state and evidence completeness, and each
//! finding's rule, message, location and severity.
use serde_json::{Value, json};
use std::{fs, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/cpp/next-cpp-gate-reasons-oracle.json");

fn native_run(scenario: &Value, argv: &[&str]) -> Value {
    let work = tempfile::tempdir().unwrap();
    let base = work.path().canonicalize().unwrap();
    fs::create_dir(base.join("home")).unwrap();
    let root = base.join("p");
    fs::create_dir_all(root.join(".hg")).unwrap();
    for (file, text) in scenario["files"].as_object().unwrap() {
        let path = root.join(file);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
    fs::write(root.join("ici.toml"), scenario["config"].as_str().unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(argv)
        .current_dir(&root)
        .env_clear()
        .env("PATH", "/usr/bin:/bin")
        .env("HOME", base.join("home"))
        .env("LC_CTYPE", "C.UTF-8")
        .output()
        .unwrap();
    let mut view = json!({"exit_code": output.status.code()});
    if let Ok(bytes) = fs::read(root.join(".ici/rust-v1/result.json")) {
        let result: Value = serde_json::from_slice(&bytes).unwrap();
        let mut checks = serde_json::Map::new();
        for check in result["checks"].as_array().unwrap() {
            checks.insert(
                check["check_id"].as_str().unwrap().to_owned(),
                json!([check["state"], check["evidence_complete"]]),
            );
        }
        let mut findings: Vec<Value> = result["findings"]
            .as_array()
            .unwrap()
            .iter()
            .map(|finding| {
                json!([
                    finding["rule_id"],
                    finding["message"],
                    finding["primary_location"]["path"],
                    finding["severity"]
                ])
            })
            .collect();
        findings.sort_by_key(Value::to_string);
        view["gate"] = json!({"selected": result["gate"]["selected"],
            "reasons": result["gate"]["reasons"]});
        view["checks"] = Value::Object(checks);
        view["findings"] = json!(findings);
    }
    serde_json::from_str(
        &view
            .to_string()
            .replace(&root.to_string_lossy().into_owned(), "<ROOT>"),
    )
    .unwrap()
}

/// Frozen literals per scenario, so a recapture that drifts fails here too:
/// (scenario, next verify exit, next verify gate, analyze exit).
const EXPECTED: [(&str, i64, &str, i64); 12] = [
    ("artifact-no-build", 0, "PASS", 2),
    ("artifact-no-contract", 0, "PASS", 2),
    ("artifact-build-dir-absent", 0, "PASS", 2),
    ("artifact-no-match", 0, "PASS", 2),
    ("artifact-directory-only", 0, "PASS", 2),
    ("artifact-satisfied", 0, "PASS", 2),
    ("artifact-partly-missing", 0, "PASS", 2),
    ("artifact-required-no-build", 3, "INCOMPLETE", 0),
    ("artifact-required-no-contract", 3, "INCOMPLETE", 0),
    ("artifact-required-no-match", 1, "FAIL", 0),
    ("artifact-required-satisfied", 0, "PASS", 0),
    ("test-coverage-no-suite", 3, "INCOMPLETE", 2),
];

#[test]
fn next_cpp_artifact_test_and_coverage_gate_reasons_equal_frozen() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    assert_eq!(oracle["source"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    let runs = oracle["runs"].as_object().unwrap();
    assert_eq!(runs.len(), EXPECTED.len());
    let mut mismatches = vec![];
    for (name, verify_exit, gate, analyze_exit) in EXPECTED {
        let scenario = &runs[name];
        assert_eq!(scenario["next verify"]["exit_code"], verify_exit, "{name}");
        assert_eq!(scenario["next verify"]["gate"]["selected"], gate, "{name}");
        assert_eq!(scenario["analyze"]["exit_code"], analyze_exit, "{name}");
        for (command, argv) in [
            ("next verify", &["next", "verify", "--no-cache"][..]),
            ("analyze", &["analyze", "--no-cache"][..]),
        ] {
            let frozen = &scenario[command];
            let native = native_run(scenario, argv);
            if frozen.get("console").is_some() {
                // Frozen refused before writing a result (nothing selected): the exit and the
                // absence of a saved result are the comparison.
                if native["exit_code"] != frozen["exit_code"] || native.get("gate").is_some() {
                    mismatches.push(format!("{name} {command}: {native} vs {frozen}"));
                }
                continue;
            }
            for key in ["exit_code", "gate", "checks", "findings"] {
                if native[key] != frozen[key] {
                    mismatches.push(format!(
                        "{name} {command} {key}: native {} frozen {}",
                        native[key], frozen[key]
                    ));
                }
            }
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}
