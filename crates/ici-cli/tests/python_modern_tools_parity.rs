//! Replays the frozen modern python.lint/python.format/python.type projects in
//! `tests/rust-parity/python/modern-tools-findings.json` through the native CLI with the same
//! Ruff, mypy and ty executables linked into one PATH directory, and compares every check
//! state, finding, metric, limitation, gate, exit code and attributed fingerprint with the
//! recorded original observation. Reviewed differences are explicit per-case `corrections`.
use ici_cli::{commands, execute, registry};
use ici_execution::Cancellation;
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

const FIXTURE: &str = include_str!("../../../tests/rust-parity/python/modern-tools-findings.json");
const ALL_CHECKS: [&str; 16] = [
    "line",
    "lint",
    "format",
    "type",
    "test",
    "coverage",
    "complexity",
    "cognitive",
    "cycle",
    "dup",
    "security",
    "resource",
    "exception",
    "dead",
    "compat",
    "compat-runtime",
];

fn materialize(root: &Path, case: &Value) {
    let checks: Vec<&str> = case
        .get("checks")
        .and_then(Value::as_array)
        .map(|checks| checks.iter().map(|check| check.as_str().unwrap()).collect())
        .unwrap_or_else(|| vec!["lint", "format", "type"]);
    let mut config = String::from(
        "schema_version=1\n[workspace]\nname=\"oracle\"\nprofile=\"fast\"\n[[components]]\nid=\"project\"\nroot=\".\"\nlanguages=[\"python\"]\n",
    );
    if let Some(provider) = case.get("type_provider").and_then(Value::as_str) {
        config.push_str(&format!(
            "[components.python]\ntype_provider=\"{provider}\"\n"
        ));
    }
    for check in ALL_CHECKS {
        if !checks.contains(&check) {
            config.push_str(&format!("[checks.\"python.{check}\"]\nenabled=false\n"));
        }
    }
    fs::write(root.join("ici.toml"), config).unwrap();
    for (relative, text) in case["files"].as_object().unwrap() {
        let path = root.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text.as_str().unwrap()).unwrap();
    }
}

fn strip(finding: &Value) -> Value {
    let mut finding = finding.clone();
    let object = finding.as_object_mut().unwrap();
    for key in ["task_id", "fingerprint", "analysis_unit_id"] {
        object.remove(key);
    }
    finding
}

fn normalize(document: &Value) -> BTreeMap<&'static str, Value> {
    let checks: Vec<Value> = document["checks"]
        .as_array()
        .unwrap()
        .iter()
        .map(|check| {
            json!({"check_id":check["check_id"],"state":check["state"],"evidence_complete":check["evidence_complete"]})
        })
        .collect();
    let findings: Vec<Value> = document["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(strip)
        .collect();
    BTreeMap::from([
        ("checks", json!(checks)),
        ("findings", json!(findings)),
        ("metrics", document["metrics"].clone()),
        ("limitations", document["limitations"].clone()),
        ("gate", document["gate"].clone()),
    ])
}

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("required native fixture tool {name} is missing"))
}

fn tools(fixture: &Value) -> tempfile::TempDir {
    let directory = tempfile::tempdir().unwrap();
    for (name, variable) in [
        ("ruff", "ICI_TEST_RUFF"),
        ("mypy", "ICI_TEST_MYPY"),
        ("ty", "ICI_TEST_TY"),
    ] {
        let target = required(variable);
        let version = std::process::Command::new(&target)
            .arg("--version")
            .output()
            .unwrap();
        assert_eq!(
            String::from_utf8_lossy(&version.stdout).trim(),
            fixture["tool_versions"][name],
            "{variable} is not the captured {name}"
        );
        std::os::unix::fs::symlink(target, directory.path().join(name)).unwrap();
    }
    directory
}

