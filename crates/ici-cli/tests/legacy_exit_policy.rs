//! Intended exit difference for incomplete legacy runs (SPEC-01 §8, SPEC-04): frozen v0.13.0
//! exits 1 for a suite ERROR caused by missing evidence, native keeps the incomplete exit 3. The
//! legacy view still shows the frozen ERROR verdict, and nothing is reported as a pass.
use ici_cli::{commands, registry};
use serde_json::Value;
use std::{collections::BTreeMap, fs};

const BLOCKED: &str =
    include_str!("../../../tests/rust-parity/validation/integration-blocked-oracle.json");

#[test]
fn incomplete_legacy_verify_exits_three_where_frozen_exited_one_with_the_same_error_verdict() {
    let oracle: Value = serde_json::from_str(BLOCKED).unwrap();
    let frozen = &oracle["runs"]["unknown_python_target"];
    // Frozen: suite ERROR (required integration engine could not run) -> exit 1.
    assert_eq!(frozen["exit_code"], 1);
    assert_eq!(frozen["integration"]["status"], "ERROR");
    let root = tempfile::tempdir().unwrap();
    fs::write(
        root.path().join("ici.toml"),
        frozen["config"]
            .as_str()
            .unwrap()
            .replace("\"<PYTHON>\"", "\"/bin/sh\""),
    )
    .unwrap();
    fs::write(root.path().join("main.py"), "def f():\n    return 1\n").unwrap();
    fs::write(root.path().join("x.py"), "echo x\n").unwrap();
    let output = commands::dispatch(
        &registry::parse([
            "ici",
            "verify",
            "--report",
            "--profile",
            "deep",
            "--no-cache",
        ])
        .unwrap(),
        root.path(),
        &BTreeMap::from([("PATH".into(), "/usr/bin:/bin".into())]),
    )
    .unwrap();
    // Native: still non-zero, but the distinct incomplete exit 3 (never 0, never a pass).
    assert_eq!(output.exit_code, 3, "{} {}", output.stdout, output.stderr);
    let report: Value =
        serde_json::from_slice(&fs::read(root.path().join("verify_report.json")).unwrap()).unwrap();
    assert_eq!(report["suite_status"], "ERROR");
    let integration = report["results"]
        .as_array()
        .unwrap()
        .iter()
        .find(|engine| engine["engine_name"] == "integration")
        .unwrap();
    assert_eq!(integration["status"], frozen["integration"]["status"]);
    let native: Value =
        serde_json::from_slice(&fs::read(root.path().join(ici_cli::DEFAULT_RESULT)).unwrap())
            .unwrap();
    assert_eq!(native["gate"]["selected"], "INCOMPLETE");
    assert!(output.stdout.contains("Suite: ERROR"), "{}", output.stdout);
}
