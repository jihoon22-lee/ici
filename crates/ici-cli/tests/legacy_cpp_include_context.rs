//! Legacy `ici verify` compiler-traced include graph (frozen `build_compiler_cpp_graph`) against
//! frozen v0.13.0 runs (`tests/rust-parity/cpp/include-context-oracle.json`,
//! `capture_include_context.py`) over parity-fixtures/cpp/include-context: database and unit
//! diagnostics, a production source without a command, no traceable unit, rejected replay
//! commands, recorded compilers that cannot be resolved, a failing trace, a response-file unit
//! and a traced include cycle. Compared: exit
//! code, suite status and the cycle engine's status, evidence, summary, frozen counters,
//! targets, findings and tool evidence (name, return code, error). Standalone `ici cycle` on
//! the same database stays on the heuristic graph like frozen (`cycle:mixed`).
use ici_cli::{commands, registry};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path, process::Command};

fn copy(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        let target = to.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), target).unwrap();
        }
    }
}

fn pick(value: &Value, keys: &[&str]) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

/// Every frozen cycle `extra` key (`py_cycles`, `total_cycles` and the `_analyze_cpp_includes`
/// counters), picked from the native extra; native-only evidence keys are not compared here.
fn engine(exit_code: i32, report: &Value, standalone: bool, frozen_extra: &Value) -> Value {
    let engine = if standalone {
        report
    } else {
        report["results"]
            .as_array()
            .unwrap()
            .iter()
            .find(|engine| engine["engine_name"] == "cycle")
            .unwrap()
    };
    let mut findings: Vec<Value> = engine["findings"]
        .as_array()
        .unwrap()
        .iter()
        .map(|finding| {
            json!({"rule_id": finding["rule_id"], "severity": finding["severity"],
                "message": finding["message"], "path": finding["primary_location"]["path"]})
        })
        .collect();
    findings.sort_by_key(|item| (item["path"].to_string(), item["message"].to_string()));
    json!({
        "exit_code": exit_code,
        "suite_status": if standalone { Value::Null } else { report["suite_status"].clone() },
        "status": engine["status"],
        "evidence": engine["evidence"],
        "summary": engine["summary"],
        "extra": pick(
            &engine["extra"],
            &frozen_extra.as_object().unwrap().keys().map(String::as_str).collect::<Vec<_>>(),
        ),
        "targets": engine["targets"].as_array().unwrap().iter()
            .map(|target| pick(target, &["target_name", "status", "message", "file_path", "start_line"]))
            .collect::<Vec<_>>(),
        "findings": findings,
        "tool_evidence": engine["tool_evidence"].as_array().unwrap().iter()
            .map(|item| pick(item, &["name", "returncode", "error"]))
            .collect::<Vec<_>>(),
    })
}

#[test]
#[ignore = "explicit actual /usr/bin/g++ include traces (the captured g++ version, ICI_TEST_GXX)"]
fn legacy_compiler_include_graph_keeps_frozen_unit_outcomes_errors_and_cycles() {
    compare(true);
}

/// The same trees with whatever `/usr/bin/g++` the host has (the CI runner's): no compared
/// field names a compiler version.
#[test]
fn legacy_compiler_include_graph_matches_frozen_with_the_host_gxx() {
    compare(false);
}

fn compare(captured_version: bool) {
    let oracle: Value = serde_json::from_str(include_str!(
        "../../../tests/rust-parity/cpp/include-context-oracle.json"
    ))
    .unwrap();
    assert_eq!(oracle["oracle"], "6fe3e405f8e584234a56cb8f0d8c0bd2cb31a188");
    // A missing compiler fails the comparison; it is never a skipped pass.
    let gxx = Command::new("/usr/bin/g++")
        .arg("--version")
        .output()
        .expect("/usr/bin/g++");
    if captured_version {
        assert_eq!(
            String::from_utf8(gxx.stdout).unwrap().lines().next(),
            oracle["gxx"].as_str()
        );
    }
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../parity-fixtures/cpp/include-context")
        .canonicalize()
        .unwrap();
    let mut mismatches = vec![];
    let cases = oracle["cases"].as_object().unwrap();
    assert_eq!(cases.len(), 6);
    for (name, expected) in cases {
        let (fixture, standalone) = match name.strip_prefix("cycle:") {
            Some(fixture) => (fixture, true),
            None => (name.as_str(), false),
        };
        let expected = expected.clone();
        let work = tempfile::tempdir().unwrap();
        let root = work.path().canonicalize().unwrap().join("project");
        copy(&fixtures.join(fixture), &root);
        let home = work.path().join("home");
        fs::create_dir(&home).unwrap();
        let environment = BTreeMap::from([
            ("PATH".to_owned(), "/usr/bin:/bin".to_owned()),
            ("HOME".to_owned(), home.display().to_string()),
            ("LANG".to_owned(), "C.UTF-8".to_owned()),
            ("LC_ALL".to_owned(), "C.UTF-8".to_owned()),
        ]);
        let argv: &[&str] = if standalone {
            &["ici", "cycle", "--report"]
        } else {
            &[
                "ici",
                "verify",
                "--profile",
                "deep",
                "--no-cache",
                "--report",
            ]
        };
        let invocation = registry::parse(argv.iter().copied()).unwrap();
        let output = commands::dispatch(&invocation, &root, &environment).unwrap();
        let report = if standalone {
            "cycle_report.json"
        } else {
            "verify_report.json"
        };
        let text = fs::read_to_string(root.join(report))
            .unwrap()
            .replace(&root.display().to_string(), "<root>");
        let actual = engine(
            output.exit_code,
            &serde_json::from_str(&text).unwrap(),
            standalone,
            &expected["extra"],
        );
        if actual != expected {
            eprintln!("== {name}\nnative: {actual:#}\nfrozen: {expected:#}");
            mismatches.push(name.clone());
        }
    }
    assert!(mismatches.is_empty(), "{mismatches:?}");
}