/// Replays the named cases; returns how many native fingerprints equalled a frozen one.
fn replay(ids: &[&str]) -> usize {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    assert_eq!(
        fixture["source"],
        "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188"
    );
    let linked = tools(&fixture);
    let empty = tempfile::tempdir().unwrap();
    let mut compared = 0;
    for id in ids {
        let case = fixture["cases"]
            .as_array()
            .unwrap()
            .iter()
            .find(|case| case["id"] == *id)
            .unwrap_or_else(|| panic!("{id}: no frozen case"));
        let corrections = &case["corrections"];
        for key in corrections["expected"].as_object().unwrap().keys() {
            assert!(
                corrections["reasons"][key]
                    .as_str()
                    .is_some_and(|reason| !reason.is_empty()),
                "{id}: correction {key} needs a reason"
            );
        }
        let expect = |key: &str| -> Value {
            corrections["expected"]
                .get(key)
                .cloned()
                .unwrap_or_else(|| case["oracle"][key].clone())
        };
        let mut oracle = normalize(&case["oracle"]);
        for (key, value) in corrections["expected"].as_object().unwrap() {
            if key != "exit_code" {
                let key = *oracle.keys().find(|candidate| *candidate == key).unwrap();
                let value = if key == "findings" {
                    json!(
                        value
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(strip)
                            .collect::<Vec<_>>()
                    )
                } else {
                    value.clone()
                };
                oracle.insert(key, value);
            }
        }
        let bin = if case.get("tools") == Some(&json!(false)) {
            empty.path()
        } else {
            linked.path()
        };
        let directory = tempfile::tempdir().unwrap();
        materialize(directory.path(), case);
        let environment = BTreeMap::from([
            (
                "PATH".to_string(),
                format!("{}:/usr/bin:/bin", bin.display()),
            ),
            ("HOME".to_string(), directory.path().display().to_string()),
            // Git discovery stops above the fixture: a repository enclosing the host's
            // temporary directory is not the fixture's (the oracle was captured without one).
            (
                "GIT_CEILING_DIRECTORIES".to_string(),
                directory.path().parent().unwrap().display().to_string(),
            ),
        ]);
        let output = commands::dispatch(
            &registry::parse(["ici", "analyze", "--json"]).unwrap(),
            directory.path(),
            &environment,
        )
        .unwrap();
        let document: Value = serde_json::from_str(&output.stdout).unwrap();
        let native = normalize(&document);
        for key in ["checks", "findings", "metrics", "limitations", "gate"] {
            if native[key] != oracle[key] {
                panic!(
                    "{id}: {key}\nnative {}\nfrozen {}",
                    serde_json::to_string_pretty(&native[key]).unwrap(),
                    serde_json::to_string_pretty(&oracle[key]).unwrap()
                );
            }
        }
        assert_eq!(
            json!(output.exit_code),
            expect("exit_code"),
            "{id}: exit code"
        );

        // Finding identity: recompute each attributed fingerprint from the provider's raw
        // fingerprint without the native analysis-unit attribution and require the frozen value.
        let directory = tempfile::tempdir().unwrap();
        materialize(directory.path(), case);
        let run = execute::execute(
            &registry::parse(["ici", "analyze"]).unwrap(),
            directory.path(),
            &environment,
            true,
            &Cancellation::default(),
        )
        .unwrap();
        let original = corrections["expected"]
            .get("findings")
            .unwrap_or(&case["oracle"]["findings"])
            .as_array()
            .unwrap();
        for finding in &run.result.findings {
            let raw = run.outputs[finding.task_id.as_ref().unwrap()]
                .observation
                .findings
                .iter()
                .find(|raw| {
                    raw.rule_id == finding.rule_id
                        && raw.message == finding.message
                        && raw.primary_location == finding.primary_location
                })
                .unwrap_or_else(|| panic!("{id}: raw finding for {}", finding.rule_id));
            let shape = strip(&serde_json::to_value(finding).unwrap());
            let matched = original
                .iter()
                .find(|item| strip(item) == shape)
                .unwrap_or_else(|| panic!("{id}: no expected finding for {}", finding.rule_id));
            // Attribute the provider's raw fingerprint exactly as frozen did (an analysis unit
            // only where the frozen finding named one).
            let mut unattributed = finding.clone();
            unattributed.analysis_unit_id = matched["analysis_unit_id"].as_str().map(str::to_owned);
            unattributed.fingerprint = raw.fingerprint.clone();
            let fingerprint = ici_domain::attributed_fingerprint(&unattributed, &[]).unwrap();
            // A corrected finding without a frozen counterpart (another provider) has none.
            if let Some(expected) = matched.get("fingerprint") {
                assert_eq!(
                    &json!(fingerprint),
                    expected,
                    "{id}: fingerprint of {}",
                    finding.rule_id
                );
                compared += 1;
            }
        }
        assert_eq!(compared_findings(case), run.result.findings.len(), "{id}");
    }
    compared
}

fn compared_findings(case: &Value) -> usize {
    case["corrections"]["expected"]
        .get("findings")
        .unwrap_or(&case["oracle"]["findings"])
        .as_array()
        .unwrap()
        .len()
}

/// Ruff check (import order, unused imports, Unicode columns), Ruff format, mypy errors and
/// notes and the opt-in ty provider report frozen's findings and fingerprints.
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn modern_lint_format_and_type_findings_match_frozen_with_the_same_tools() {
    let compared = replay(&[
        "violations",
        "format-only",
        "lint-only-unicode",
        "two-files",
        "mypy-note",
        "ty-provider",
        "default-provider",
    ]);
    assert!(compared >= 15, "only {compared} fingerprints compared");
}

/// A clean project is three succeeded checks, no finding and a passing gate (mypy and ty).
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn modern_lint_format_and_type_clean_projects_match_frozen() {
    replay(&["clean", "ty-provider-clean"]);
}

/// A syntax error and absent tools keep frozen's failed/blocked states and incomplete gate.
#[test]
#[ignore = "explicit pinned native tool gate; no tool installation or silent skip"]
fn modern_lint_format_and_type_failures_match_frozen() {
    replay(&["syntax-error", "tools-missing"]);
}

#[test]
fn every_captured_modern_tool_case_is_replayed() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    assert_eq!(fixture["cases"].as_array().unwrap().len(), 11);
}
