//! Closure-sweep frozen replays (`tests/rust-parity/python/capture_legacy_sweep.py`, frozen
//! v0.13.0 CLI on CPython 3.10.21): legacy `ici security --report` and the security entry of
//! `ici verify --report` with `engines.security.scan_tests` (frozen engines/security.py
//! `_test_python_files`), and the legacy security/resource `files_checked` syntax counters.
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs};

const FIELDS: [&str; 6] = [
    "target_name",
    "status",
    "message",
    "start_line",
    "start_column",
    "file_path",
];
const EXTRA: [&str; 4] = [
    "files_checked",
    "calls_checked",
    "secret_literals_checked",
    "excluded_source_counts",
];

fn oracle() -> Value {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/python/legacy-sweep-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    oracle
}

fn pick(items: &Value, keys: &[&str]) -> Vec<Value> {
    items
        .as_array()
        .unwrap()
        .iter()
        .map(|item| {
            Value::Object(
                keys.iter()
                    .map(|key| ((*key).to_owned(), item[*key].clone()))
                    .collect(),
            )
        })
        .collect()
}

fn run(case: &Value) -> Value {
    let root = tempfile::tempdir().unwrap();
    if let Some(config) = case["config"].as_str() {
        fs::write(root.path().join("ici.toml"), config).unwrap();
    }
    for (name, content) in case["files"].as_object().unwrap() {
        let path = root.path().join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        match content {
            Value::String(text) => fs::write(path, text).unwrap(),
            other => std::os::unix::fs::symlink(
                root.path().join(other["symlink"].as_str().unwrap()),
                path,
            )
            .unwrap(),
        }
    }
    let command = case["command"].as_str().unwrap();
    let invocation = registry::parse(["ici", command, "--report"]).unwrap();
    let environment = BTreeMap::from([("PATH".to_string(), "/usr/bin:/bin".to_string())]);
    let output = commands::dispatch(&invocation, root.path(), &environment).unwrap();
    let mut report: Value = serde_json::from_slice(
        &fs::read(root.path().join(format!("{command}_report.json"))).unwrap(),
    )
    .unwrap();
    let mut exit_code = output.exit_code;
    if command == "verify" {
        report = report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|item| item["engine_name"] == "security")
            .unwrap()
            .clone();
        // Accepted exit policy: the incomplete legacy suite (no pytest/ruff/mypy on PATH) exits
        // 3 where frozen exited 1; only the security entry is compared.
        assert_eq!(exit_code, 3);
        exit_code = 1;
    }
    json!({
        "exit_code": exit_code,
        "status": report["status"],
        "summary": report["summary"],
        "evidence": report["evidence"],
        "extra": Value::Object(EXTRA.iter().map(|key| ((*key).to_owned(), report["extra"][*key].clone())).collect()),
        "targets": pick(&report["targets"], &FIELDS),
        "findings": pick(&report["findings"], &["rule_id", "fingerprint", "message", "tool_rule_id"]),
    })
}

fn replay(names: &[&str]) {
    let oracle = oracle();
    for name in names {
        let key = if name.contains('/') {
            (*name).to_owned()
        } else {
            format!("security/{name}")
        };
        let case = &oracle["cases"][key];
        assert!(case.is_object(), "{name}: no frozen case");
        let actual = run(case);
        if actual != case["frozen"] {
            panic!(
                "{name}\nnative {}\nfrozen {}",
                serde_json::to_string_pretty(&actual).unwrap(),
                serde_json::to_string_pretty(&case["frozen"]).unwrap()
            );
        }
    }
}

/// scan_tests = true adds the tests/ Python files: their findings, the scanned-only-tests tree
/// (no Python under source_dirs), a tests/ directory overlapping source_dirs (deduplicated) and
/// the secret-name allowlist applied to the added files.
#[test]
fn scan_tests_adds_frozen_test_python_files_to_the_security_scope() {
    replay(&[
        "scan-tests-on",
        "scan-tests-only-tests",
        "scan-tests-overlap",
        "scan-tests-allowlist",
        "scan-tests-no-tests-dir",
        "scan-tests-dir-symlink-inside",
        "verify/scan-tests-on",
    ]);
}

/// Default and explicit false leave tests/ out; ignored directory parts, symlinked files and
/// directories, non-`.py` suffixes, a `tests` regular file and vendor exclusions match frozen.
#[test]
fn scan_tests_off_and_frozen_discovery_exclusions_leave_test_files_out() {
    replay(&[
        "scan-tests-default-off",
        "scan-tests-explicit-false",
        "scan-tests-ignored-parts",
        "scan-tests-symlinks",
        "scan-tests-tests-is-file",
        "scan-tests-dir-symlink-dangling",
    ]);
}

/// A test file the security AST cannot parse is frozen's ERROR (exit 1: security is not required
/// by default), a tree with no Python anywhere stays the frozen SKIP, and the legacy security and
/// resource counters count every read source, including two the parser rejected.
#[test]
fn scan_tests_syntax_error_and_empty_scope_keep_frozen_failure_verdicts() {
    replay(&[
        "scan-tests-syntax",
        "scan-tests-nothing",
        "security/syntax-files-checked",
        "resource/syntax-files-checked",
    ]);
}

#[test]
fn every_captured_sweep_case_is_replayed() {
    let oracle = oracle();
    assert_eq!(oracle["cases"].as_object().unwrap().len(), 17);
}
