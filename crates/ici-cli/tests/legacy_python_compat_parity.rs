//! Frozen legacy `ici python-compat --report` (`engines/python_compat.py`), replayed from
//! `tests/rust-parity/validation/legacy-python-compat-oracle.json` (captured through a private
//! copy of the released pyz by `capture_legacy_python_compat.py`). Every scenario keeps the
//! frozen exit code, engine status/summary/evidence, every target field, every finding field
//! (legacy fingerprints included) and the frozen `extra` keys.
use serde_json::{Value, json};
use std::{fs, path::Path, process::Command};

const ORACLE: &str =
    include_str!("../../../tests/rust-parity/validation/legacy-python-compat-oracle.json");

fn bare_python() -> String {
    std::env::var("ICI_TEST_PYTHON_BARE").expect("explicit ICI_TEST_PYTHON_BARE (CPython 3.12)")
}

struct Replay {
    code: i32,
    output: String,
    report: Option<Value>,
}

fn replay(oracle: &Value, scenario: &str) -> Replay {
    let frozen = &oracle["runs"][scenario];
    let work = tempfile::tempdir().unwrap();
    let silent = work.path().join("silent-python");
    fs::write(&silent, oracle["silent"].as_str().unwrap()).unwrap();
    fs::set_permissions(&silent, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let home = work.path().join("home");
    fs::create_dir_all(&home).unwrap();
    let root = work.path().join("project");
    fs::create_dir_all(&root).unwrap();
    let root = root.canonicalize().unwrap();
    let bare = bare_python();
    let bin = Path::new(&bare).parent().unwrap().display().to_string();
    let configured = format!("{bin}/python3.12");
    fs::write(
        root.join("ici.toml"),
        frozen["config"]
            .as_str()
            .unwrap()
            .replace("<BARE>", &configured)
            .replace("<SILENT>", &silent.display().to_string()),
    )
    .unwrap();
    for (name, text) in frozen["files"].as_object().unwrap() {
        fs::write(root.join(name), text.as_str().unwrap()).unwrap();
    }
    let output = Command::new(env!("CARGO_BIN_EXE_ici"))
        .args(["python-compat", "--report"])
        .current_dir(&root)
        .env_clear()
        .env("HOME", &home)
        .env("PATH", &bin)
        .output()
        .unwrap();
    let report = fs::read_to_string(root.join("python_compat_report.json"))
        .ok()
        .map(|text| {
            let text = text
                .replace(&root.display().to_string(), "<ROOT>")
                .replace(&configured, "<BARE>")
                .replace(&silent.display().to_string(), "<SILENT>");
            serde_json::from_str(&text).unwrap()
        });
    Replay {
        code: output.status.code().unwrap(),
        output: String::from_utf8_lossy(&output.stdout).into_owned()
            + &String::from_utf8_lossy(&output.stderr),
        report,
    }
}

fn compare(oracle: &Value, scenario: &str) {
    let frozen = &oracle["runs"][scenario];
    let native = replay(oracle, scenario);
    assert_eq!(
        i64::from(native.code),
        frozen["exit_code"].as_i64().unwrap(),
        "{scenario}: {}",
        native.output
    );
    let report = native
        .report
        .unwrap_or_else(|| panic!("{scenario}: no report: {}", native.output));
    for key in ["status", "summary", "evidence", "required", "targets"] {
        assert_eq!(report[key], frozen[key], "{scenario}: {key}");
    }
    // Intended difference: native probes the version with the allowlisted runtime metadata
    // script (ADR-0008) instead of `python -VV`, so the adapted findings name that probe.
    let mut expected = frozen["findings"].clone();
    for finding in expected.as_array_mut().unwrap() {
        if finding["tool_name"] == "python -VV" {
            finding["tool_name"] = json!("python runtime metadata");
        }
    }
    assert_eq!(report["findings"], expected, "{scenario}: findings");
    for (key, value) in frozen["extra"].as_object().unwrap() {
        assert_eq!(&report["extra"][key], value, "{scenario}: extra.{key}");
    }
    // Every frozen probe kind ran for the checked interpreters (argv are native, see below).
    let names = |document: &Value| -> Vec<Value> {
        document["tool_evidence"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item["name"].clone())
            .collect()
    };
    let frozen_names: Vec<Value> = names(frozen)
        .into_iter()
        .map(|name| {
            if name == "python -VV" {
                json!("python runtime metadata")
            } else {
                name
            }
        })
        .collect();
    assert_eq!(names(&report), frozen_names, "{scenario}: tool evidence");
}

#[test]
#[ignore = "explicit bare CPython 3.12 (ICI_TEST_PYTHON_BARE)"]
fn verified_runtime_targets_and_counters_match_the_frozen_engine() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in ["verified_launcher", "matrix_optional_unavailable"] {
        compare(&oracle, scenario);
    }
}

#[test]
#[ignore = "explicit bare CPython 3.12 (ICI_TEST_PYTHON_BARE)"]
fn incompatible_and_not_applicable_runtimes_match_the_frozen_engine() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in [
        "requires_python_incompatible",
        "import_smoke_failure",
        "not_applicable",
    ] {
        compare(&oracle, scenario);
    }
}

#[test]
#[ignore = "explicit bare CPython 3.12 (ICI_TEST_PYTHON_BARE)"]
fn unavailable_interpreters_and_unparseable_sources_are_the_frozen_errors() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    for scenario in [
        "required_unavailable",
        "version_unavailable",
        "syntax_unavailable",
        "analysis_unavailable_requires",
    ] {
        compare(&oracle, scenario);
    }
}

/// Refused before any engine runs. Frozen refuses an invalid `target_version` as a configuration
/// error (exit 2, same class natively). An unparseable pyproject.toml is a frozen AnalysisUnavailable
/// ERROR (exit 1); native configuration discovery reads pyproject.toml for mypy/ty settings and
/// refuses the run (exit 2) — recorded as an open cross-area difference.
#[test]
#[ignore = "explicit bare CPython 3.12 (ICI_TEST_PYTHON_BARE)"]
fn configuration_refusals_keep_their_recorded_exit_classes() {
    let oracle: Value = serde_json::from_str(ORACLE).unwrap();
    let frozen = &oracle["runs"]["target_version_config_error"];
    assert_eq!(frozen["exit_code"], 2);
    let native = replay(&oracle, "target_version_config_error");
    assert_eq!(native.code, 2, "{}", native.output);
    assert!(native.report.is_none());
    assert!(
        native
            .output
            .contains("engines.python_compat.target_version"),
        "{}",
        native.output
    );
    let frozen = &oracle["runs"]["analysis_unavailable_toml"];
    assert_eq!(frozen["exit_code"], 1);
    assert_eq!(frozen["status"], "ERROR");
    let native = replay(&oracle, "analysis_unavailable_toml");
    assert_eq!(native.code, 2, "{}", native.output);
    assert!(native.output.contains("invalid TOML"), "{}", native.output);
}
